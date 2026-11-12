"""The workspace's settings (docs/phases/phase-2.md D16, D26, D42; docs/phases/phase-5.md D123).

Three switches on the workspace row: `ai_tenant_documents` (tenant documents may be sent to the
model, §11.2), `auto_accept_llm` (a confident model answer may book a line without review,
within D40's limits) and `reminder_emails` (phase 5: the daily reminder digest is mailed to the
members, migration 0019). Any signed-in member may change them (one household). A change writes
only the values that differ and appends one workspace.settings_changed event (entity
`workspace`, the changed names and their new booleans); a patch that changes nothing writes
no event. A setting is never changed by a direct database write, which would skip the event.

Phase 6 D168: `ai_private_accounts` (private-account lines and the depot's documents may be
sent to the model). Switching it on records the signed-in member as the first consent
(`consented_by` on the settings_changed event); the other owner records their own consent by
their own click (`record_ai_consent`, workspace.ai_consent_recorded). The switch takes effect
(`private_ai_allowed`) only once two different members have consented since it was last
switched on; switching it off and on again asks both once more.
"""

from uuid import UUID

from sqlalchemy import Connection, select, update

from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.errors import (
    SETTINGS_CONSENT_ALREADY_RECORDED,
    SETTINGS_CONSENT_NOT_A_MEMBER,
    SETTINGS_CONSENT_SWITCH_OFF,
)
from hausbuch.schemas.api import WorkspaceSettings
from hausbuch.schemas.events import (
    AiConsentRecorded,
    EventType,
    WorkspaceSetting,
    WorkspaceSettingsChanged,
)

from .allocations import user_id
from .context import CommandContext
from .errors import RuleViolationError
from .events import append_event

_COLUMNS = [workspace_table.c[s.value] for s in WorkspaceSetting]
# The members whose consent the switch needs (D168: both owners).
CONSENTS_NEEDED = 2


def get_settings(conn: Connection) -> WorkspaceSettings:
    """The current workspace's settings (RLS shows only its own row), with the members who
    consented to ai_private_accounts since it was switched on."""
    row = conn.execute(select(*_COLUMNS)).one()
    data = row._asdict()
    consents = private_ai_consents(conn) if data["ai_private_accounts"] else []
    return WorkspaceSettings.model_validate(
        data
        | {
            "ai_private_accounts_consented_by": consents,
            "ai_private_accounts_active": len(consents) >= CONSENTS_NEEDED,
        }
    )


def private_ai_consents(conn: Connection) -> list[UUID]:
    """The members who consented to ai_private_accounts since its latest switch-on, in the
    order they did: the switching member, then each ai_consent_recorded after it. Empty while
    the switch is off or was switched on before consents were recorded (before step 1)."""
    e = event_table.c
    rows = conn.execute(
        select(e.type, e.payload)
        .where(
            e.type.in_(
                [
                    EventType.WORKSPACE_SETTINGS_CHANGED.value,
                    EventType.WORKSPACE_AI_CONSENT_RECORDED.value,
                ]
            )
        )
        .order_by(e.seq)
    ).all()
    consents: list[UUID] = []
    setting = WorkspaceSetting.AI_PRIVATE_ACCOUNTS.value
    for row in rows:
        if row.type == EventType.WORKSPACE_SETTINGS_CHANGED.value:
            changes = row.payload.get("changes") or {}
            if setting not in changes:
                continue
            consents = []
            by = row.payload.get("consented_by")
            if changes[setting] and by:
                consents = [UUID(by)]
        elif row.payload.get("setting") == setting:
            member = UUID(row.payload["consented_by"])
            if member not in consents:
                consents.append(member)
    return consents


def private_ai_allowed(conn: Connection) -> bool:
    """Whether private-account lines may reach the model now (D168): the switch is on and two
    different members consented since it was switched on."""
    on = conn.execute(select(workspace_table.c.ai_private_accounts)).scalar_one_or_none()
    return bool(on) and len(private_ai_consents(conn)) >= CONSENTS_NEEDED


def _member(ctx: CommandContext) -> UUID:
    """The signed-in member whose consent this is; a job or the model never consents."""
    member = user_id(ctx)
    if member is None:
        raise RuleViolationError([SETTINGS_CONSENT_NOT_A_MEMBER], "a member consents")
    return member


def record_ai_consent(ctx: CommandContext) -> WorkspaceSettings:
    """POST /workspace/settings/ai-consent: the signed-in member's own consent to
    ai_private_accounts (D168), one workspace.ai_consent_recorded event. Refused while the
    switch is off (settings.consent_switch_off) and for a member who consented already
    (settings.consent_already_recorded)."""
    member = _member(ctx)
    on = ctx.conn.execute(
        select(workspace_table.c.ai_private_accounts)
        .where(workspace_table.c.id == ctx.workspace_id)
        .with_for_update()
    ).scalar_one()
    if not on:
        raise RuleViolationError([SETTINGS_CONSENT_SWITCH_OFF], "the switch is off")
    if member in private_ai_consents(ctx.conn):
        raise RuleViolationError([SETTINGS_CONSENT_ALREADY_RECORDED], "consented already")
    append_event(
        ctx,
        EventType.WORKSPACE_AI_CONSENT_RECORDED,
        ctx.workspace_id,
        AiConsentRecorded(setting=WorkspaceSetting.AI_PRIVATE_ACCOUNTS, consented_by=member),
    )
    return get_settings(ctx.conn)


def update_settings(
    ctx: CommandContext,
    *,
    ai_tenant_documents: bool | None = None,
    auto_accept_llm: bool | None = None,
    reminder_emails: bool | None = None,
    ai_private_accounts: bool | None = None,
) -> WorkspaceSettings:
    """Set the settings passed (None = unchanged); one event naming those that changed.
    Switching ai_private_accounts on records the signed-in member's consent (D168)."""
    requested = {
        WorkspaceSetting.AI_TENANT_DOCUMENTS: ai_tenant_documents,
        WorkspaceSetting.AUTO_ACCEPT_LLM: auto_accept_llm,
        WorkspaceSetting.REMINDER_EMAILS: reminder_emails,
        WorkspaceSetting.AI_PRIVATE_ACCOUNTS: ai_private_accounts,
    }
    if ai_private_accounts:
        _member(ctx)
    current = ctx.conn.execute(
        select(*_COLUMNS).where(workspace_table.c.id == ctx.workspace_id).with_for_update()
    ).one()
    changes = {
        setting: value
        for setting, value in requested.items()
        if value is not None and getattr(current, setting.value) != value
    }
    if not changes:
        return get_settings(ctx.conn)
    ctx.conn.execute(
        update(workspace_table)
        .where(workspace_table.c.id == ctx.workspace_id)
        .values({setting.value: value for setting, value in changes.items()})
    )
    switched_on = changes.get(WorkspaceSetting.AI_PRIVATE_ACCOUNTS) is True
    append_event(
        ctx,
        EventType.WORKSPACE_SETTINGS_CHANGED,
        ctx.workspace_id,
        WorkspaceSettingsChanged(
            changes=changes, consented_by=_member(ctx) if switched_on else None
        ),
    )
    return get_settings(ctx.conn)
