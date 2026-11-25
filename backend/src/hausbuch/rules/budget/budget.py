"""The Budget as a partition of phase 9's household month (docs/phases/phase-10.md D272–D276,
§4). Fixtures: tests/fixtures/phase10/budget_partition.yaml, budget_overrides.yaml,
budget_marks.yaml.

`budget(months, lines, overrides, targets)`: `months` are phase 9's `HouseholdMonth` of the
window (rules/health/month), `lines` the same months' private lines (`MonthLine`, the ones
behind each month). Pure: no I/O, nothing stored.

- **Spending per code** comes from the lines through rules.health.month's `cashflow_rows`
  (D234's recode) and the same split month_cashflow makes (a code outside the taxonomy, or a
  spending or tax code, is spending; the Geldanlage group, income and transfers are not), so
  a code whose month nets to 0 is still a code. phase 6's `spending_by_category` is not used:
  it drops a group whose month totals 0, and its codes with it, while the other lines of the
  month still count (a −5 € fee and a +5 € credit in Sonstiges would leave the fee out).

- **Lines** (D272). Every spending code goes to exactly one line: a one-off code in force
  (`one_off_codes`: the taxonomy's, `hh_car_purchase` and five more since D307, then the
  household's switches, rules.household.taxonomy.one_off_codes) to „Einmalig im Zeitraum“,
  `uncategorized` to „Ohne Einordnung“, every other code to Notwendiges or Wünsche by its
  class: the household's override, else the taxonomy's (`bank_fees`, outside the taxonomy,
  is a need here). An override of a code that may not move (a one-off code in force,
  uncategorized, a code without a class) is ignored, so a row stored before a taxonomy or a
  switch change still reads; saving one is refused by
  services.household_scenarios.check_levers. A refund (`hh_refund`) is a negative amount in
  its class's line.
- **The identity** (D273, amended by D308 on 25 Nov 2026), checked per month and over the
  window: need + want + one-off + uncategorized = A (the month's spending, ValueError when
  the codes do not add up to it); saving = E − (A − one-off), the income less the regular
  spending, so need + want + uncategorized + saving = E and saving ÷ E is
  rules.health.figures' savings rate to the last digit (both are rules.health's `ratio` of
  the same sums). The one-offs stand beside the partition (`BudgetView.one_off`, „Einmalig
  im Zeitraum“): their own monthly mean half-up, their codes apportioned within it.
- **Monthly amounts.** The window's mean of E, to the cent half-up, is apportioned to the
  lines by largest remainder of their exact means (ties in the lines' order), then each line's
  cents to its codes the same way (ties by the code's value), so the shown codes add up to the
  shown line and the shown lines to the shown income. A code's mean may differ by a cent from
  the one spending in play shows (rules/plan/spending.code_means apportions within the regular
  spending instead).
- **Shares** of ΣE, four places half-up; with ΣE ≤ 0 none, and no mark, and `no_shares` says
  why. The housing share (D276): the Wohnen group's codes ÷ ΣE, whatever class they are in.
- **To-do** „Ohne Einordnung“: whenever a line of the window is booked uncategorized,
  counted by lines, even when they net to 0.
- **Marks** (D274): only against a target the household saved; green when met (at or below a
  maximum, at or above the saving minimum, an equal share meets it), amber when missed, never
  red; decided on the share as the sentence prints it (percent, one place, half-up), so a
  share printed as the target meets it. The sentence names the points and the euros a month
  between the share and the target.
- Below three closed months (D231) no figure at all, the window's to-do instead.
"""

from collections.abc import Collection, Hashable, Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from uuid import UUID

from hausbuch.rules.health.bands import format_money, format_multiple, format_percent
from hausbuch.rules.health.month import cashflow_rows
from hausbuch.rules.health.window import MIN_MONTHS, window_todos
from hausbuch.rules.household.taxonomy import ONE_OFF, TAXONOMY
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.budget import (
    BudgetCode,
    BudgetHousing,
    BudgetIncome,
    BudgetLine,
    BudgetLineKey,
    BudgetMark,
    BudgetMarkColour,
    BudgetMonth,
    BudgetReference,
    BudgetTargets,
    BudgetView,
)
from hausbuch.schemas.health import (
    HealthTodo,
    HealthTodoCode,
    HouseholdMonth,
    MonthLine,
    Window,
    ratio,
)
from hausbuch.schemas.household import BudgetClass, HouseholdGroup, Relevance

L = BudgetLineKey
ZERO = Decimal("0.00")
CENT = Decimal("0.01")
TENTH = Decimal("0.1")

# D272: the private codes outside the taxonomy. bank_fees is a need; uncategorized has no
# class and its own line.
OUTSIDE: Mapping[C, BudgetClass] = {C.BANK_FEES: BudgetClass.NEED}
# D276: the household's own home, by group, so a code added to Wohnen later counts too.
HOUSING_CODES = frozenset(c for c, row in TAXONOMY.items() if row.group is HouseholdGroup.HOUSING)

LABELS = {
    L.NEED: "Notwendiges",
    L.WANT: "Wünsche",
    L.ONE_OFF: "Einmalige Ausgaben",
    L.UNCATEGORIZED: "Ohne Einordnung",
    L.SAVING: "Sparen",
}
HOUSING_LABEL = "Wohnen"
NO_SHARES = (
    "Im Zeitraum war euer Einkommen nicht größer als 0 €; daher zeigt das Budget keine Anteile "
    "und keine Markierungen."
)


# --- Classes -------------------------------------------------------------------------------


def default_class(code: C) -> BudgetClass | None:
    """The taxonomy's class (D272), bank_fees' from OUTSIDE; None for a code the Budget
    does not sort into need or want (income, transfers, uncategorized)."""
    row = TAXONOMY.get(code)
    return OUTSIDE.get(code) if row is None else row.budget_class


def movable(code: C, one_off_codes: Collection[C] = ONE_OFF) -> bool:
    """A code a household may move to the other class (D272 (1)): it has a class and is not
    one-off in force (D272 (2); `one_off_codes`, D307)."""
    return default_class(code) is not None and code not in one_off_codes


def refused_overrides(
    overrides: Mapping[C, BudgetClass], one_off_codes: Collection[C] = ONE_OFF
) -> list[C]:
    """The overrides a scenario may not be saved with, sorted by code."""
    return sorted((c for c in overrides if not movable(c, one_off_codes)), key=lambda c: c.value)


def class_of(
    code: C, overrides: Mapping[C, BudgetClass], one_off_codes: Collection[C] = ONE_OFF
) -> BudgetClass | None:
    """The class shown: the override for a movable code, else the default."""
    default = default_class(code)
    if movable(code, one_off_codes):
        return overrides.get(code, default)
    return default


def line_of(
    code: C, overrides: Mapping[C, BudgetClass], one_off_codes: Collection[C] = ONE_OFF
) -> BudgetLineKey:
    if code in one_off_codes:
        return L.ONE_OFF
    if code is C.UNCATEGORIZED:
        return L.UNCATEGORIZED
    budget_class = class_of(code, overrides, one_off_codes)
    if budget_class is None:
        raise ValueError(f"{code} is not spending the Budget can place")
    return L.NEED if budget_class is BudgetClass.NEED else L.WANT


def _group(code: C) -> HouseholdGroup:
    row = TAXONOMY.get(code)
    return HouseholdGroup.OTHER if row is None else row.group


# --- Arithmetic ----------------------------------------------------------------------------


def _half_up_cents(value: Fraction) -> int:
    """value (in cents) to a whole cent, half away from zero, as Decimal's ROUND_HALF_UP."""
    sign = -1 if value < 0 else 1
    magnitude = abs(value)
    whole = magnitude.numerator // magnitude.denominator
    if magnitude - whole >= Fraction(1, 2):
        whole += 1
    return sign * whole


def _apportion[K: Hashable](
    exact: Mapping[K, Fraction], target: int, rank: Mapping[K, int]
) -> dict[K, int]:
    """Largest remainder: each value (in cents) floored, the `target`'s cents left over given
    to the largest remainders, ties by `rank`."""
    floors = {k: v.numerator // v.denominator for k, v in exact.items()}
    left = target - sum(floors.values())
    if not 0 <= left <= len(exact):  # pragma: no cover - target is not a rounding of Σ
        raise ValueError("the target is not a rounding of the values' sum")
    order = sorted(exact, key=lambda k: (-(exact[k] - floors[k]), rank[k]))
    for k in order[:left]:
        floors[k] += 1
    return floors


def _euros(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(CENT)


def _mean(total: Decimal, n: int) -> Decimal:
    return _euros(_half_up_cents(Fraction(total) * 100 / n))


# --- Spending per code ---------------------------------------------------------------------


def is_spending(code: C) -> bool:
    """Whether month_cashflow counts a private row of `code` as spending: a code outside the
    taxonomy (bank_fees, uncategorized) or a spending or tax code outside Geldanlage."""
    row = TAXONOMY.get(code)
    if row is None:
        return True
    if row.group is HouseholdGroup.INVESTMENT:
        return False
    return row.relevance in (Relevance.SPENDING, Relevance.TAX)


def month_codes(month: HouseholdMonth, lines: Sequence[MonthLine]) -> dict[C, Decimal]:
    """The month's spending per code (positive, a refund negative) from its private lines,
    a code that nets to 0 kept; checked against A."""
    codes: dict[C, Decimal] = {}
    for code, amount in cashflow_rows(lines):
        if is_spending(code):
            codes[code] = codes.get(code, ZERO) - amount
    if sum(codes.values(), ZERO) != month.spending:
        raise ValueError(f"{month.month}: the codes do not add up to the month's spending")
    return codes


# --- Marks ---------------------------------------------------------------------------------


def _percent(value: Decimal) -> str:
    return format_percent(value / 100)


def mark(
    label: str,
    share: Decimal | None,
    monthly: Decimal,
    income_monthly: Decimal,
    target: Decimal | None,
    *,
    minimum: bool = False,
) -> BudgetMark | None:
    """D274: green when the target is met, amber when missed; None without a target or a
    share. `minimum`: the saving target, met at or above it; the others at or below. Decided
    at the precision the sentence prints: the share in percent to one place, half-up, so
    50,04 % against „höchstens 50,0 %“ reads 50,0 % and is met."""
    if target is None or share is None:
        return None
    limit = target / 100
    shown = (share * 100).quantize(TENTH, ROUND_HALF_UP)
    met = shown >= target if minimum else shown <= target
    gap_points = (shown - target).quantize(TENTH)
    gap_points = gap_points or abs(gap_points)  # never −0.0
    at_target = (limit * income_monthly).quantize(CENT, ROUND_HALF_UP)
    gap_monthly = monthly - at_target
    bound = "mindestens" if minimum else "höchstens"
    start = f"{label}: {format_percent(share)} eures Einkommens"
    goal = f"eurem Ziel von {bound} {_percent(target)}"
    if shown == target:
        text = f"{start}, genau auf {goal}."
    else:
        points = f"{format_multiple(abs(gap_points))} Prozentpunkte"
        side = "über" if shown > target else "unter"
        money = format_money(abs(gap_monthly))
        if met:
            spare = "mehr" if minimum else "Luft"
            text = f"{start}, {points} {side} {goal} ({money} im Monat {spare})."
        else:
            short = "zu wenig" if minimum else "zu viel"
            text = f"{start}, {points} {side} {goal}; das sind {money} im Monat {short}."
    return BudgetMark(
        colour=BudgetMarkColour.GREEN if met else BudgetMarkColour.AMBER,
        target=target,
        gap_points=gap_points,
        gap_monthly=gap_monthly,
        at_target=at_target,
        text=text,
    )


# --- The Budget ----------------------------------------------------------------------------


def _uncategorized_todo(count: int, total: Decimal) -> HealthTodo:
    words = "eine Zahlung" if count == 1 else f"{count} Zahlungen"
    return HealthTodo(
        code=HealthTodoCode.UNCATEGORIZED_LINES,
        text=(
            f"Im Zeitraum sind {words} ohne Einordnung gebucht, zusammen "
            f"{format_money(total)}; sie zählen weder als Notwendiges noch als Wünsche."
        ),
        action="1 Zahlung einordnen" if count == 1 else f"{count} Zahlungen einordnen",
        href="/household",
    )


def marked_one_off(
    rows: Sequence[MonthLine],
    one_off_ids: Collection[UUID],
    one_off_codes: Collection[C] = ONE_OFF,
) -> dict[C, Decimal]:
    """What the payments marked one-off (D297) moved per code in one month, positive: a
    payment (money out) in a spending code that is not one-off in force, D234's recode
    applied, the same test as rules.health.month's."""
    moved: dict[C, Decimal] = {}
    if not one_off_ids:
        return moved
    for line in rows:
        if line.allocation_id not in one_off_ids or line.amount >= 0:
            continue
        code = cashflow_rows([line])[0][0]
        if is_spending(code) and code not in one_off_codes:
            moved[code] = moved.get(code, ZERO) - line.amount
    return moved


def placed(
    codes: Mapping[C, Decimal],
    rows: Sequence[MonthLine],
    overrides: Mapping[C, BudgetClass],
    one_off_ids: Collection[UUID] = frozenset(),
    one_off_codes: Collection[C] = ONE_OFF,
) -> dict[tuple[BudgetLineKey, C], Decimal]:
    """One month's spending per code (`month_codes`) placed on the Budget's lines (D272):
    each code on its line, a payment marked one-off (D297) moved from its code's line to
    „Einmalig im Zeitraum“. The one placement the Budget and the reserve's need (D305) read."""
    out = {
        (line_of(code, overrides, one_off_codes), code): amount for code, amount in codes.items()
    }
    for code, amount in marked_one_off(rows, one_off_ids, one_off_codes).items():
        regular = (line_of(code, overrides, one_off_codes), code)
        out[regular] = out[regular] - amount
        out[(L.ONE_OFF, code)] = out.get((L.ONE_OFF, code), ZERO) + amount
    return out


def need_spending(
    month: HouseholdMonth,
    rows: Sequence[MonthLine],
    overrides: Mapping[C, BudgetClass],
    *,
    one_off_ids: Collection[UUID] = frozenset(),
    one_off_codes: Collection[C] = ONE_OFF,
) -> dict[C, Decimal]:
    """The month's Notwendiges per code (phase 10 D305: the reserve's need): the codes on
    the need line, the household's overrides applied, one-off codes in force and payments
    marked one-off left out, a refund in a need code netted; a code netting to 0 kept."""
    codes = month_codes(month, rows)
    return {
        code: amount
        for (key, code), amount in placed(
            codes, rows, overrides, one_off_ids, one_off_codes
        ).items()
        if key is L.NEED
    }


def _joined(seen: BudgetCode | None, row: BudgetCode) -> BudgetCode:
    """A code shown on two lines (its marked payments on „Einmalig im Zeitraum“) counts once
    in the housing share: its two rows summed, the class of the line it belongs to."""
    if seen is None:
        return row
    first, other = (seen, row) if seen.budget_class is not None else (row, seen)
    return first.model_copy(
        update={"monthly": first.monthly + other.monthly, "total": first.total + other.total}
    )


def budget(
    months: Sequence[HouseholdMonth],
    lines: Sequence[Sequence[MonthLine]],
    overrides: Mapping[C, BudgetClass],
    targets: BudgetTargets,
    *,
    as_of: date,
    scenario_id: UUID | None = None,
    references: Sequence[BudgetReference] = (),
    one_off_ids: Collection[UUID] = frozenset(),
    one_off_codes: Collection[C] = ONE_OFF,
) -> BudgetView:
    """The Budget over the window `months` (consecutive, oldest first; the closed months
    phase 9's key figures use, D231), `lines` each month's private lines. `one_off_ids`: the
    payments a person marked one-off (phase 10 §7.9 (c) 2, D297; empty by default); each
    moves from its code's line to „Einmalig im Zeitraum“, its code then shown on both, as
    rules.health.month moves it from regular to one-off spending. `one_off_codes`: the codes
    counted as one-off (D307; the taxonomy's by default), the same the months were built
    with. The housing share keeps the one-offs (D276: the Wohnen group's spending, whatever
    line a payment sits on)."""
    win = Window(months=[m.month for m in months], enough=len(months) >= MIN_MONTHS)
    base = {
        "as_of": as_of,
        "scenario_id": scenario_id,
        "window": win,
        "targets": targets,
        "references": list(references),
    }
    if len(lines) != len(months):
        raise ValueError("one list of lines per month")
    per_month = [month_codes(m, rows) for m, rows in zip(months, lines, strict=True)]
    uncategorized_lines = sum(
        1 for rows in lines for code, _ in cashflow_rows(rows) if code is C.UNCATEGORIZED
    )
    if not win.enough:
        return BudgetView(**base, todos=window_todos(win))
    n = len(months)

    # Per month and over the window, exact sums (D273).
    month_rows = []
    totals: dict[tuple[BudgetLineKey, C], Decimal] = {}
    for m, codes, rows in zip(months, per_month, lines, strict=True):
        sums = dict.fromkeys(L, ZERO)
        for (key, code), amount in placed(
            codes, rows, overrides, one_off_ids, one_off_codes
        ).items():
            sums[key] += amount
            totals[(key, code)] = totals.get((key, code), ZERO) + amount
        if sums[L.ONE_OFF] != m.one_off_spending:
            raise ValueError(f"{m.month}: the one-offs do not match the month's (D308)")
        month_rows.append(
            BudgetMonth(
                month=m.month,
                income=m.income,
                need=sums[L.NEED],
                want=sums[L.WANT],
                one_off=sums[L.ONE_OFF],
                uncategorized=sums[L.UNCATEGORIZED],
                saving=m.saved + m.one_off_spending,  # D308: E − the regular spending
            )
        )
    income_total = sum((m.income for m in months), ZERO)
    saved_total = sum((m.saved + m.one_off_spending for m in months), ZERO)
    by_line: dict[BudgetLineKey, dict[C, Decimal]] = {key: {} for key in L}
    for (key, code), total in totals.items():
        if total:
            by_line[key][code] = total
    line_totals = {key: sum(codes.values(), ZERO) for key, codes in by_line.items()}
    line_totals[L.SAVING] = saved_total
    partition = sum((v for k, v in line_totals.items() if k is not L.ONE_OFF), ZERO)
    if partition != income_total:  # pragma: no cover - checked per month
        raise ValueError("the lines do not add up to the income")
    shown = [
        key
        for key in L
        if key in (L.NEED, L.WANT, L.SAVING) or (key is L.UNCATEGORIZED and line_totals[key])
    ]

    # Monthly means: E's mean to the cent, apportioned to the lines, then to their codes.
    income_cents = _half_up_cents(Fraction(income_total) * 100 / n)
    line_cents = _apportion(
        {key: Fraction(line_totals[key]) * 100 / n for key in shown},
        income_cents,
        {key: i for i, key in enumerate(L)},
    )
    # D308: „Einmalig im Zeitraum“ beside the partition, its own mean to the cent half-up
    one_off_shown = line_totals[L.ONE_OFF] != 0
    if one_off_shown:
        line_cents[L.ONE_OFF] = _half_up_cents(Fraction(line_totals[L.ONE_OFF]) * 100 / n)
    rental_monthly = _mean(sum((m.rental_result for m in months), ZERO), n)
    income = BudgetIncome(
        monthly=_euros(income_cents),
        total=income_total,
        rental_monthly=rental_monthly,
        private_monthly=_euros(income_cents) - rental_monthly,
    )
    shares_ok = income_total > 0
    line_targets = {L.NEED: targets.needs_max, L.WANT: targets.wants_max}
    line_targets[L.SAVING] = targets.saving_min
    budget_lines: list[BudgetLine] = []
    one_off_line: BudgetLine | None = None
    all_codes: dict[C, BudgetCode] = {}
    for key in [*shown, *([L.ONE_OFF] if one_off_shown else [])]:
        codes = by_line[key]
        code_cents = (
            {}
            if key is L.SAVING  # E − A: no codes
            else _apportion(
                {c: Fraction(v) * 100 / n for c, v in codes.items()},
                line_cents[key],
                {c: i for i, c in enumerate(sorted(codes, key=lambda c: c.value))},
            )
        )
        rows = []
        for code in sorted(codes, key=lambda c: c.value):
            default = default_class(code)
            shown_class = (
                None
                if key in (L.ONE_OFF, L.UNCATEGORIZED)
                else class_of(code, overrides, one_off_codes)
            )
            row = BudgetCode(
                category=code,
                group=_group(code),
                budget_class=shown_class,
                default_class=default,
                overridden=shown_class is not None and shown_class is not default,
                monthly=_euros(code_cents[code]),
                total=codes[code],
            )
            rows.append(row)
            all_codes[code] = _joined(all_codes.get(code), row)
        share = ratio(line_totals[key], income_total) if shares_ok else None
        target = line_targets.get(key)
        monthly = _euros(line_cents[key])
        if key is L.ONE_OFF:  # beside the partition: no target, no mark
            one_off_line = BudgetLine(
                key=key, monthly=monthly, total=line_totals[key], share=share, codes=rows
            )
            continue
        budget_lines.append(
            BudgetLine(
                key=key,
                monthly=monthly,
                total=line_totals[key],
                share=share,
                codes=rows,
                target=target,
                mark=mark(
                    LABELS[key], share, monthly, income.monthly, target, minimum=key is L.SAVING
                ),
            )
        )

    housing_codes = [all_codes[c] for c in sorted(all_codes, key=lambda c: c.value)]
    housing_codes = [row for row in housing_codes if row.category in HOUSING_CODES]
    housing_total = sum((row.total for row in housing_codes), ZERO)
    housing_monthly = sum((row.monthly for row in housing_codes), ZERO)
    housing_share = ratio(housing_total, income_total) if shares_ok else None
    housing = BudgetHousing(
        monthly=housing_monthly,
        total=housing_total,
        share=housing_share,
        codes=housing_codes,
        target=targets.housing_max,
        mark=mark(
            HOUSING_LABEL, housing_share, housing_monthly, income.monthly, targets.housing_max
        ),
    )
    todos = []
    if uncategorized_lines:
        uncategorized = sum((v for (_, code), v in totals.items() if code is C.UNCATEGORIZED), ZERO)
        todos.append(_uncategorized_todo(uncategorized_lines, uncategorized))
    return BudgetView(
        **base,
        months=month_rows,
        income=income,
        lines=budget_lines,
        one_off=one_off_line,
        housing=housing,
        no_shares=None if shares_ok else NO_SHARES,
        todos=todos,
    )


__all__ = [
    "HOUSING_CODES",
    "OUTSIDE",
    "budget",
    "class_of",
    "default_class",
    "is_spending",
    "line_of",
    "mark",
    "month_codes",
    "movable",
    "refused_overrides",
]
