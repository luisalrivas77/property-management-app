"""Phase 10 §7.11 (c) 3, „Route 3 as decided for the build“ point 8 (D310 amended):
`reference_return`, the funds series route 3 compares the needed return with.

Kenneth French's „Developed“ market return may not be committed (a copyright line, no licence,
Bloomberg data), so the app downloads his annual file into this table, per workspace, for own
use only. One row per series and year of one download, as published: the market's excess
return (Mkt-RF) and the risk-free rate (RF), in percent, in US dollars. The conversion to a
return in euros is done on read by rules/goal (eigene Berechnung) and never stored.

Each row names its source, address, series, the day it was read, the licence and the
attribution, and carries `repo_allowed` and `commercial_use`, both false and held false by a
CHECK. All rows of one download share `download_id` and `fetched_at` (one transaction); the
newest download per series counts (services.reference_returns). Every successful download is
inserted, also when it equals the previous one, so its `fetched_at` tells the daily task when
it last succeeded.

Insert-only (principle 4): the app role gets SELECT and INSERT, and triggers refuse UPDATE,
DELETE and TRUNCATE. Forced row-level security on `workspace_id`, and the three
`mark_data_version` triggers 0050 gives every workspace table, so a download moves the
workspace's data version and the goal's kept answer is computed again (§7.4).

No event entity type: a download is reference data, as a fetched price is, and writes no event.

The downgrade is refused while the table holds a row (a downgrade never drops data, as 0048);
it lifts FORCE RLS for the check, since the owner has no workspace set, and restores it.

Revision ID: 0057_reference_return
Revises: 0056_spending_mark
Create Date: 2026-11-25
"""

from alembic import op

revision = "0057_reference_return"
down_revision = "0056_spending_mark"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"
TABLE = "reference_return"

DDL = r"""
CREATE TABLE reference_return (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL REFERENCES workspace (id),
    download_id        uuid NOT NULL,
    series             text NOT NULL CHECK (series IN ('french_developed')),
    year               smallint NOT NULL CHECK (year BETWEEN 1900 AND 2200),
    -- As published, in percent: a total return is never below -100 %.
    market_excess_pct  numeric(9, 4) NOT NULL
        CHECK (market_excess_pct > -200 AND market_excess_pct < 1000),
    risk_free_pct      numeric(9, 4) NOT NULL
        CHECK (risk_free_pct > -50 AND risk_free_pct < 100),
    currency           text NOT NULL CHECK (currency IN ('USD')),
    source             text NOT NULL CHECK (char_length(source) BETWEEN 1 AND 500),
    url                text NOT NULL CHECK (char_length(url) BETWEEN 1 AND 500),
    series_key         text NOT NULL CHECK (char_length(series_key) BETWEEN 1 AND 500),
    read_on            date NOT NULL,
    licence            text NOT NULL CHECK (char_length(licence) BETWEEN 1 AND 500),
    attribution        text NOT NULL CHECK (char_length(attribution) BETWEEN 1 AND 500),
    repo_allowed       boolean NOT NULL DEFAULT false CHECK (NOT repo_allowed),
    commercial_use     boolean NOT NULL DEFAULT false CHECK (NOT commercial_use),
    fetched_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, download_id, series, year)
);
CREATE INDEX reference_return_newest
    ON reference_return (workspace_id, series, fetched_at DESC, download_id);
COMMENT ON TABLE reference_return IS
    'Phase 10 §7.11 (c) 3, D310 amended: Kenneth French''s annual Developed market return '
    '(Mkt-RF, RF, percent, USD) per download and year, own use only, never in the repository; '
    'insert-only, the newest download per series counts.';

CREATE TRIGGER reference_return_append_only BEFORE UPDATE OR DELETE ON reference_return
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER reference_return_no_truncate BEFORE TRUNCATE ON reference_return
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

ALTER TABLE reference_return ENABLE ROW LEVEL SECURITY;
ALTER TABLE reference_return FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON reference_return
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());

-- As 0050 creates them on every workspace table.
CREATE TRIGGER mark_data_version_insert AFTER INSERT ON reference_return FOR EACH ROW
    WHEN (data_version_queue_first(NEW.workspace_id))
    EXECUTE FUNCTION mark_workspace_data_version();
CREATE TRIGGER mark_data_version_update AFTER UPDATE ON reference_return FOR EACH ROW
    WHEN (data_version_queue_first(OLD.workspace_id, NEW.workspace_id))
    EXECUTE FUNCTION mark_workspace_data_version();
CREATE TRIGGER mark_data_version_delete AFTER DELETE ON reference_return FOR EACH ROW
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

REFUSE_IF_DATA = f"""
DO $$
BEGIN
    ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY;
    IF EXISTS (SELECT 1 FROM {TABLE}) THEN
        RAISE EXCEPTION 'downgrade of {revision} refused: table {TABLE} holds rows'
            USING ERRCODE = 'restrict_violation',
                  HINT = 'Export and remove the data first; a downgrade never drops data.';
    END IF;
    ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY;
END
$$;
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(REFUSE_IF_DATA)
    op.execute(f"DROP TABLE {TABLE};")
