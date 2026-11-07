"""The workspace's settings (docs/phases/phase-2.md D16, D26, D42).

Two switches on the workspace row: `ai_tenant_documents` (tenant documents may be sent to the
model, §11.2) and `auto_accept_llm` (a confident model answer may book a line without review,
within D40's limits). Any signed-in member may change them (one household). A change writes
only the values that differ and appends one workspace.settings_changed event (entity
`workspace`, the changed names and their new booleans); a patch that changes nothing writes
no event. A setting is never changed by a direct database write, which would skip the event.
"""

from sqlalchemy import Connection, select, update

from hausbuch.db.tables import workspace as workspace_table
from hausbuch.schemas.api import WorkspaceSettings
from hausbuch.schemas.events import EventType, WorkspaceSetting, WorkspaceSettingsChanged

from .context import CommandContext
from .events import append_event

_COLUMNS = [workspace_table.c[s.value] for s in WorkspaceSetting]


def get_settings(conn: Connection) -> WorkspaceSettings:
    """The current workspace's settings (RLS shows only its own row)."""
    row = conn.execute(select(*_COLUMNS)).one()
    return WorkspaceSettings.model_validate(row._asdict())


def update_settings(
    ctx: CommandContext,
    *,
    ai_tenant_documents: bool | None = None,
    auto_accept_llm: bool | None = None,
) -> WorkspaceSettings:
    """Set the settings passed (None = unchanged); one event naming those that changed."""
    requested = {
        WorkspaceSetting.AI_TENANT_DOCUMENTS: ai_tenant_documents,
        WorkspaceSetting.AUTO_ACCEPT_LLM: auto_accept_llm,
    }
    current = ctx.conn.execute(
        select(*_COLUMNS).where(workspace_table.c.id == ctx.workspace_id).with_for_update()
    ).one()
    changes = {
        setting: value
        for setting, value in requested.items()
        if value is not None and getattr(current, setting.value) != value
    }
    if not changes:
        return WorkspaceSettings.model_validate(current._asdict())
    row = ctx.conn.execute(
        update(workspace_table)
        .where(workspace_table.c.id == ctx.workspace_id)
        .values({setting.value: value for setting, value in changes.items()})
        .returning(*_COLUMNS)
    ).one()
    append_event(
        ctx,
        EventType.WORKSPACE_SETTINGS_CHANGED,
        ctx.workspace_id,
        WorkspaceSettingsChanged(changes=changes),
    )
    return WorkspaceSettings.model_validate(row._asdict())
