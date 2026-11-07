"""The workspace's settings (docs/phases/phase-2.md D16, D26, D42).

GET returns both switches; PATCH sets the ones sent and appends one workspace.settings_changed
event when anything changed. Any signed-in member of the workspace may change them.
"""

from hausbuch.schemas.api import WorkspaceSettings, WorkspaceSettingsPatch
from hausbuch.services.workspace_settings import get_settings, update_settings

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/workspace/settings")
def workspace_settings_get(cmd: Cmd) -> WorkspaceSettings:
    return cmd.read(get_settings)


@router.patch("/workspace/settings")
def workspace_settings_update(body: WorkspaceSettingsPatch, cmd: Cmd) -> WorkspaceSettings:
    """Only the settings sent change; a patch that changes nothing writes no event."""
    return cmd.run(update_settings, **body.changes())
