"""Phase 5: email-in from one label of the household's Gmail account (docs/phases/phase-5.md
§5, §8, §9; D121).

`decide` (rules/inbound_mail.py, step 4) reads only the header fields and the BODYSTRUCTURE of
a message, before any part is fetched; these are its inputs and output, and the read model of
an `inbound_mail` row. Nothing of the mail but its attachments is ever stored: an
`inbound_mail` row has the SHA-256 of the Message-ID, the time, the decision and the counts,
never an address, a subject, a body or a header (D121, §5.5). `InboundMailFacts` holds the
sender and the recipients only in memory, for the decision.

InboundDecision is stored in a CHECK'd column, so it lives here with its module and
tests/db/test_schema_consistency.py keeps it equal to migration 0019's CHECK.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from .common import Schema

MAX_ATTACHMENTS = 20  # D121: at most 20 attachments per mail
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # D121: 25 MiB per attachment
MAX_ATTEMPTS = 3  # D121: a failed mail is retried at the next polls, at most 3 attempts

# "2", or "2.1" for a part of a nested message/rfc822 (one level is followed, D121; the
# BODYSTRUCTURE numbering of RFC 3501 §6.4.5, where a nested message's parts are n.1, n.2 …).
PartIndex = Annotated[str, Field(pattern=r"^[1-9][0-9]*(\.[1-9][0-9]*)*$", max_length=32)]


class InboundDecision(StrEnum):
    """inbound_mail.decision (migration 0019). accepted and rejected are final at once; a
    message is final after three failed rows (MAX_ATTEMPTS)."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class AuthResult(Schema):
    """One method result of one Authentication-Results header (RFC 8601). Only a header whose
    authserv_id equals the policy's (Gmail's own, `mx.google.com`) is trusted; any other can
    be written by the sender and is ignored (D121)."""

    authserv_id: str
    method: Literal["dkim", "spf", "dmarc"]
    result: str  # pass, fail, softfail, neutral, none, temperror, permerror, policy …
    domain: str | None = None  # header.d (dkim) / smtp.mailfrom's domain (spf) / header.from


class MailPart(Schema):
    """One leaf of the BODYSTRUCTURE, or a message/rfc822 part itself (nested_rfc822)."""

    index: PartIndex
    filename: str | None = None  # a part without one is a body part, never an attachment
    media_type: str  # lower-case type/subtype as the BODYSTRUCTURE states it
    size_bytes: int = Field(ge=0)  # the BODYSTRUCTURE's size of the encoded part
    nested_rfc822: bool = False  # this part is a message/rfc822 (a mail forwarded as file)


class InboundMailFacts(Schema):
    """What decide reads (§5.3): the header fields and the part list, nothing fetched."""

    from_address: str
    recipients: list[str] = Field(default_factory=list)  # To, Cc and Delivered-To together
    auth_results: list[AuthResult] = Field(default_factory=list)  # every A-R header
    parts: list[MailPart] = Field(default_factory=list)
    message_id_present: bool


class InboundPolicy(Schema):
    """The workspace's policy (§5.3): the members' emails, the token address and the trusted
    authserv-id from Settings, the accepted media types (Settings.upload_media_types)."""

    allowed_senders: list[str]
    token_address: str
    authserv_id: str = "mx.google.com"
    media_types: frozenset[str]
    max_attachments: int = Field(default=MAX_ATTACHMENTS, ge=1)
    max_bytes: int = Field(default=MAX_ATTACHMENT_BYTES, ge=1)


class MailDecision(Schema):
    """decide's output. `code` is the first failed check's `email.*` code for a rejection,
    and `email.accepted` for an acceptance (§9 types `code: str`, never null; the accepted
    value is a step 0 choice). `attachments` are the part indexes to fetch with BODY.PEEK, in
    BODYSTRUCTURE order, empty for a rejection; `skipped` counts the attachment parts of
    other media types, which are neither fetched nor stored."""

    accept: bool
    code: str = Field(max_length=64)
    attachments: list[PartIndex] = Field(default_factory=list)
    skipped: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _rejected_fetches_nothing(self) -> Self:
        if not self.accept and (self.attachments or self.skipped):
            raise ValueError("a rejected mail's parts are never fetched or counted")
        if self.accept and not self.attachments:
            raise ValueError("an accepted mail has at least one attachment to fetch")
        return self


class InboundMail(Schema):
    """GET /inbound-mail: one attempt's row with the documents and imports it produced (the
    table names its timestamp recorded_at, so not Record, as TaxInput)."""

    id: UUID
    workspace_id: UUID
    recorded_at: datetime
    received_at: datetime | None = None  # the IMAP INTERNALDATE
    attempt: int = Field(ge=1, le=MAX_ATTEMPTS)
    decision: InboundDecision
    code: str | None = Field(default=None, max_length=64)
    attachments_total: int | None = Field(default=None, ge=0)
    attachments_accepted: int | None = Field(default=None, ge=0)
    documents: list[UUID] = Field(default_factory=list)
    imports: list[UUID] = Field(default_factory=list)
