"""An account's owner shares (docs/phases/phase-6.md D157, §4.2; fixture
tests/fixtures/phase6/ownership.yaml). Built in phase 6 step 1.

`validate(shares, owners)` checks a full set against the workspace's owners: Σ = 1 exactly
(ownership.sum_not_one), each owner once and every owner known (ownership.owner_missing). The
schema (SetOwnershipBody) already refuses a sum other than 1 and a repeated owner; this rule
adds the owners only the service knows, and `supersede(current, new)` names the rows a new
set replaces (a second set supersedes the first).
"""

from collections.abc import Iterable, Sequence
from fractions import Fraction
from uuid import UUID

from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.household import AccountOwnership, OwnershipShare


def validate(shares: Sequence[OwnershipShare], owners: Iterable[UUID]) -> None:
    """Every owner known first (a share for someone else is the clearer error), then each
    owner once and Σ = 1 in exact fractions (a rounded 0.33 + 0.67 would not prove it)."""
    known = set(owners)
    if not shares or any(s.owner_id not in known for s in shares):
        raise RuleError(E.OWNERSHIP_OWNER_MISSING)
    named = [s.owner_id for s in shares]
    if len(named) != len(set(named)):
        raise RuleError(E.OWNERSHIP_SUM_NOT_ONE, "each owner once")
    if sum((Fraction(s.share_numerator, s.share_denominator) for s in shares), Fraction(0)) != 1:
        raise RuleError(E.OWNERSHIP_SUM_NOT_ONE)


def supersede(current: Sequence[AccountOwnership], new: Sequence[OwnershipShare]) -> list[UUID]:
    """The rows a new full set replaces: every current row of the account (one already
    superseded by an earlier change is not superseded again). The new set is the whole set,
    so even an unchanged share gets a new row under the new effective_from."""
    del new  # the whole current set goes, whatever the new one holds
    return [row.id for row in current if row.superseded_by is None]
