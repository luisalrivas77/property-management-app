"""Phase 9 step 0: the household's key figures and its plan (docs/phases/phase-9.md §11).

- `workspace.advice_mode`: `own_use` (default) or `generic` (D251).
- `household_scenario`: the household plan's levers, append-only like phase 4's
  `projection_scenario` (0016) without `property_id` (D248). As there, "at most one default
  among the rows neither withdrawn nor superseded" is the service's rule, not a constraint: a
  partial unique index cannot see supersession. §11 names the actor and time columns
  `created_by`, `created_at` (0016 says `recorded_*`); this follows §11.
- `health_snapshot`: a closed month's key figures, stored once (D242). `month_end` is a
  month's last day; `net_worth_run_id` names the `valuation_snapshot` run the stock figures
  read, a grouping uuid with no table of its own, so it carries no foreign key.
- `plan_explanation`: one row per explanation request, model or template (D257).
- `event.entity_type` gains `household_scenario`, `health` and `plan`, the prefixes of §11.6's
  events (each event's type starts with its entity type); the event types themselves are
  added by the steps that write them.

All three tables are append-only (principle 4): the app role gets SELECT and INSERT only, and
triggers refuse UPDATE, DELETE and TRUNCATE. The downgrade is refused while any of them holds
a row, an event names one of the new entity types, or a workspace is set to `generic` (that
setting would be lost silently; 0016's rule that a downgrade never drops data); it lifts FORCE
RLS for the checks, since the owner has no workspace set, and restores it.

The number: 0049 in the spec until 17 Nov 2026, when Luis deferred phase 8 (which had 0048)
and started phase 9's step 0 alone.

Revision ID: 0048_household_plan
Revises: 0047_elster_hersteller_id
Create Date: 2026-11-17
"""

from alembic import op

revision = "0048_household_plan"
down_revision = "0047_elster_hersteller_id"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"
TABLES = ("household_scenario", "health_snapshot", "plan_explanation")

OLD_ENTITY_TYPES = (
    "owner",
    "property",
    "unit",
    "tenant",
    "tenancy",
    "loan",
    "document",
    "user",
    "proposal",
    "account",
    "statement",
    "transaction",
    "rule",
    "workspace",
    "asset",
    "movement",
    "transfer",
    "wallet",
    "wallet_address",
    "movement_group",
)
NEW_ENTITY_TYPES = ("household_scenario", "health", "plan")

DDL = r"""
ALTER TABLE workspace ADD COLUMN advice_mode text NOT NULL DEFAULT 'own_use'
    CONSTRAINT workspace_advice_mode_check CHECK (advice_mode IN ('own_use', 'generic'));

CREATE TABLE household_scenario (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    uuid NOT NULL REFERENCES workspace (id),
    name            text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    is_default      boolean NOT NULL DEFAULT false,
    levers          jsonb NOT NULL CHECK (jsonb_typeof(levers) = 'object'),
    supersedes_id   uuid UNIQUE,
    withdrawn       boolean NOT NULL DEFAULT false,
    created_by      uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id),
    -- A correction supersedes a scenario of the same workspace.
    FOREIGN KEY (workspace_id, supersedes_id) REFERENCES household_scenario (workspace_id, id),
    CHECK (supersedes_id IS NULL OR supersedes_id <> id),
    CHECK (NOT withdrawn OR supersedes_id IS NOT NULL)
);
CREATE INDEX household_scenario_workspace ON household_scenario (workspace_id, created_at);
COMMENT ON TABLE household_scenario IS
    'Phase 9 D248: the household plan''s levers, append-only; a change is a new row. One '
    'default in force is the service''s rule (as projection_scenario).';

CREATE TABLE health_snapshot (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id      uuid NOT NULL REFERENCES workspace (id),
    month_end         date NOT NULL
        CHECK (month_end = (date_trunc('month', month_end) + interval '1 month - 1 day')::date),
    net_worth_run_id  uuid NOT NULL,
    figures           jsonb NOT NULL CHECK (jsonb_typeof(figures) = 'object'),
    rule_version      text NOT NULL CHECK (char_length(rule_version) BETWEEN 1 AND 128),
    created_by        uuid,  -- null for the daily task
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id)
);
CREATE INDEX health_snapshot_month
    ON health_snapshot (workspace_id, month_end, created_at DESC);
COMMENT ON TABLE health_snapshot IS
    'Phase 9 D242: a closed month''s key figures, stored once and never changed; the newest '
    'row per month-end is the one shown. No mark is stored.';

CREATE TABLE plan_explanation (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id        uuid NOT NULL REFERENCES workspace (id),
    scenario_id         uuid,  -- null: the defaults
    advice_mode         text NOT NULL CHECK (advice_mode IN ('own_use', 'generic')),
    status              text NOT NULL CHECK (status IN ('model', 'template')),
    reason              text CHECK (reason IN ('rejected', 'refused', 'consent_off', 'limit',
                                               'error')),
    facts               jsonb NOT NULL CHECK (jsonb_typeof(facts) = 'object'),
    labels              jsonb NOT NULL DEFAULT '{}'::jsonb
                            CHECK (jsonb_typeof(labels) = 'object'),
    facts_sha256        bytea NOT NULL CHECK (octet_length(facts_sha256) = 32),
    paragraphs          jsonb NOT NULL CHECK (jsonb_typeof(paragraphs) = 'array'),
    violations          jsonb NOT NULL DEFAULT '[]'::jsonb
                            CHECK (jsonb_typeof(violations) = 'array'),
    ai_call_ids         uuid[] NOT NULL DEFAULT '{}',
    prompt_version      text,  -- null for a template text no model was asked for
    validator_version   text NOT NULL,
    lexicon_version     text NOT NULL,
    vocabulary_version  text NOT NULL,
    created_by          uuid,  -- null for the daily task
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, scenario_id) REFERENCES household_scenario (workspace_id, id),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id),
    -- A template text says why; a model text has no reason (D257).
    CHECK ((status = 'template') = (reason IS NOT NULL))
);
CREATE INDEX plan_explanation_newest
    ON plan_explanation (workspace_id, advice_mode, scenario_id, created_at DESC);
COMMENT ON TABLE plan_explanation IS
    'Phase 9 D257: a validated model text or the template text, with its own facts; labels '
    'for rendering only, never sent; violations hold codes and positions, never text.';
"""


def _triggers() -> str:
    return "\n".join(
        f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table}
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table}
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();"""
        for table in TABLES
    )


def _rls(table: str) -> str:
    return f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON {table}
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());"""


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_entity_types(names: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE event DROP CONSTRAINT event_entity_type_check")
    op.execute(
        "ALTER TABLE event ADD CONSTRAINT event_entity_type_check"
        f" CHECK (entity_type IN ({_sql_list(names)}))"
    )


GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT ON household_scenario, health_snapshot, plan_explanation
            TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""

_DATA_CHECKS = (
    *((table, f"SELECT 1 FROM {table}", f"table {table} holds rows") for table in TABLES),
    (
        "event",
        f"SELECT 1 FROM event WHERE entity_type IN ({_sql_list(NEW_ENTITY_TYPES)})",
        "an event is about a household scenario, a health snapshot or an explanation",
    ),
    (
        "workspace",
        "SELECT 1 FROM workspace WHERE advice_mode <> 'own_use'",
        "a workspace is set to the generic advice mode",
    ),
)
_CHECKED = tuple(dict.fromkeys(t for t, _, _ in _DATA_CHECKS))
REFUSE_IF_DATA = (
    "DO $$\nBEGIN"
    + "".join(f"\n    ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;" for table in _CHECKED)
    + "".join(
        f"""
    IF EXISTS ({query}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: {what}'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;"""
        for _, query, what in _DATA_CHECKS
    )
    + "".join(f"\n    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" for table in _CHECKED)
    + "\nEND\n$$;"
)


def upgrade() -> None:
    op.execute(DDL)
    op.execute(_triggers())
    for table in TABLES:
        op.execute(_rls(table))
    _set_entity_types((*OLD_ENTITY_TYPES, *NEW_ENTITY_TYPES))
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_entity_types(OLD_ENTITY_TYPES)
    op.execute("DROP TABLE plan_explanation;")
    op.execute("DROP TABLE health_snapshot;")
    op.execute("DROP TABLE household_scenario;")
    op.execute("ALTER TABLE workspace DROP COLUMN advice_mode;")
