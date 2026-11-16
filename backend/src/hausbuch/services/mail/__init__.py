"""Outgoing mail (docs/phases/phase-5.md D123): the reminder digest through Scaleway
Transactional Email. `tem.py` is the client; tests use a fake `Mailer`."""

from .tem import (
    MIN_SUBJECT_LENGTH,
    Mailer,
    MailSendError,
    OutgoingMail,
    TemClient,
    available,
    configured,
    mail_settings,
    tem_client,
)

__all__ = [
    "MIN_SUBJECT_LENGTH",
    "MailSendError",
    "Mailer",
    "OutgoingMail",
    "TemClient",
    "available",
    "configured",
    "mail_settings",
    "tem_client",
]
