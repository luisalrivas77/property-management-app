"""Service-layer invariants the database cannot express (tests/fixtures/phase1/validation.yaml).

Each check returns the list of violated error codes; an empty list means valid.
"""

from collections.abc import Sequence
from datetime import date
from fractions import Fraction as Rational

from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import TenancyRole
from hausbuch.schemas.events import EventRef

SHARES_MUST_SUM_TO_ONE = "ownership.shares_must_sum_to_one"
NO_TENANT = "tenancy.no_tenant"
EXACTLY_ONE_PRIMARY = "tenancy.exactly_one_primary"
RENT_OUTSIDE_TENANCY = "rent_schedule.outside_tenancy"
CORRECTION_MISMATCH = "event.correction_mismatch"


def check_ownership_shares(shares: Sequence[Fraction]) -> list[str]:
    """The owners' shares of one property must add up to exactly 1 (exact rationals)."""
    total = sum((Rational(s.numerator, s.denominator) for s in shares), Rational(0))
    return [] if total == 1 else [SHARES_MUST_SUM_TO_ONE]


def check_tenancy_parties(roles: Sequence[TenancyRole]) -> list[str]:
    """A tenancy has at least one tenant and exactly one of them is primary."""
    if not roles:
        return [NO_TENANT]
    primaries = sum(1 for r in roles if r == TenancyRole.PRIMARY)
    return [] if primaries == 1 else [EXACTLY_ONE_PRIMARY]


def check_rent_row(
    tenancy_start: date, tenancy_end: date | None, effective_from: date
) -> list[str]:
    """A rent row takes effect within the tenancy, both ends inclusive; open end = forever."""
    before_start = effective_from < tenancy_start
    after_end = tenancy_end is not None and effective_from > tenancy_end
    return [RENT_OUTSIDE_TENANCY] if before_start or after_end else []


def check_correction(original: EventRef, correction: EventRef) -> list[str]:
    """A correcting event repeats the corrected event's type and entity."""
    same = (
        original.type == correction.type
        and original.entity_type == correction.entity_type
        and original.entity_id == correction.entity_id
    )
    return [] if same else [CORRECTION_MISMATCH]
