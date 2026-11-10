"""Email-in (docs/phases/phase-5.md §5, §10, §11; D121).

- GET /inbound-mail: the last 30 days' attempts at forwarded mails (one row per attempt),
  newest first, each with the documents and imports it brought. A row holds no address,
  subject or body: only the Message-ID's hash (never returned), the time, the decision
  and the counts.
- GET /inbound-mail/address: the token address and the label for the settings page, null
  while email-in is not set up.
"""

from datetime import UTC, datetime

from hausbuch.jobs.inbound_mail import configured
from hausbuch.schemas.inbound_mail import InboundMail, InboxAddress
from hausbuch.services.inbound_mail import list_inbound_mail

from ..deps import Cmd, SettingsDep, domain_router

router = domain_router()


@router.get("/inbound-mail")
def inbound_mail_list(cmd: Cmd) -> list[InboundMail]:
    return cmd.read(list_inbound_mail, datetime.now(UTC))


@router.get("/inbound-mail/address")
def inbound_mail_address(settings: SettingsDep) -> InboxAddress:
    """Signed-in members only, as every domain route: the token is what keeps strangers'
    mail out of the label, so it is shown to members and nowhere else."""
    if not configured(settings):
        return InboxAddress(configured=False)
    return InboxAddress(configured=True, address=settings.inbox_address, label=settings.inbox_label)
