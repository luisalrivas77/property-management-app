"""The workspace's settings (docs/phases/phase-2.md D16, D26, D42; phase-5.md D123).

GET returns the three switches; PATCH sets the ones sent and appends one
workspace.settings_changed event when anything changed. Any signed-in member of the workspace
may change them. Both also say whether reminder emails can be sent at all
(`reminder_emails_available`: the Transactional Email settings are present), so the page can
say when the switch has no effect yet.
"""

from hausbuch.schemas.api import WorkspaceSettings, WorkspaceSettingsPatch
from hausbuch.services import mail
from hausbuch.services.workspace_settings import get_settings, update_settings
from hausbuch.settings import Settings

from ..deps import Cmd, SettingsDep, domain_router

router = domain_router()


@router.get("/workspace/settings")
def workspace_settings_get(cmd: Cmd, settings: SettingsDep) -> WorkspaceSettings:
    return _with_availability(cmd.read(get_settings), settings)


@router.patch("/workspace/settings")
def workspace_settings_update(
    body: WorkspaceSettingsPatch, cmd: Cmd, settings: SettingsDep
) -> WorkspaceSettings:
    """Only the settings sent change; a patch that changes nothing writes no event."""
    return _with_availability(cmd.run(update_settings, **body.changes()), settings)


def _with_availability(current: WorkspaceSettings, settings: Settings) -> WorkspaceSettings:
    return current.model_copy(update={"reminder_emails_available": mail.configured(settings)})
