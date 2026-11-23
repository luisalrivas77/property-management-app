"""Phase 10 §7.5 (a), extended: signing out moves no workspace data version either.

Migration 0051 took a sign-in's own event and the passkey events out of the data version, so
the first load after a sign-in finds its answers kept. Signing out still moved it: „Abmelden“
writes `user.signed_out` (and „Überall abmelden“ `user.sessions_revoked`) beside the revoked
session (`auth_session`, never marked), so the first load after the next sign-in recomputed
every kept answer although nothing an answer reads had changed.

Checked before adding them (as 0051 did):
- No kept answer shows these events: of the services behind KEPT_ROUTES
  (api/kept_answers.py), every one that reads the `event` table filters by a named domain
  event type (statement.imported, transfer.linked, movement_group.*,
  workspace.settings_changed and the like), never an event of entity `user`; `/api/events`
  and the property History routes (services/history.py), which show the user events, stay
  unkept.
- A kept answer is keyed by its workspace and member (user id), never by the session
  (api/kept_answers.Key), so a new session of the same member after a sign-out may be served
  the answer kept before it. A signed-out or revoked session itself still gets its 401: the
  session is resolved on every request, a kept hit included.
- Both writes touch only `auth_session` (revoked_at; outside the version since 0050) and the
  event, so with this migration a sign-out moves no version at all.

Only the `event` table's INSERT mark changes, exactly as in 0051 (the same WHEN with VARIADIC
and an empty array for an exempt row, so a transaction that writes an exempt event and
anything else still bumps once). The downgrade restores 0051's mark with its four types.

Revision ID: 0055_sign_out_keeps_data_version
Revises: 0054_price_renovation
Create Date: 2026-11-23
"""

from alembic import op

revision = "0055_sign_out_keeps_data_version"
down_revision = "0054_price_renovation"
branch_labels = None
depends_on = None

MARK_INSERT = "mark_data_version_insert"
# 0051's four, as they were.
PREVIOUS_EXEMPT_TYPES = (
    "user.signed_in",
    "user.passkey_enrolled",
    "user.passkey_renamed",
    "user.passkey_removed",
)
# The event types of entity `user` that move no version (phase 10 §7.5 (a)); signing out
# joins them here.
EXEMPT_TYPES = PREVIOUS_EXEMPT_TYPES + (
    "user.signed_out",
    "user.sessions_revoked",
)


def _mark(types: tuple[str, ...]) -> str:
    exempt = ", ".join(f"'{name}'" for name in types)
    return f"""
DROP TRIGGER {MARK_INSERT} ON event;
CREATE TRIGGER {MARK_INSERT} AFTER INSERT ON event FOR EACH ROW
    WHEN (data_version_queue_first(VARIADIC CASE
        WHEN NEW.entity_type = 'user' AND NEW.type IN ({exempt}) THEN '{{}}'::uuid[]
        ELSE ARRAY[NEW.workspace_id] END))
    EXECUTE FUNCTION mark_workspace_data_version();
"""


UPGRADE = _mark(EXEMPT_TYPES)
# 0051's mark, as it was.
DOWNGRADE = _mark(PREVIOUS_EXEMPT_TYPES)


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
