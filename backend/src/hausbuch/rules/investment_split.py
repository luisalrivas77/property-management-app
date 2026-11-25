"""The household's investment split (docs/phases/phase-10.md §7.11 (a) 2, (a) 6, (f) 1, D301,
D304). Fixture: tests/fixtures/phase10/investment_split.yaml.

Pure functions over the levers and rows services.plan reads:

- `effective_split`: the mix the goal's walk invests the money above the reserve in. The saved
  lever `investment_split`; else the continuing savings plans' mix by monthly amount; else
  today's held funds and crypto by value. A kind with a share above 0 and no typed yearly rate
  (`value_change.depot` / `.crypto`, D247: a typed 0 is a rate) is left out and the other
  takes its share; when every kind with a share is left out, nothing moves and the to-do
  „Rendite eintragen“ names them. Nothing saved, planned or held: source `none`, nothing moves,
  no to-do (no kind to name).
- `prefill`: the window's purchases per kind as shares that only fill the form (§7.11 (f) 1).
- `not_invested`: once a split is saved, per kind the split's share of everything invested in
  the window less what was bought in it, while above 0 (§7.11 (a) 6). A fact: no reminder, no
  email, no colour; in `generic` mode (D251) the amount only, without a kind.

A purchase is a confirmed outgoing payment from a private account (phase 9's plan lines, D245)
into an own depot (depot), exchange (crypto) or wallet (crypto), or to a payee booked
hh_investment_securities (depot) or hh_investment_crypto (crypto). Money moved into an account
of the reserve's set (D237, and a depot ticked „Zählt zur Reserve“, D304) is no purchase, and
such a depot's plan and value are left out of the mix.

Outside rules/health/ and rules/plan/ on purpose, so services.health.rule_version() (the stored
key figures' rule version, D242) does not change with it: no stored figure reads the split.

Shares from amounts: depot = round half up (100 × depot ÷ total, 0.1), crypto = 100 − depot, so
the two add up to exactly 100.0.
"""

from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import HealthTodo, HealthTodoCode
from hausbuch.schemas.plan import (
    EffectiveSplit,
    InvestmentSplit,
    NotInvested,
    NotInvestedLine,
    PlanClass,
    SavingsPlan,
    SplitPrefill,
    SplitSource,
    ValueChange,
)

from .plan.projection import CLASS_WORDS, PLAN_HREF
from .plan.savings_plans import PLAN_CODES, PlanLine

KINDS = (PlanClass.DEPOT, PlanClass.CRYPTO)
HUNDRED = Decimal(100)
TENTH = Decimal("0.1")
CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# The own accounts a purchase goes into, by the kind it buys. A wallet is crypto (§7.11 (f)).
PURCHASE_KINDS = {
    AccountKind.DEPOT: PlanClass.DEPOT,
    AccountKind.EXCHANGE: PlanClass.CRYPTO,
    AccountKind.WALLET: PlanClass.CRYPTO,
}


@dataclass(frozen=True)
class Held:
    """One valued line of today's net worth in the class depot or crypto."""

    account_id: UUID | None
    asset_class: PlanClass
    value: Decimal


def shares(amounts: Mapping[PlanClass, Decimal]) -> InvestmentSplit | None:
    """The amounts as a split; None when they add up to nothing."""
    depot = max(amounts.get(PlanClass.DEPOT, ZERO), ZERO)
    crypto = max(amounts.get(PlanClass.CRYPTO, ZERO), ZERO)
    total = depot + crypto
    if total <= 0:
        return None
    first = (HUNDRED * depot / total).quantize(TENTH, rounding=ROUND_HALF_UP)
    return InvestmentSplit(depot=first, crypto=HUNDRED - first)


def _share(split: InvestmentSplit, kind: PlanClass) -> Decimal:
    return split.depot if kind is PlanClass.DEPOT else split.crypto


def _rate(rates: ValueChange, kind: PlanClass) -> Decimal | None:
    return rates.depot if kind is PlanClass.DEPOT else rates.crypto


def rate_todo(missing: Sequence[PlanClass]) -> HealthTodo:
    words = " und ".join(CLASS_WORDS[c] for c in missing)
    return HealthTodo(
        code=HealthTodoCode.RATE_MISSING,
        text=(
            f"Für {words} fehlt eure angenommene Rendite pro Jahr. Ohne sie legt euer Ziel das "
            "Geld über der Reserve nicht an; Hausbuch nimmt keinen Wert an."
        ),
        action="Rendite eintragen",
        href=PLAN_HREF,
    )


def _summed(pairs: Iterable[tuple[PlanClass, Decimal]]) -> dict[PlanClass, Decimal]:
    out = dict.fromkeys(KINDS, ZERO)
    for kind, amount in pairs:
        out[kind] += amount
    return out


def effective_split(
    saved: InvestmentSplit | None,
    plans: Sequence[SavingsPlan],
    held: Iterable[Held],
    rates: ValueChange,
    reserve: AbstractSet[UUID],
) -> EffectiveSplit:
    """§7.11 (a) 2. `plans`: the continuing plans (after the household's overrides). `held`
    is read only when neither the lever nor the plans give a mix."""
    split, source = saved, SplitSource.SAVED
    if split is None:
        split, source = (
            shares(
                _summed(
                    (p.asset_class, p.monthly)
                    for p in plans
                    if p.destination_account_id is None or p.destination_account_id not in reserve
                )
            ),
            SplitSource.SAVINGS_PLANS,
        )
    if split is None:
        split, source = (
            shares(
                _summed(
                    (h.asset_class, h.value)
                    for h in held
                    if h.account_id is None or h.account_id not in reserve
                )
            ),
            SplitSource.HOLDINGS,
        )
    if split is None:
        return EffectiveSplit(source=SplitSource.NONE)
    left_out = [k for k in KINDS if _share(split, k) > 0 and _rate(rates, k) is None]
    if not left_out:
        return EffectiveSplit(source=source, shares=split)
    kept = shares({k: _share(split, k) for k in KINDS if k not in left_out})
    if kept is None:
        return EffectiveSplit(source=source, left_out=left_out, todos=[rate_todo(left_out)])
    return EffectiveSplit(source=source, shares=kept, left_out=left_out)


def bought(
    lines: Iterable[PlanLine], months: Sequence[str], reserve: AbstractSet[UUID]
) -> dict[PlanClass, Decimal]:
    """Σ purchases per kind booked in `months` (positive amounts)."""
    window = set(months)
    pairs: list[tuple[PlanClass, Decimal]] = []
    for line in lines:
        if line.amount >= 0 or line.booked.strftime("%Y-%m") not in window:
            continue
        if line.account_id is not None:
            if line.account_id in reserve or line.account_kind is None:
                continue
            kind = PURCHASE_KINDS.get(line.account_kind)
        else:
            kind = PLAN_CODES.get(line.category) if line.category is not None else None
        if kind is not None:
            pairs.append((kind, -line.amount))
    return _summed(pairs)


def prefill(
    lines: Iterable[PlanLine], months: Sequence[str], reserve: AbstractSet[UUID]
) -> SplitPrefill | None:
    """§7.11 (f) 1; None when nothing was bought in the window."""
    if not months:
        return None
    amounts = bought(lines, months, reserve)
    split = shares(amounts)
    if split is None:
        return None
    return SplitPrefill(from_month=months[0], to_month=months[-1], bought=amounts, split=split)


def not_invested(
    lines: Iterable[PlanLine],
    months: Sequence[str],
    reserve: AbstractSet[UUID],
    saved: InvestmentSplit | None,
    mode: AdviceMode,
) -> NotInvested | None:
    """§7.11 (a) 6; None without a saved split, a purchase or a kind below its share."""
    if saved is None or not months:
        return None
    amounts = bought(lines, months, reserve)
    actual = shares(amounts)
    if actual is None:
        return None
    invested = sum(amounts.values(), ZERO)
    rows: list[NotInvestedLine] = []
    for kind in KINDS:
        target = (invested * _share(saved, kind) / HUNDRED).quantize(CENT, rounding=ROUND_HALF_UP)
        gap = target - amounts[kind]
        if gap > 0:
            rows.append(
                NotInvestedLine(
                    asset_class=kind,
                    target_percent=_share(saved, kind),
                    actual_percent=_share(actual, kind),
                    amount=gap,
                )
            )
    if not rows:
        return None
    return NotInvested(
        from_month=months[0],
        to_month=months[-1],
        invested=invested,
        amount=sum((r.amount for r in rows), ZERO),
        lines=rows if mode is AdviceMode.OWN_USE else [],
    )
