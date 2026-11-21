"""Phase 10 §7.4: a version per workspace that every committed write moves.

Übersicht and Zu prüfen recompute every figure on each load (about 8 s on the live
container). §7.4 keeps a computed answer in the API's memory and serves it again while
nothing of the workspace has changed. This migration gives "nothing has changed" a number:

- `workspace_data_version`: one row per workspace, `version` a bigint that only grows. The
  existing workspaces are seeded at 1; a new workspace gets its row at its first commit.
- Marking, at the write: three plain `AFTER INSERT`, `AFTER UPDATE` and `AFTER DELETE` row
  triggers on every table that holds `workspace_id` (WORKSPACE_TABLES below), and INSERT and
  UPDATE on `workspace` itself (its settings columns: an UPDATE of them changes answers too; a
  workspace is never deleted). Each trigger's `WHEN` calls
  `data_version_queue_first(<the row's workspace>)`, which is true only for the first row a
  transaction writes per workspace; that row's trigger (`mark_workspace_data_version()`)
  inserts one row into `workspace_data_version_pending`. The workspaces already marked are
  listed in the transaction-local setting `app.data_version_queued` (one setting holding a
  list: a custom setting's name cannot carry a workspace id). The row's own workspace
  decides, never `app.workspace_id`: a superuser writing rows of two workspaces in one
  transaction marks both. A setting made inside a savepoint is undone with it, exactly like
  the pending row inserted there, so a write after a rolled-back savepoint marks again. A
  transaction that writes 500 lines of one workspace marks once. Three triggers per table
  because a `WHEN` may name NEW only on INSERT and OLD only on DELETE.
- Bumping, at the commit: the one deferred constraint trigger, `DEFERRABLE INITIALLY
  DEFERRED`, sits on `workspace_data_version_pending` (`bump_workspace_data_version()`).
  Deferred, the bump runs at COMMIT, so its row lock is taken last and held only through the
  commit: two writers of one workspace commit their bumps one after the other, the versions
  follow the commit order, and the data and the new version become visible to readers in the
  same instant (one commit). The bump is an upsert (`INSERT … ON CONFLICT DO UPDATE SET
  version = version + 1`) that raises when it touched no row, then deletes its pending row
  and raises unless exactly that one row went: a pending row never outlives its transaction.
- Why the helper table: Postgres refuses TRUNCATE and ALTER TABLE on a table that holds
  pending deferred trigger events ("cannot TRUNCATE … because it has pending trigger
  events"), before any BEFORE TRUNCATE trigger runs. A deferred trigger on the workspace
  tables themselves held every table a transaction wrote against them until COMMIT; it broke
  the append-only TRUNCATE guards of phases 2, 4 and 4b (their tests write a row and then
  truncate that table in one transaction), and a data migration that writes rows and then
  alters the same table would have failed. The deferred events now live on the helper only.

SECURITY INVOKER, not DEFINER (the reasoning, §7.4 asked for it):
- The mark and the bump run as the writing role, so the forced RLS policy of both new tables
  (the standard `workspace_isolation`) decides, as for every other table: the app role and the
  owner role (provisioning, the CLI) may mark and bump only the workspace their transaction
  set in `app.workspace_id`, which is already the only workspace whose rows they can write; a
  mismatch or an unset workspace is refused by the policy and raises at the write (the
  pending row) or at the commit (the version), never skipped.
- A DEFINER function would run as the owner, who is also under FORCE RLS here, so it would
  gain nothing for those roles but bypass the tables' GRANTs; and it would break a superuser
  writing rows of two workspaces in one transaction (the test suites' seeding; superusers
  bypass RLS, so under INVOKER they mark and bump each row's own workspace correctly).
- The price of INVOKER: the app role needs SELECT, INSERT and UPDATE on the version table
  (no DELETE) and SELECT, INSERT and DELETE on the pending table. A role could then only move
  its own workspace's version; a BEFORE UPDATE trigger (`workspace_data_version_only_grows`)
  refuses any update that does not raise the version or that changes the workspace, so a
  version can never go back to a value an answer was kept under.

A future migration that writes workspace rows as the owner with `NO FORCE ROW LEVEL SECURITY`
on that table and no `app.workspace_id` set will now fail at the write (both new tables stay
forced): lift FORCE on them too for such a data migration, or set the workspace. That is the
intended loud failure, never a skipped bump.

Not covered, on purpose: `auth_session` (every request slides its session; a bump per
request would discard every kept answer) and `auth_passkey` (a sign-in updates its counter);
no answer kept by §7.4 reads either table (api/kept_answers.py). TRUNCATE fires no row
trigger; the app never truncates a workspace table. The Procrastinate tables have no
`workspace_id` and no trigger; no kept answer reads them.

The migration refuses to run when a table of the schema holds `workspace_id` and is neither
in WORKSPACE_TABLES nor excluded (a table added since would be left without a bump), and
tests/db/test_data_version.py fails for any table added later without its three triggers.

Revision ID: 0050_workspace_data_version
Revises: 0049_exchange_close_price
Create Date: 2026-11-21
"""

from alembic import op

revision = "0050_workspace_data_version"
down_revision = "0049_exchange_close_price"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"
TRIGGER = "bump_data_version"  # the deferred one on the pending table
MARK = "mark_data_version"  # MARK_insert, MARK_update, MARK_delete on each workspace table
# Tables holding workspace_id that get no trigger (the module docstring says why).
EXCLUDED = ("auth_session", "auth_passkey")
# Every other table holding workspace_id on 21 Nov 2026 (60 tables).
WORKSPACE_TABLES = (
    "account",
    "account_ownership",
    "ai_call",
    "allocation",
    "app_secret",
    "app_user",
    "asset",
    "asset_movement",
    "auth_invite",
    "bank_connection",
    "bank_credential",
    "bank_statement",
    "bank_sync",
    "chain_decoder",
    "chain_pair",
    "document",
    "document_extraction",
    "document_file",
    "document_link",
    "event",
    "exchange_deposit_address",
    "exchange_withdrawal_pair",
    "health_snapshot",
    "holding_statement",
    "holdings_export",
    "household_scenario",
    "inbound_mail",
    "inbound_mail_attachment",
    "income_input",
    "loan",
    "matching_rule",
    "movement_group",
    "movement_import",
    "notification",
    "owner",
    "plan_explanation",
    "possible_duplicate",
    "price",
    "projection_scenario",
    "property",
    "property_acquisition_cost",
    "property_ownership",
    "property_price_split",
    "proposal",
    "questionnaire_answer",
    "reminder_ack",
    "rent_schedule",
    "return_input",
    "statement_import",
    "tax_export",
    "tax_input",
    "tenancy",
    "tenancy_tenant",
    "tenant",
    "transaction",
    "transfer_link",
    "unit",
    "valuation_snapshot",
    "wallet_address",
    "wallet_sync",
)


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{name}'" for name in names)


# The catalog must hold exactly these tables with workspace_id (EXCLUDED aside), so no table
# is left without its bump.
CHECK_COVERAGE = f"""
DO $$
DECLARE
    missing text;
    unknown text;
BEGIN
    SELECT string_agg(c.relname, ', ' ORDER BY c.relname) INTO missing
    FROM pg_class c
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'workspace_id'
                       AND NOT a.attisdropped
    WHERE c.relnamespace = current_schema()::regnamespace
      AND c.relkind IN ('r', 'p')
      AND c.relname NOT IN ({_sql_list(WORKSPACE_TABLES + EXCLUDED)});
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '{revision}: tables with workspace_id not listed: %', missing;
    END IF;
    SELECT string_agg(t, ', ' ORDER BY t) INTO unknown
    FROM unnest(ARRAY[{_sql_list(WORKSPACE_TABLES)}]) AS t
    WHERE to_regclass(t) IS NULL;
    IF unknown IS NOT NULL THEN
        RAISE EXCEPTION '{revision}: listed tables that do not exist: %', unknown;
    END IF;
END
$$;
"""


DDL = r"""
CREATE TABLE workspace_data_version (
    workspace_id  uuid PRIMARY KEY REFERENCES workspace (id) ON DELETE CASCADE,
    version       bigint NOT NULL CHECK (version >= 1)
);
COMMENT ON TABLE workspace_data_version IS
    'Phase 10 §7.4: moves at the commit of every write to the workspace, once per '
    'transaction; the API keeps a computed answer only under the version it was computed '
    'at. Only grows.';

CREATE TABLE workspace_data_version_pending (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL
);
COMMENT ON TABLE workspace_data_version_pending IS
    'Phase 10 §7.4: one row per transaction and workspace written, inserted at the first '
    'write and deleted by the version bump at COMMIT; always empty outside a transaction. '
    'It carries the one deferred trigger, so the written tables stay free for TRUNCATE and '
    'ALTER TABLE.';
"""

# The owner has no workspace set here: FORCE is lifted on `workspace` for the read and the
# new table is filled before its own RLS is enabled.
SEED = r"""
ALTER TABLE workspace NO FORCE ROW LEVEL SECURITY;
INSERT INTO workspace_data_version (workspace_id, version) SELECT id, 1 FROM workspace;
ALTER TABLE workspace FORCE ROW LEVEL SECURITY;
"""

RLS = r"""
ALTER TABLE workspace_data_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_data_version FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON workspace_data_version
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

ALTER TABLE workspace_data_version_pending ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_data_version_pending FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON workspace_data_version_pending
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

CREATE FUNCTION workspace_data_version_only_grows() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.workspace_id <> OLD.workspace_id OR NEW.version <= OLD.version THEN
        RAISE EXCEPTION 'workspace_data_version only grows'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER workspace_data_version_only_grows
    BEFORE UPDATE ON workspace_data_version
    FOR EACH ROW EXECUTE FUNCTION workspace_data_version_only_grows();
"""

FUNCTIONS = r"""
-- True when one of `ids` is not yet marked in this transaction (and records it), or when one
-- is NULL (so the mark runs and raises). Called by the WHEN of every mark trigger.
CREATE FUNCTION data_version_queue_first(VARIADIC ids uuid[]) RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    queued text := coalesce(current_setting('app.data_version_queued', true), '');
    w uuid;
    first boolean := false;
BEGIN
    FOREACH w IN ARRAY ids LOOP
        IF w IS NULL THEN
            RETURN true;
        END IF;
        CONTINUE WHEN strpos(queued, w::text) > 0;
        queued := queued || w::text || ',';
        first := true;
    END LOOP;
    IF first THEN
        PERFORM set_config('app.data_version_queued', queued, true);
    END IF;
    RETURN first;
END
$$;

-- The row's workspace(s) into the pending table. On an UPDATE that moves a row between
-- workspaces both get a row, even when one was marked before (a second bump is harmless; a
-- missed one is not).
CREATE FUNCTION mark_workspace_data_version() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    old_ws uuid;
    new_ws uuid;
BEGIN
    IF TG_TABLE_NAME = 'workspace' THEN
        IF TG_OP <> 'INSERT' THEN old_ws := OLD.id; END IF;
        IF TG_OP <> 'DELETE' THEN new_ws := NEW.id; END IF;
    ELSE
        IF TG_OP <> 'INSERT' THEN old_ws := OLD.workspace_id; END IF;
        IF TG_OP <> 'DELETE' THEN new_ws := NEW.workspace_id; END IF;
    END IF;
    IF (TG_OP <> 'INSERT' AND old_ws IS NULL) OR (TG_OP <> 'DELETE' AND new_ws IS NULL) THEN
        RAISE EXCEPTION 'mark_workspace_data_version: % row without a workspace on %',
            TG_OP, TG_TABLE_NAME;
    END IF;
    IF old_ws IS NOT NULL THEN
        INSERT INTO workspace_data_version_pending (workspace_id) VALUES (old_ws);
    END IF;
    IF new_ws IS NOT NULL AND new_ws IS DISTINCT FROM old_ws THEN
        INSERT INTO workspace_data_version_pending (workspace_id) VALUES (new_ws);
    END IF;
    RETURN NULL;
END
$$;

-- At COMMIT, once per pending row: the version moves by one, the pending row goes. Both
-- must touch exactly one row, or the commit fails.
CREATE FUNCTION bump_workspace_data_version() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    touched bigint;
BEGIN
    INSERT INTO workspace_data_version AS v (workspace_id, version)
    VALUES (NEW.workspace_id, 1)
    ON CONFLICT (workspace_id) DO UPDATE SET version = v.version + 1;
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> 1 THEN
        RAISE EXCEPTION 'bump_workspace_data_version: workspace % not bumped (% rows)',
            NEW.workspace_id, touched;
    END IF;
    DELETE FROM workspace_data_version_pending WHERE id = NEW.id;
    GET DIAGNOSTICS touched = ROW_COUNT;
    IF touched <> 1 THEN
        RAISE EXCEPTION 'bump_workspace_data_version: pending row of workspace % not removed'
            ' (% rows)', NEW.workspace_id, touched;
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER bump_data_version
    AFTER INSERT ON workspace_data_version_pending
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION bump_workspace_data_version();
"""

# (trigger suffix, event, the WHEN's arguments with {col} the workspace column)
EVENTS = (
    ("insert", "INSERT", "NEW.{col}"),
    ("update", "UPDATE", "OLD.{col}, NEW.{col}"),
    ("delete", "DELETE", "OLD.{col}"),
)


def _marks(table: str) -> list[str]:
    col = "id" if table == "workspace" else "workspace_id"
    statements = []
    for suffix, event, args in EVENTS:
        if table == "workspace" and event == "DELETE":
            continue  # a workspace is never deleted (its rows cascade with it)
        statements.append(
            f"CREATE TRIGGER {MARK}_{suffix} AFTER {event} ON {table} FOR EACH ROW"
            f" WHEN (data_version_queue_first({args.format(col=col)}))"
            " EXECUTE FUNCTION mark_workspace_data_version();"
        )
    return statements


GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON workspace_data_version TO {APP_ROLE};
        GRANT SELECT, INSERT, DELETE ON workspace_data_version_pending TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(CHECK_COVERAGE)
    op.execute(DDL)
    op.execute(SEED)
    op.execute(RLS)
    op.execute(FUNCTIONS)
    for table in (*WORKSPACE_TABLES, "workspace"):
        for statement in _marks(table):
            op.execute(statement)
    op.execute(GRANTS)


def downgrade() -> None:
    # Nothing is lost: the version is derived bookkeeping, rebuilt at 1 by the next upgrade.
    for table in (*WORKSPACE_TABLES, "workspace"):
        for suffix, _, _ in EVENTS:
            if table == "workspace" and suffix == "delete":
                continue
            op.execute(f"DROP TRIGGER {MARK}_{suffix} ON {table};")
    op.execute("DROP TABLE workspace_data_version_pending;")
    op.execute("DROP TABLE workspace_data_version;")
    op.execute("DROP FUNCTION bump_workspace_data_version();")
    op.execute("DROP FUNCTION mark_workspace_data_version();")
    op.execute("DROP FUNCTION data_version_queue_first(uuid[]);")
    op.execute("DROP FUNCTION workspace_data_version_only_grows();")
