"""Errors shared by the service commands."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from hausbuch.rules.errors import RuleError
from hausbuch.schemas.document import RetentionResult
from hausbuch.schemas.enums import EntityType


class NotFoundError(LookupError):
    """The entity does not exist in the current workspace (or RLS hides it).

    entity_type is an EntityType, or a plain name for rows that carry no events of their own
    (e.g. "rent_schedule", "price_split").
    """

    def __init__(self, entity_type: EntityType | str, entity_id: UUID) -> None:
        super().__init__(f"{entity_type} {entity_id} not found")
        self.entity_type = entity_type
        self.entity_id = entity_id


class RuleViolationError(RuleError):
    """A service-layer invariant failed (tests/fixtures/phase1/validation.yaml); nothing was
    written. A RuleError, so `code` is the stable error code; `codes` lists every one."""

    def __init__(self, codes: Sequence[str], message: str) -> None:
        super().__init__(codes[0], f"{message}: {', '.join(codes)}")
        self.codes = tuple(codes)


def require_valid(codes: Sequence[str], message: str) -> None:
    """Raise RuleViolationError if a validation check returned any error codes."""
    if codes:
        raise RuleViolationError(codes, message)


class ConflictError(ValueError):
    """The command conflicts with stored state (an overlapping tenancy, a duplicate label, a
    row already superseded). Nothing was written; the transaction must be rolled back."""

    def __init__(self, message: str, *, constraint: str | None = None) -> None:
        super().__init__(message)
        self.constraint = constraint


class TenancyOverlapError(ConflictError):
    """Another tenancy of the same unit overlaps these dates (both ends inclusive)."""


class RetentionHoldError(ConflictError):
    """The data is still under a retention hold, so it may not be deleted yet.

    result: the retention decision that refused it (a hold, or a retain_until not yet past).
    """

    def __init__(self, message: str, *, result: RetentionResult) -> None:
        super().__init__(message)
        self.result = result


def violated_constraint(exc: IntegrityError) -> str | None:
    """The name of the constraint Postgres reported, if any."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
