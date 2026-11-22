"""Phase 10 §7.5 (a): a sign-in's own event no longer moves the workspace data version.

Migration 0050 moves a workspace's data version at the commit of every write, so the API
recomputes every kept answer after it. A sign-in writes three things: its session
(`auth_session`, never marked), the passkey's sign counter (`auth_passkey`, never marked) and
its own event `user.signed_in`, which until now moved the version, so the first load after
every sign-in recomputed every kept answer although no answer had changed. The passkey events
(`user.passkey_enrolled`, `.passkey_renamed`, `.passkey_removed`) belong with the passkey
table, outside the version too.

No kept answer shows these events: they are read only by `/api/events` and the property
History routes (services/history.py), which stay unkept (api/kept_answers.py), and no service
of a kept route reads an event of entity `user`.

Only the `event` table's INSERT mark changes; its UPDATE and DELETE marks (which an
append-only table never fires in practice) still mark every row, the safe direction. The
WHEN still calls `data_version_queue_first`, now through VARIADIC with an empty array for an
exempt row: the function loops over no workspace, records nothing in
`app.data_version_queued` and returns false. Recording nothing matters: a workspace recorded
as queued by an exempt row would leave a later write of the same transaction without its
bump. So a transaction that writes a sign-in event and anything else still bumps once.

`user.signed_out` and `user.sessions_revoked` still move the version (not named by §7.5 (a)).

Revision ID: 0051_sign_in_keeps_data_version
Revises: 0050_workspace_data_version
Create Date: 2026-11-22
"""

from alembic import op

revision = "0051_sign_in_keeps_data_version"
down_revision = "0050_workspace_data_version"
branch_labels = None
depends_on = None

MARK_INSERT = "mark_data_version_insert"
# The event types of entity `user` that move no version (phase 10 §7.5 (a)).
EXEMPT_TYPES = (
    "user.signed_in",
    "user.passkey_enrolled",
    "user.passkey_renamed",
    "user.passkey_removed",
)
_EXEMPT = ", ".join(f"'{name}'" for name in EXEMPT_TYPES)

UPGRADE = f"""
DROP TRIGGER {MARK_INSERT} ON event;
CREATE TRIGGER {MARK_INSERT} AFTER INSERT ON event FOR EACH ROW
    WHEN (data_version_queue_first(VARIADIC CASE
        WHEN NEW.entity_type = 'user' AND NEW.type IN ({_EXEMPT}) THEN '{{}}'::uuid[]
        ELSE ARRAY[NEW.workspace_id] END))
    EXECUTE FUNCTION mark_workspace_data_version();
"""

# 0050's mark, as it was.
DOWNGRADE = f"""
DROP TRIGGER {MARK_INSERT} ON event;
CREATE TRIGGER {MARK_INSERT} AFTER INSERT ON event FOR EACH ROW
    WHEN (data_version_queue_first(NEW.workspace_id))
    EXECUTE FUNCTION mark_workspace_data_version();
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
