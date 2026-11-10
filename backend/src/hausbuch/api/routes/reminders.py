"""Deadline reminders (docs/phases/phase-5.md §6, §10; D122).

- GET /reminders?from=&to=: every open reminder computed on read (both severities), by due
  day; `from` and `to` keep the due days in that range. Each carries its label code and
  arguments (a date, a month, a year, a property, unit, account label or lender; never an
  amount or a tenant), so the screens compute no date.
- POST /reminders/{reminder_id}/ack: done, snoozed (with snoozed_until), muted or unmuted;
  an append-only reminder_ack row and the event workspace.reminder_acknowledged.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import Path, Query

from hausbuch.schemas.reminders import Reminder, ReminderAck, ReminderAckCreate
from hausbuch.services import reminders
from hausbuch.services.documents import LOCAL_TIME_ZONE

from ..deps import Cmd, domain_router

router = domain_router()


@router.get("/reminders")
def reminder_list(
    cmd: Cmd,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
) -> list[Reminder]:
    """Today is the day in Europe/Berlin. An instance stays listed while it is open, also
    after its due day; one hidden by an acknowledgement or cleared by the data is not."""
    today = datetime.now(LOCAL_TIME_ZONE).date()
    return cmd.read(reminders.list_reminders, today, from_, to)


@router.post("/reminders/{reminder_id}/ack", status_code=201)
def reminder_ack(
    reminder_id: Annotated[str, Path(max_length=160)], body: ReminderAckCreate, cmd: Cmd
) -> ReminderAck:
    """422 reminder.unknown for an id that is not a reminder's; 422 reminder.scope_invalid
    when its scope is not this workspace's property, loan, tenancy, owner or account of the
    kind's scope type."""
    return cmd.run(reminders.acknowledge, reminder_id, body)
