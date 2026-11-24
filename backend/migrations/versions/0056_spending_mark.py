"""Phase 10 wave 3 (docs/phases/phase-10.md §7.9 (f), D295 (2)): `spending_mark`.

One row per answer a person gives about the household's spending, never a computed figure:

- on a single payment (§7.9 (c), D297): `allocation_id` and `one_off` („Ja, einmalig“) or
  `regular` („Nein, normal“);
- on a recurring series (§7.9 (b), D296): its payee key (`series_key`, rules.matching.payee_key)
  and `series_interval`, and `hidden` („kein Abo“) or `shown` („Wieder anzeigen“).

The newest answer per target counts (the service's rule, read in `created_at, id` order); a
correction is a new row. The table is append-only (principle 4): the app role gets SELECT and
INSERT only, and triggers refuse UPDATE, DELETE and TRUNCATE. Forced row-level security on
`workspace_id`, and the three `mark_data_version` triggers 0050 gives every workspace table, so
an answer moves the workspace's data version and no kept answer stays stale.

`allocation_id` references the allocation (allocations are never deleted, 0007's triggers): a
re-booked payment gets a new allocation and is asked again. The series key is the payee's
words in upper case, so it lives on the row only; the event `household.mark_recorded` carries
the target allocation or the interval and the answer, never the key or any payee text.

`event.entity_type` gains `household` (entity_id = the spending_mark row). The name avoids
`levers.one_offs`, which already means a planned payment such as a refund.

The downgrade is refused while the table holds a row or an event names `household` (a
downgrade never drops data, as 0048); it lifts FORCE RLS for the checks, since the owner has
no workspace set, and restores it.

Revision ID: 0056_spending_mark
Revises: 0055_sign_out_keeps_data_version
Create Date: 2026-11-24
"""

from alembic import op

revision = "0056_spending_mark"
down_revision = "0055_sign_out_keeps_data_version"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"
TABLE = "spending_mark"

# 0048's list, in its order.
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
    "household_scenario",
    "health",
    "plan",
)
NEW_ENTITY_TYPES = ("household",)

DDL = r"""
CREATE TABLE spending_mark (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id     uuid NOT NULL REFERENCES workspace (id),
    allocation_id    uuid,
    series_key       text CHECK (char_length(series_key) BETWEEN 1 AND 500),
    series_interval  text CHECK (series_interval IN ('monthly', 'quarterly', 'yearly')),
    answer           text NOT NULL CHECK (answer IN ('one_off', 'regular', 'hidden', 'shown')),
    created_by       uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, allocation_id) REFERENCES allocation (workspace_id, id),
    FOREIGN KEY (workspace_id, created_by) REFERENCES app_user (workspace_id, id),
    -- A payment is answered one_off or regular; a series (key and interval) hidden or shown.
    CONSTRAINT spending_mark_target_check CHECK (
        (allocation_id IS NOT NULL AND series_key IS NULL AND series_interval IS NULL
            AND answer IN ('one_off', 'regular'))
        OR (allocation_id IS NULL AND series_key IS NOT NULL AND series_interval IS NOT NULL
            AND answer IN ('hidden', 'shown'))
    )
);
CREATE INDEX spending_mark_allocation
    ON spending_mark (workspace_id, allocation_id, created_at) WHERE allocation_id IS NOT NULL;
CREATE INDEX spending_mark_series
    ON spending_mark (workspace_id, series_key, series_interval, created_at)
    WHERE series_key IS NOT NULL;
COMMENT ON TABLE spending_mark IS
    'Phase 10 D295 (2), D296, D297: a person''s answer on a payment (one_off, regular) or a '
    'recurring series (hidden, shown); append-only, the newest answer per target counts.';

CREATE TRIGGER spending_mark_append_only BEFORE UPDATE OR DELETE ON spending_mark
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER spending_mark_no_truncate BEFORE TRUNCATE ON spending_mark
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

ALTER TABLE spending_mark ENABLE ROW LEVEL SECURITY;
ALTER TABLE spending_mark FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON spending_mark
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- As 0050 creates them on every workspace table.
CREATE TRIGGER mark_data_version_insert AFTER INSERT ON spending_mark FOR EACH ROW
    WHEN (data_version_queue_first(NEW.workspace_id))
    EXECUTE FUNCTION mark_workspace_data_version();
CREATE TRIGGER mark_data_version_update AFTER UPDATE ON spending_mark FOR EACH ROW
    WHEN (data_version_queue_first(OLD.workspace_id, NEW.workspace_id))
    EXECUTE FUNCTION mark_workspace_data_version();
CREATE TRIGGER mark_data_version_delete AFTER DELETE ON spending_mark FOR EACH ROW
    WHEN (data_version_queue_first(OLD.workspace_id))
    EXECUTE FUNCTION mark_workspace_data_version();
"""

GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT ON {TABLE} TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""


def _sql_list(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def _set_entity_types(names: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE event DROP CONSTRAINT event_entity_type_check")
    op.execute(
        "ALTER TABLE event ADD CONSTRAINT event_entity_type_check"
        f" CHECK (entity_type IN ({_sql_list(names)}))"
    )


REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE event NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM {TABLE}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: table {TABLE} holds rows'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    IF EXISTS (SELECT 1 FROM event WHERE entity_type IN ({_sql_list(NEW_ENTITY_TYPES)})) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: an event is about a spending mark'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;
    ALTER TABLE event FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(DDL)
    _set_entity_types((*OLD_ENTITY_TYPES, *NEW_ENTITY_TYPES))
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    _set_entity_types(OLD_ENTITY_TYPES)
    op.execute(f"DROP TABLE {TABLE};")
