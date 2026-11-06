"""Phase 2 step 1: the job queue, AI call provenance and pre-fill proposals.

Spec: docs/phases/phase-2.md §3.1 (D14, D18, D19, D20). Follows the 0001 conventions:
workspace_id on every table, UNIQUE (workspace_id, id) and composite FKs, text + CHECK for
closed value sets, forced workspace_isolation RLS, explicit hausbuch_app grants.

- Procrastinate's schema (tables, enums, functions, triggers) is applied from a vendored copy
  of the pinned package's schema.sql (migrations/sql/procrastinate_3.10.0_schema.sql; a test
  keeps it equal to the installed package's file), so this migration never changes when the
  package is upgraded: a later procrastinate version gets its own migration. Its tables are
  not RLS'd and not in tables.py (D20): they hold job ids and arguments, every job payload
  carries workspace_id, and a job opens its own workspace_transaction as the app role. None
  of its functions is SECURITY DEFINER, so EXECUTE on them reaches nothing the app role could
  not already touch.
- ai_call is immutable (D18) except the erasure scrub: output and raw_text may be set to
  NULL, nothing else may change (trigger ai_call_scrub_only). The app role's UPDATE privilege
  is limited to those two columns as well.
- proposal is mutable (status, decisions) with events. D19 nulls fields and decisions 90 days
  after the decision; scrubbed_at records that, so "decisions are written exactly on apply"
  stays checkable after the scrub (a column the spec table does not list, see the PR notes).
- event.entity_type gains every phase 2 entity type at once, so 0006 and 0007 leave it alone.

Revision ID: 0005_jobs_ai_proposals
Revises: 0004_grundsteuerbescheid
Create Date: 2026-11-06
"""

from pathlib import Path

from alembic import op

revision = "0005_jobs_ai_proposals"
down_revision = "0004_grundsteuerbescheid"
branch_labels = None
depends_on = None

PROCRASTINATE_VERSION = "3.10.0"
PROCRASTINATE_SQL = (
    Path(__file__).resolve().parent.parent
    / "sql"
    / f"procrastinate_{PROCRASTINATE_VERSION}_schema.sql"
)
# The objects the vendored schema creates (a test checks the list against the database).
PROCRASTINATE_TABLES = [
    "procrastinate_workers",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
    "procrastinate_events",
]
PROCRASTINATE_TYPES = [
    "procrastinate_job_to_defer_v1",
    "procrastinate_job_event_type",
    "procrastinate_job_status",
]

# Parents before children. Reversed for downgrade.
TABLES = ["ai_call", "proposal"]

APP_ROLE = "hausbuch_app"
APP_GRANTS = {
    # No table-wide UPDATE: only the scrub columns (below). DELETE for retention.
    "ai_call": "SELECT, INSERT, DELETE",
    "proposal": "SELECT, INSERT, UPDATE",
}
APP_COLUMN_GRANTS = {"ai_call": "UPDATE (output, raw_text)"}

EXTRACTION_KINDS = (
    "'manual', 'purchase_contract', 'loan_contract', 'lease', 'handover_protocol', "
    "'deposit_record', 'invoice', 'bescheid', 'grundsteuerbescheid', 'wirtschaftsplan', "
    "'hausgeldabrechnung', 'zinsbescheinigung', 'statement'"
)
PROPOSAL_KINDS = EXTRACTION_KINDS.replace("'manual', ", "")

OLD_ENTITY_TYPES = "'owner', 'property', 'unit', 'tenant', 'tenancy', 'loan', 'document'"
NEW_ENTITY_TYPES = (
    OLD_ENTITY_TYPES
    + ", 'user', 'proposal', 'account', 'statement', 'transaction', 'rule', 'workspace'"
)

FUNCTIONS = """
CREATE FUNCTION ai_call_scrub_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The erasure scrub nulls output and raw_text in place; every other column, and any
    -- non-null new value, is a mutation of an immutable row.
    IF (to_jsonb(NEW) - 'output' - 'raw_text') IS DISTINCT FROM
           (to_jsonb(OLD) - 'output' - 'raw_text')
       OR (NEW.output IS NOT NULL AND NEW.output IS DISTINCT FROM OLD.output)
       OR (NEW.raw_text IS NOT NULL AND NEW.raw_text IS DISTINCT FROM OLD.raw_text) THEN
        RAISE EXCEPTION 'UPDATE on ai_call is not allowed: rows are append-only'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Only the erasure scrub may set output and raw_text to NULL.';
    END IF;
    RETURN NEW;
END
$$;
"""

DDL = rf"""
ALTER TABLE workspace
    ADD COLUMN ai_tenant_documents boolean NOT NULL DEFAULT false,
    ADD COLUMN auto_accept_llm boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN workspace.ai_tenant_documents IS
    'D16: tenant documents may be sent to the model. Changed with workspace.settings_changed.';
COMMENT ON COLUMN workspace.auto_accept_llm IS
    'D26: LLM categorizations may auto-confirm above their threshold. Off for the first month.';

CREATE TABLE ai_call (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    job                text NOT NULL CHECK (job IN ('classify', 'extract', 'match', 'explain')),
    document_id        uuid,
    transaction_id     uuid,  -- FK added by 0007_allocations_rules
    model              text NOT NULL CHECK (length(model) > 0),
    prompt_version     text NOT NULL CHECK (length(prompt_version) > 0),
    inference_geo      text,
    input_sha256       bytea NOT NULL CHECK (octet_length(input_sha256) = 32),
    input_tokens       integer CHECK (input_tokens >= 0),
    output_tokens      integer CHECK (output_tokens >= 0),
    cache_read_tokens  integer CHECK (cache_read_tokens >= 0),
    stop_reason        text,
    output             jsonb,
    raw_text           text,
    error_code         text,
    duration_ms        integer CHECK (duration_ms >= 0),
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id)
        ON DELETE CASCADE,
    CHECK (job NOT IN ('classify', 'extract') OR document_id IS NOT NULL)
);
CREATE INDEX ai_call_document_idx ON ai_call (workspace_id, document_id)
    WHERE document_id IS NOT NULL;
COMMENT ON TABLE ai_call IS
    'AI provenance (D18): one row per model request. Immutable; output and raw_text can quote '
    'tenant data and are nulled by tenant erasure. Rows follow their document''s retention.';
COMMENT ON COLUMN ai_call.input_sha256 IS
    'SHA-256 of the request body without the API key: replays and evals compare it.';

ALTER TABLE document_extraction
    ADD CONSTRAINT document_extraction_workspace_id_id_key UNIQUE (workspace_id, id),
    ADD COLUMN kind text NOT NULL DEFAULT 'manual' CHECK (kind IN ({EXTRACTION_KINDS})),
    ADD COLUMN ai_call_id uuid,
    ADD CONSTRAINT document_extraction_ai_call_fk
        FOREIGN KEY (workspace_id, ai_call_id) REFERENCES ai_call (workspace_id, id),
    ADD CONSTRAINT document_extraction_ai_provenance_check
        CHECK ((source = 'ai') = (ai_call_id IS NOT NULL));

CREATE TABLE proposal (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspace (id),
    document_id       uuid NOT NULL,
    kind              text CHECK (kind IN ({PROPOSAL_KINDS})),  -- null until classified
    status            text NOT NULL DEFAULT 'queued' CHECK (status IN (
                          'queued', 'running', 'ready', 'applied', 'rejected', 'superseded',
                          'failed', 'blocked_tenant_data')),
    error_code        text,
    extraction_id     uuid,
    ai_call_id        uuid,
    target_mode       text NOT NULL DEFAULT 'create' CHECK (target_mode IN ('create', 'patch')),
    -- D16 gate 2: the uploader marked the document as tenant data. Set at creation, copied by
    -- a re-run; the job reads it from here, never from its own arguments.
    contains_tenant_data boolean NOT NULL DEFAULT false,
    targets           jsonb CHECK (targets IS NULL OR jsonb_typeof(targets) = 'array'),
    fields            jsonb CHECK (fields IS NULL OR jsonb_typeof(fields) = 'array'),
    decisions         jsonb CHECK (decisions IS NULL OR jsonb_typeof(decisions) = 'object'),
    applied_entities  jsonb CHECK (applied_entities IS NULL
                                   OR jsonb_typeof(applied_entities) = 'array'),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    decided_at        timestamptz,
    decided_by        uuid,
    scrubbed_at       timestamptz,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, document_id) REFERENCES document (workspace_id, id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, extraction_id) REFERENCES document_extraction (workspace_id, id),
    FOREIGN KEY (workspace_id, ai_call_id) REFERENCES ai_call (workspace_id, id),
    FOREIGN KEY (workspace_id, decided_by) REFERENCES app_user (workspace_id, id),
    -- Decided exactly when applied or rejected; decisions only on apply (spec §3.4), and an
    -- applied proposal keeps them until the D19 scrub.
    CHECK ((decided_at IS NOT NULL) = (status IN ('applied', 'rejected'))),
    CHECK (decided_by IS NULL OR decided_at IS NOT NULL),
    CHECK (decisions IS NULL OR status = 'applied'),
    CHECK (status <> 'applied' OR decisions IS NOT NULL OR scrubbed_at IS NOT NULL),
    CHECK ((applied_entities IS NOT NULL) = (status = 'applied')),
    CHECK (scrubbed_at IS NULL OR (fields IS NULL AND decisions IS NULL)),
    CHECK (target_mode = 'patch' OR targets IS NULL),
    CHECK (error_code IS NULL OR status IN ('failed', 'blocked_tenant_data')),
    CHECK (status <> 'failed' OR error_code IS NOT NULL)
);
CREATE INDEX proposal_document_idx ON proposal (workspace_id, document_id);
COMMENT ON TABLE proposal IS
    'Pre-fill proposals (D14): what a document would create or patch. Nothing reaches a domain '
    'table until apply. fields and decisions are personal data at rest (D19).';
COMMENT ON COLUMN proposal.fields IS
    'The proposal records and their values (schemas.proposal.ProposalRecord[]).';
COMMENT ON COLUMN proposal.decisions IS
    'Per record and field, the FieldDecision of the apply; written once by apply.';

ALTER TABLE event DROP CONSTRAINT event_entity_type_check;
ALTER TABLE event ADD CONSTRAINT event_entity_type_check
    CHECK (entity_type IN ({NEW_ENTITY_TYPES}));

CREATE TRIGGER ai_call_immutable BEFORE UPDATE ON ai_call
    FOR EACH ROW EXECUTE FUNCTION ai_call_scrub_only();
"""


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
"""


def _if_app_role(body: str) -> str:
    return f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        {body}
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""


# Procrastinate's own tables, sequences and functions, found by prefix in this schema.
_PROCRASTINATE_GRANTS = f"""
DECLARE
            obj record;
        BEGIN
            FOR obj IN SELECT c.relname, c.relkind FROM pg_class c
                       WHERE c.relnamespace = current_schema()::regnamespace
                         AND c.relname LIKE 'procrastinate\\_%' AND c.relkind IN ('r', 'S')
            LOOP
                IF obj.relkind = 'r' THEN
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO {APP_ROLE}',
                                   obj.relname);
                ELSE
                    EXECUTE format('GRANT USAGE ON SEQUENCE %I TO {APP_ROLE}', obj.relname);
                END IF;
            END LOOP;
            FOR obj IN SELECT p.oid::regprocedure AS signature FROM pg_proc p
                       WHERE p.pronamespace = current_schema()::regnamespace
                         AND p.proname LIKE 'procrastinate\\_%'
            LOOP
                EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO {APP_ROLE}', obj.signature);
            END LOOP;
        END;
"""


def _procrastinate_grants() -> str:
    # A nested block: DO bodies cannot DECLARE inside IF directly.
    return _if_app_role(_PROCRASTINATE_GRANTS.strip())


def _drop_procrastinate_functions(trigger_op: str) -> str:
    return rf"""
DO $$
DECLARE
    fn regprocedure;
BEGIN
    FOR fn IN SELECT p.oid::regprocedure FROM pg_proc p
              WHERE p.pronamespace = current_schema()::regnamespace
                AND p.proname LIKE 'procrastinate\_%'
                AND p.prorettype {trigger_op} 'trigger'::regtype
    LOOP
        EXECUTE format('DROP FUNCTION %s', fn);
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    assert set(APP_GRANTS) == set(TABLES)
    op.execute(PROCRASTINATE_SQL.read_text(encoding="utf-8"))
    op.execute(FUNCTIONS)
    op.execute(DDL)
    for table in TABLES:
        op.execute(_rls(table))
    lines = [f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_GRANTS.items()]
    lines += [
        f"GRANT {privs} ON {table} TO {APP_ROLE};" for table, privs in APP_COLUMN_GRANTS.items()
    ]
    op.execute(_if_app_role("\n        ".join(lines)))
    op.execute(_procrastinate_grants())


def downgrade() -> None:
    # Fails while any event uses a phase 2 entity type: those events are the record of phase
    # 2 data, which a downgrade would orphan.
    op.execute("ALTER TABLE event DROP CONSTRAINT event_entity_type_check")
    op.execute(
        "ALTER TABLE event ADD CONSTRAINT event_entity_type_check "
        f"CHECK (entity_type IN ({OLD_ENTITY_TYPES}))"
    )
    op.execute("DROP TABLE proposal")
    op.execute("""
ALTER TABLE document_extraction
    DROP CONSTRAINT document_extraction_ai_provenance_check,
    DROP CONSTRAINT document_extraction_ai_call_fk,
    DROP COLUMN ai_call_id,
    DROP COLUMN kind,
    DROP CONSTRAINT document_extraction_workspace_id_id_key
""")
    op.execute("DROP TABLE ai_call")
    op.execute("DROP FUNCTION ai_call_scrub_only()")
    op.execute("ALTER TABLE workspace DROP COLUMN auto_accept_llm, DROP COLUMN ai_tenant_documents")
    # Functions that use a table's row type go before the tables; trigger functions after
    # them (their triggers go with the tables); the types last.
    op.execute(_drop_procrastinate_functions("<>"))
    for table in reversed(PROCRASTINATE_TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute(_drop_procrastinate_functions("="))
    for type_ in PROCRASTINATE_TYPES:
        op.execute(f"DROP TYPE {type_}")
