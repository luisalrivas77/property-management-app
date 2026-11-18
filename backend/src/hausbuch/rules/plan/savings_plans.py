"""Savings plans detected from the transfers (docs/phases/phase-9.md §6.2, D245). Fixture:
tests/fixtures/phase9/savings_plans.yaml.

A plan is a destination that received a confirmed outgoing payment from a private account in
each of the last three closed months: an own account of kind depot (class `depot`) or exchange
(class `crypto`), or the payee of lines booked `hh_investment_securities` (`depot`) or
`hh_investment_crypto` (`crypto`). A transfer into an own savings account is no plan (it builds
the reserve). monthly = the median of the three monthly sums. The household's overrides then set
a plan's amount or drop it.

Step 0's readings (the fixture's header): a plan's key is its destination account's key (here
the account id as text), or for a payee the payee in lower case with every run of other
characters as one hyphen (at most 64 characters; a payee with no letter or digit has no key and
no plan); plans by monthly amount, largest first, then by key.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.plan import PlanClass, PlanOverride, SavingsPlan

ZERO = Decimal("0.00")

PLAN_KINDS = {AccountKind.DEPOT: PlanClass.DEPOT, AccountKind.EXCHANGE: PlanClass.CRYPTO}
PLAN_CODES = {
    AllocationCategory.HH_INVESTMENT_SECURITIES: PlanClass.DEPOT,
    AllocationCategory.HH_INVESTMENT_CRYPTO: PlanClass.CRYPTO,
}
KEY_LENGTH = 64


@dataclass(frozen=True)
class PlanLine:
    """One confirmed outgoing payment from a private account (`amount` < 0): into an own
    account (`account_id` and its `account_kind`), or to a payee with its category."""

    booked: date
    amount: Decimal
    account_id: UUID | None = None
    account_kind: AccountKind | None = None
    payee: str | None = None
    category: AllocationCategory | None = None


def payee_key(payee: str) -> str | None:
    """The payee folded to a plan key; None when nothing of it is a letter or a digit."""
    key = re.sub(r"[^a-z0-9]+", "-", payee.lower()).strip("-")[:KEY_LENGTH].strip("-")
    return key or None


def _ordered(plans: list[SavingsPlan]) -> list[SavingsPlan]:
    return sorted(plans, key=lambda p: (-p.monthly, p.key))


def detect(lines: Sequence[PlanLine], last_three_closed: Sequence[str]) -> list[SavingsPlan]:
    """A destination paid in each of the last three closed months; monthly = the median."""
    if len(last_three_closed) != 3:
        raise ValueError("a plan is read from the last three closed months")
    months = list(last_three_closed)
    sums: dict[str, dict[str, Decimal]] = {}
    meta: dict[str, tuple[PlanClass, UUID | None, str | None]] = {}
    for line in lines:
        month = line.booked.strftime("%Y-%m")
        if month not in months or line.amount >= 0:
            continue
        if line.account_id is not None:
            plan_class = PLAN_KINDS.get(line.account_kind) if line.account_kind else None
            key: str | None = str(line.account_id)
            destination = (line.account_id, None)
        else:
            plan_class = PLAN_CODES.get(line.category) if line.category else None
            key = payee_key(line.payee or "")
            destination = (None, line.payee)
        if plan_class is None or key is None:
            continue
        meta[key] = (plan_class, *destination)
        by_month = sums.setdefault(key, dict.fromkeys(months, ZERO))
        by_month[month] -= line.amount
    plans = []
    for key, by_month in sums.items():
        three = [by_month[m] for m in months]
        if any(v <= 0 for v in three):
            continue
        plan_class, account_id, payee = meta[key]
        plans.append(
            SavingsPlan(
                key=key,
                destination_account_id=account_id,
                destination_payee=payee,
                asset_class=plan_class,
                monthly=sorted(three)[1],
                months=three,
            )
        )
    return _ordered(plans)


def apply_overrides(
    plans: Sequence[SavingsPlan], overrides: Sequence[PlanOverride]
) -> list[SavingsPlan]:
    """The household's typed amounts and dropped plans over the detected ones. An override of a
    plan that was not detected changes nothing here (the service names it: „Szenario prüfen“)."""
    by_key = {o.plan_key: o for o in overrides}
    out = []
    for plan in plans:
        override = by_key.get(plan.key)
        if override is None:
            out.append(plan)
        elif not override.dropped:
            out.append(plan.model_copy(update={"monthly": override.monthly, "overridden": True}))
    return _ordered(out)


def already_invested(plans: Sequence[SavingsPlan]) -> Decimal:
    return sum((p.monthly for p in plans), ZERO)
