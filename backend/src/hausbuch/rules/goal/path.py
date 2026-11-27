"""The goal's path (docs/phases/phase-10.md §7.11 (i), D311): a realistic path within what the
household can save, adopted and tracked. Pure, Decimal; pinned by
tests/fixtures/phase10/goal_path.yaml. rules/goal/goal.py walks the steps (it owns the walk);
this module holds the arithmetic around them:

1. The ceiling (item 1) = Anlegbar's free cash with the wants cut to the wants kept, less the
   buffer kept from it as phase 9 keeps it from a free month: free = income − regular spending
   + rental cash + max(0, wants net − wants kept). The wants net is the want class's net mean,
   every want code with a refund (`hh_refund`) netted, as the Budget's Wünsche line counts it
   (rules/budget, D273); the cut, when there is one, is shared over the positive wants (3.).
   So spending outside the need and the wants (an uncategorised line) counts as Anlegbar
   counts it; the row `other_spending` holds it, signed, so income + max(rental cash, 0) −
   need − wants kept − other spending − buffer = ceiling. Wants kept = min(wants net, wants
   target × income), else, with no wants target saved, the suggestion: half of each positive
   want's mean, rounded down to the cent, at most the wants net; never below 0. Anlegbar =
   free − buffer, free = income − regular spending + rental cash. Room = max(0, ceiling −
   Anlegbar).
2. The state from the steps: on_track, doable_rates (an extra within the room at the typed
   rates), else step 2 (own use, a net-worth goal): the return needed with the whole room,
   reached in at least half the windows doable_return, in some but under half ambitious, in
   none, out of range or without a history hardly.
3. The wants budget the path stores: its extra grossed up for a buffer (÷ (1 − b), up to the
   cent, + 0.01 when b > 0), at most wants net − wants kept, shared by the positive wants'
   means, each up to the cent; its total the wants net less the cuts.
4. The tracking rows since the day adopted (item 6, 12) and whether the path's inputs moved.

Nothing here proposes a goal, a rate or a product (D247, D289).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from hausbuch.rules.plan.surplus import ZERO, buffer_of
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.goal import (
    GoalBudget,
    GoalPathAction,
    GoalPathActionKind,
    GoalPathCeiling,
    GoalPathPoint,
    GoalPathRow,
    GoalTrackingWord,
)
from hausbuch.schemas.plan import (
    GoalPathLever,
    GoalPathState,
    HouseholdLevers,
    InvestmentSplit,
    ValueChange,
)

CENT = Decimal("0.01")
RATE_PLACES = Decimal("0.001")
PERCENT_PLACES = Decimal("0.01")


def ceiling_of(
    income: Decimal,
    regular: Decimal,
    rental_cash: Decimal,
    need: Decimal,
    wants: Mapping[AllocationCategory, Decimal],
    wants_net: Decimal,
    wants_max: Decimal | None,
    buffer_percent: Decimal,
) -> GoalPathCeiling:
    """`regular`: the mean regular spending Anlegbar reads; `need`: the reserve's need, which
    holds the flat's shortfall when the rental cash is negative; `wants`: the want codes'
    positive means (the suggestion's halves); `wants_net`: the want class's net mean, every
    want code with its refunds (the Budget's Wünsche)."""
    kept = wants_kept(income, wants, wants_net, wants_max)
    rental = max(rental_cash, ZERO)
    free = income - regular + rental_cash + cuttable(wants_net, kept)
    buffer = buffer_of(free, buffer_percent)
    return GoalPathCeiling(
        income=income,
        rental_cash=rental,
        need=need,
        wants_kept=kept,
        other_spending=income + rental - need - kept - free,
        suggested=wants_max is None,
        buffer=buffer,
        ceiling=free - buffer,
    )


def wants_kept(
    income: Decimal,
    wants: Mapping[AllocationCategory, Decimal],
    wants_net: Decimal,
    wants_max: Decimal | None,
) -> Decimal:
    """The wants a plan keeps: the household's wants target of the income, else the
    suggestion (half of each want's positive mean, rounded down to the cent), never more than
    the want class's net mean (never below 0)."""
    net = max(wants_net, ZERO)
    if wants_max is None:
        suggested = sum((_floor_cent(Fraction(m) / 2) for m in wants.values()), ZERO)
        return min(suggested, net)
    return min(net, (wants_max * income / 100).quantize(CENT, ROUND_HALF_UP))


def cuttable(wants_net: Decimal, kept: Decimal) -> Decimal:
    """What the wants can give up: the wants net above the wants kept, never below 0."""
    return max(ZERO, wants_net - kept)


def anlegbar_of(
    income: Decimal, regular: Decimal, rental_cash: Decimal, buffer_percent: Decimal
) -> Decimal:
    free = income - regular + rental_cash
    return free - buffer_of(free, buffer_percent)


def room_of(ceiling: GoalPathCeiling, anlegbar: Decimal) -> Decimal:
    return max(ZERO, ceiling.ceiling - anlegbar)


def typed_rate(levers: HouseholdLevers, split: InvestmentSplit | None) -> Decimal | None:
    """The typed rates weighted by the effective split (a rate not typed counts 0: its class
    is kept at its start); None without a split."""
    if split is None:
        return None
    vc = levers.value_change
    depot = vc.depot if vc.depot is not None else ZERO
    crypto = vc.crypto if vc.crypto is not None else ZERO
    rate = (split.depot * depot + split.crypto * crypto) / 100
    return rate.quantize(RATE_PLACES, ROUND_HALF_UP)


def step_2_state(reached: int | None, windows: int | None) -> GoalPathState:
    """Step 2 from route 3's count: reached × 2 ≥ windows > 0 doable_return, some but under
    half ambitious, none (or no count) hardly."""
    if reached is None or not windows or reached == 0:
        return GoalPathState.HARDLY
    if reached * 2 >= windows:
        return GoalPathState.DOABLE_RETURN
    return GoalPathState.AMBITIOUS


def wants_cuts(
    wants: Mapping[AllocationCategory, Decimal],
    cut_at_most: Decimal,
    extra: Decimal,
    buffer_percent: Decimal,
) -> dict[AllocationCategory, Decimal]:
    """Item 3: the cut per positive want for the path's extra, at most `cut_at_most`
    (`cuttable`: the wants net above the wants kept); empty when nothing is cut."""
    total = sum(wants.values(), ZERO)
    if extra <= 0 or total <= 0:
        return {}
    wanted = extra
    if buffer_percent > 0:
        wanted = _ceil_cent(Fraction(extra) / (1 - Fraction(buffer_percent) / 100)) + CENT
    wanted = min(wanted, max(ZERO, cut_at_most))
    if wanted <= 0:
        return {}
    cuts = {}
    for code, mean in wants.items():
        cut = min(mean, _ceil_cent(Fraction(mean) * Fraction(wanted) / Fraction(total)))
        if cut > 0:
            cuts[code] = cut
    return cuts


def moved(
    lever: GoalPathLever,
    goal,
    value_change: ValueChange,
    wants_max: Decimal | None,
    anlegbar: Decimal,
    investment_split: InvestmentSplit | None,
) -> bool:
    """Whether what the adopted path was computed from has moved: the goal, the typed rates,
    the wants target or the investment split lever changed (by value: two splits of the same
    percentages are equal however written, None equals None), or „Anlegbar“ moved by more
    than the room of that day or 5 % of the path's monthly amount, whichever is larger
    (Q-O192)."""
    if lever.goal != goal or lever.value_change != value_change or lever.wants_max != wants_max:
        return True
    if _split_key(lever.investment_split) != _split_key(investment_split):
        return True
    tolerance = max(lever.room, lever.monthly * MOVED_SHARE)
    return abs(anlegbar - lever.anlegbar) > tolerance


MOVED_SHARE = Decimal("0.05")  # Q-O192: 5 % of the path's monthly amount


def _split_key(split: InvestmentSplit | None) -> tuple[Decimal, Decimal] | None:
    return None if split is None else (split.depot.normalize(), split.crypto.normalize())


# --- Tracking (item 6, 12) --------------------------------------------------------------------


@dataclass(frozen=True)
class PathMonth:
    """One closed month-end since the day adopted: the month's saved, invested (the purchases)
    and crypto bought, its wants, the net worth and the path's walk at that month-end, and the
    counted accounts' value now with every flow since the day adopted."""

    month_end: date
    saved: Decimal
    principal: Decimal  # the month's loan repayment (HouseholdMonth.principal, saved in S)
    invested_property: Decimal  # paid into the property (HouseholdMonth's, D234, signed)
    invested: Decimal
    crypto_bought: Decimal
    wants: Decimal
    net_worth: Decimal
    path_net_worth: Decimal | None
    value_now: Decimal
    flows: Sequence[tuple[date, Decimal]]
    # §7.11 (k): the purchases and the crypto bought after the day adopted, which alone can pay
    # „Jetzt anlegen“ (the amount adopted is net of what was bought before it); None: the
    # month's figures, as in every month after the one adopted in
    invested_since: Decimal | None = None
    crypto_since: Decimal | None = None


def above_target_split(
    amount: Decimal | None, split: InvestmentSplit | None, target: Decimal | None
) -> tuple[Decimal, Decimal] | None:
    """„Jetzt anlegen“ on the path's plan card: the money above the reserve target today,
    which the walk invests in its first month at the split it invests in (rules/plan/
    projection.py: only with a split and a target). Funds = amount × depot share to the cent,
    half-up as the server rounds money; crypto the rest, so the two add up to the amount.
    None when the walk invests nothing of it (no amount above 0, no split or no target)."""
    if amount is None or amount <= 0 or split is None or target is None:
        return None
    depot = (amount * split.depot / 100).quantize(CENT, ROUND_HALF_UP)
    return depot, amount - depot


def monthly_split(
    monthly: Decimal | None,
    split: InvestmentSplit | None,
    target: Decimal | None,
    plans_depot: Decimal,
    plans_crypto: Decimal,
) -> tuple[Decimal, Decimal] | None:
    """„Jeden Monat X sparen und anlegen, davon …“: the walk pays each savings plan into its
    own class and moves the rest of the month's saving at the split once the reserve is at its
    target (rules/plan/projection.py). Funds = the funds plans + the rest × depot share, to the
    cent half-up; crypto the rest of the amount, so the two add up. None when the walk moves
    nothing at the split (no split or no target), the amount is not above 0, or the plans
    alone take more than it."""
    if monthly is None or monthly <= 0 or split is None or target is None:
        return None
    rest = monthly - plans_depot - plans_crypto
    if rest < 0:
        return None
    depot = (plans_depot + rest * split.depot / 100).quantize(CENT, ROUND_HALF_UP)
    return depot, monthly - depot


@dataclass(frozen=True)
class PlanFigures:
    """What „Was ihr tun müsst“ reads (rules/goal/goal.py `_plan_path` fills it). `monthly`:
    the plan's saving a month (on a path its monthly amount, without one Anlegbar + the whole
    room the why and the return needed are computed with); `cuts`: each want the plan cuts;
    `wants_net`: the want class's net mean; `target`: the reserve target, `split` the walk's
    (None for a goal of a sum or the reserve: nothing moves out of it); `reserve_start` the
    reserve the walk starts from; `adopted_budget` on track with the household's adopted
    spending budget, which the walk then counts on."""

    monthly: Decimal | None
    split: InvestmentSplit | None
    target: Decimal | None
    plans_depot: Decimal
    plans_crypto: Decimal
    above_target: Decimal | None
    cuts: Sequence[GoalBudget]
    wants_net: Decimal
    ceiling: GoalPathCeiling
    wants_max: Decimal | None
    return_needed: Decimal | None
    reserve_start: Decimal | None = None
    adopted_budget: bool = False


def actions_of(f: PlanFigures) -> list[GoalPathAction]:
    """„Was ihr tun müsst“, in order, each only when its figure exists: save a month (with its
    funds and crypto parts once the reserve is at its target: below it the walk fills the
    reserve first), invest the money above the reserve target now, keep (or fill) the reserve
    at its target (both only when the walk invests above it), one row per want cut or one row
    that nothing is cut, and the return needed (step 2 only)."""
    out: list[GoalPathAction] = []
    full = f.target is not None and f.reserve_start is not None and f.reserve_start >= f.target
    if f.monthly is not None and f.monthly > 0:
        parts = None
        if full:
            parts = monthly_split(f.monthly, f.split, f.target, f.plans_depot, f.plans_crypto)
        out.append(_action(GoalPathActionKind.SAVE_MONTHLY, f.monthly, parts))
    invests = f.split is not None and f.target is not None
    now = above_target_split(f.above_target, f.split, f.target)
    if now is not None:
        out.append(_action(GoalPathActionKind.INVEST_NOW, f.above_target, now))
    if invests:
        assert f.target is not None
        reserve = f.target.quantize(CENT, ROUND_HALF_UP)
        out.append(
            GoalPathAction(kind=GoalPathActionKind.KEEP_RESERVE, amount=reserve, within_target=full)
        )
    if f.cuts:
        out += [
            GoalPathAction(
                kind=GoalPathActionKind.WANTS_CUT, amount=b.budget, code=b.code, mean=b.mean
            )
            for b in f.cuts
        ]
    else:
        net = max(f.wants_net, ZERO)
        out.append(
            GoalPathAction(
                kind=GoalPathActionKind.WANTS_NONE,
                amount=net,
                within_target=net <= f.ceiling.wants_kept,
                wants_percent=f.wants_max,
                adopted_budget=f.adopted_budget,
            )
        )
    if f.return_needed is not None:
        out.append(GoalPathAction(kind=GoalPathActionKind.RETURN_NEEDED, rate=f.return_needed))
    return out


def _action(
    kind: GoalPathActionKind, amount: Decimal | None, parts: tuple[Decimal, Decimal] | None
) -> GoalPathAction:
    return GoalPathAction(
        kind=kind,
        amount=amount,
        amount_depot=None if parts is None else parts[0],
        amount_crypto=None if parts is None else parts[1],
    )


def track(
    day: date,
    monthly: Decimal,
    wants_budget: Decimal,
    rate: Decimal | None,
    crypto_percent: Decimal | None,
    value_then: Decimal,
    months: Sequence[PathMonth],
    now: tuple[Decimal, Decimal] | None = None,
) -> tuple[list[GoalPathRow], list[GoalPathPoint]]:
    """`now`: „Jetzt anlegen“ as adopted, (the amount, its crypto part), or None (§7.11 (k)).
    It is planned on top of the monthly amount in the first row and, as far as it is not yet
    bought, in every row after it, so a lump invested in a later month still counts. A month's
    purchases pay the month's own amount first, and only what was bought beyond it (after the
    day adopted, in the month adopted) pays what is open of the lump, its crypto part from the
    crypto bought beyond the month's crypto share: ordinary monthly investing never uses the
    lump up, and the monthly crypto share bought never reads as waiting."""
    from .tracker import implied  # tracker imports goal, which imports this module

    rows: list[GoalPathRow] = []
    points: list[GoalPathPoint] = []
    open_total, open_crypto = now if now is not None and now[0] > 0 else (ZERO, ZERO)
    open_crypto = min(open_crypto, open_total)
    crypto_share = ZERO if crypto_percent is None else monthly * crypto_percent / 100
    for m in months:
        invest_now = open_total if open_total > 0 else None
        since = m.invested if m.invested_since is None else m.invested_since
        crypto_since = m.crypto_bought if m.crypto_since is None else m.crypto_since
        beyond = max(ZERO, min(since, m.invested - monthly))
        to_now = min(open_total, beyond)
        crypto_beyond = max(ZERO, min(crypto_since, m.crypto_bought - crypto_share))
        crypto_to_now = min(open_crypto, crypto_beyond, to_now)
        open_total -= to_now
        open_crypto = min(open_crypto - crypto_to_now, open_total)
        invested_month = m.invested - to_now
        waiting = None
        if crypto_percent is not None:
            bought = m.crypto_bought - crypto_to_now
            share = max(ZERO, (monthly * crypto_percent / 100) - bought)
            # What is left of the month's saving once the loan's repayment, the payments into
            # the property and the purchases are taken out (Q-O194): never below 0. A month
            # whose loan payout exceeds what the property took (D234's figure below 0) frees
            # none of the saving: the payout is no saving, so it counts as 0 here.
            into_property = max(ZERO, m.invested_property)
            left = m.saved - m.principal - into_property - invested_month
            waiting = min(share, max(ZERO, left)).quantize(CENT, ROUND_HALF_UP)
        # the month's own amount short (less what waits in the saving), plus the lump still open
        own_short = max(ZERO, monthly - invested_month - (waiting or ZERO))
        invested_gap = _gap(own_short + open_total)
        wants_gap = _gap(m.wants - wants_budget)
        nw_gap = None if m.path_net_worth is None else _gap(m.path_net_worth - m.net_worth)
        paid = sum((a for _, a in m.flows), ZERO)
        change = m.value_now - value_then - paid
        needed = implied(rate or ZERO, [(day, value_then), *m.flows], m.month_end)
        base = value_then + paid
        actual_pct = _pct(change, base)
        needed_pct = _pct(needed, base)
        behind = change < needed
        word = (
            GoalTrackingWord.BEHIND
            if behind or invested_gap or wants_gap or nw_gap
            else GoalTrackingWord.ON_PATH
        )
        rows.append(
            GoalPathRow(
                month_end=m.month_end,
                word=word,
                planned=monthly,
                invest_now=invest_now,
                invested=m.invested,
                waiting=waiting,
                invested_gap=invested_gap,
                wants=m.wants,
                wants_budget=wants_budget,
                wants_gap=wants_gap,
                net_worth=m.net_worth,
                path_net_worth=m.path_net_worth,
                net_worth_gap=nw_gap,
                value_change=change,
                implied_change=needed,
                actual_percent=actual_pct,
                needed_percent=needed_pct,
                value_behind=behind,
            )
        )
        points.append(
            GoalPathPoint(
                month_end=m.month_end, needed=needed_pct, actual=actual_pct, behind=behind
            )
        )
    return rows, points


def _gap(short: Decimal) -> Decimal | None:
    return short.quantize(CENT, ROUND_HALF_UP) if short > 0 else None


def _pct(amount: Decimal, base: Decimal) -> Decimal | None:
    if base <= 0:
        return None
    return (amount * 100 / base).quantize(PERCENT_PLACES, ROUND_HALF_UP)


def _ceil_cent(value: Fraction) -> Decimal:
    return (Decimal(math.ceil(value * 100)) / 100).quantize(CENT)


def _floor_cent(value: Fraction) -> Decimal:
    return (Decimal(math.floor(value * 100)) / 100).quantize(CENT)


__all__ = [
    "PathMonth",
    "PlanFigures",
    "above_target_split",
    "actions_of",
    "anlegbar_of",
    "ceiling_of",
    "cuttable",
    "wants_kept",
    "monthly_split",
    "moved",
    "room_of",
    "step_2_state",
    "track",
    "typed_rate",
    "wants_cuts",
]
