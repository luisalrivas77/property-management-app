"""An account's owner shares (docs/phases/phase-6.md D157, §4.2; fixture
tests/fixtures/phase6/ownership.yaml). Built in phase 6 step 1.

`validate(shares, owners)` checks a full set against the workspace's owners: Σ = 1 exactly
(ownership.sum_not_one), each owner once and every owner known (ownership.owner_missing). The
schema (SetOwnershipBody) already refuses a sum other than 1 and a repeated owner; this rule
adds the owners only the service knows, and `supersede(current, new)` names the rows a new
set replaces (a second set supersedes the first).
"""

from collections.abc import Iterable, Sequence
from uuid import UUID

from hausbuch.schemas.household import AccountOwnership, OwnershipShare

STEP = "phase 6 step 1 (ownership)"


def validate(shares: Sequence[OwnershipShare], owners: Iterable[UUID]) -> None:
    raise NotImplementedError(STEP)


def supersede(current: Sequence[AccountOwnership], new: Sequence[OwnershipShare]) -> list[UUID]:
    raise NotImplementedError(STEP)
