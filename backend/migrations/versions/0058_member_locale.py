"""Phase 10 §7.13 (e) 3: `member_locale`, the language each member last used, so the warm of
the kept answers (§7.4) asks the routes in that language after a restart too.

A kept answer's key holds the language (api/kept_answers.py), so a warm in German leaves an
English-speaking member's first load to be computed again. The store remembers each member's
language in memory; this table keeps it across a start, read by the warm step
(jobs/kept_answers.run_warm) for a member the process has not seen yet.

One row per member, replaced in place: a preference, not a domain record, so it writes no
event and keeps no history (as `auth_session.last_seen_at`). The API writes it only when a
member's language changes, after the request, off the request path.

Outside the data version on purpose: no `mark_data_version` trigger (0050), so switching the
language never makes the household's kept answers be computed again. No kept route reads it
(tests/api/test_kept_answers.py `_UNVERSIONED_TABLES`), and tests/db/test_data_version.py lists
it beside 0050's exclusions.

Forced row-level security on `workspace_id`. The downgrade drops the table: a language is
recovered from the member's next request, so nothing is lost that the app cannot rebuild.

Revision ID: 0058_member_locale
Revises: 0057_reference_return
Create Date: 2026-11-28
"""

from alembic import op

revision = "0058_member_locale"
down_revision = "0057_reference_return"
branch_labels = None
depends_on = None

APP_ROLE = "hausbuch_app"
TABLE = "member_locale"

DDL = r"""
CREATE TABLE member_locale (
    workspace_id  uuid NOT NULL REFERENCES workspace (id),
    user_id       uuid NOT NULL,
    locale        text NOT NULL CHECK (locale IN ('de', 'en')),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id),
    FOREIGN KEY (workspace_id, user_id) REFERENCES app_user (workspace_id, id)
);
COMMENT ON TABLE member_locale IS
    'Phase 10 §7.13 (e) 3: the language each member last used, for the warm of the kept '
    'answers after a start; a preference, no event, outside the data version (no marks).';

ALTER TABLE member_locale ENABLE ROW LEVEL SECURITY;
ALTER TABLE member_locale FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace_isolation ON member_locale
    USING (workspace_id = current_workspace_id())
    WITH CHECK (workspace_id = current_workspace_id());
"""

GRANTS = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        GRANT SELECT, INSERT, UPDATE ON {TABLE} TO {APP_ROLE};
    ELSE
        RAISE NOTICE 'role {APP_ROLE} does not exist; skipping grants';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(f"DROP TABLE {TABLE};")
