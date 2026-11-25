"""rules/budget against tests/fixtures/phase10/ (docs/phases/phase-10.md D272–D276, §4), the D273
identity on its own, the stored levers, and rule_version() unchanged by phase 10."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import fixture_models_phase10 as phase10
import pytest

from hausbuch.rules.budget import budget as B
from hausbuch.rules.budget.references import fill, reference, references
from hausbuch.rules.health.figures import savings_rate
from hausbuch.rules.health.month import cashflow_rows
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.rules.household.taxonomy import ONE_OFF, SWITCHABLE, TAXONOMY, one_off_codes
from hausbuch.rules.plan import spending as SP
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.budget import BudgetLineKey as L
from hausbuch.schemas.budget import BudgetTargets
from hausbuch.schemas.health import HealthTodoCode
from hausbuch.schemas.household import BudgetClass
from hausbuch.schemas.plan import HouseholdLevers
from hausbuch.services.health import rule_version

AS_OF = date(2027, 6, 15)
BUDGET_FILES = ("budget_partition", "budget_overrides", "budget_marks", "one_off_switches")
CASES = [(name, case) for name in BUDGET_FILES for case in phase10.load(name).cases]

# rule_version(): the hash of rules/health and rules/plan, stored on every health_snapshot.
# Phase 10 steps 0-4 and waves 1-2 left it at main's before phase 10 (commit 052ce9b9,
# 1a81f628...f9784; D271, D275). Wave 3 changes it once, on purpose (§7.9 (c) 2, Q-O183 (a)):
# a payment marked one-off moves to one-off spending in rules/health/month and
# rules/plan/spending. A snapshot already stored keeps its old version and is never rewritten
# (D242); only a month stored from now on carries this one. §7.12 (b), (c) changes it once
# more (D307, D308: the one-off codes per household and the Sparquote without them; the
# one-off sums read from the month's lines, so a group netting to 0 keeps its one-off).
MAIN_RULE_VERSION = "1a81f62877cf958eca3c22b40e99c3ba14a5862844d95ed1e0fe983fe1cf9784"
WAVE3_RULE_VERSION = "3d24e195585146a011f8ec677ef054a8d19cccd92998db4b178c357317f4551d"
ONE_OFF_RULE_VERSION = "19e42b2973de12a8fc88b5186f811b2939d19bab14168566651054e806ddc9b2"
# §7.11 (g), (f) 1 changes it once more (D305: the reserve's need from need-class spending,
# placed by rules/budget.need_spending; D304: a depot the lever adds counts in the reserve).
# rules/budget is outside the hash, so a change of the placement alone would not move it.
NEED_RULE_VERSION = "2b975321a70b2f6db315190b54d89f696e96b793c6f4093ac5363cadd26e9ed0"


def _view(case: phase10.BudgetCase):
    codes = case.codes()  # D307: the taxonomy's one-off codes, then the case's switches
    built = [phase10.household(m, case.id, codes) for m in case.months]
    marked = frozenset(
        b[1][i].allocation_id for m, b in zip(case.months, built, strict=True) for i in m.marked
    )
    return B.budget(
        [b[0] for b in built],
        [b[1] for b in built],
        case.overrides,
        case.targets,
        as_of=AS_OF,
        one_off_ids=marked,
        one_off_codes=codes,
    ), [b[0] for b in built]


def _same_line(got, want) -> None:
    assert (got.monthly, got.total, got.share) == (want.monthly, want.total, want.share)
    assert got.target == want.target
    assert {c.category: c for c in got.codes}.keys() == want.codes.keys()
    for code in got.codes:
        w = want.codes[code.category]
        assert (code.monthly, code.total) == (w.monthly, w.total), code.category
        assert code.budget_class == w.budget_class, code.category
        assert code.overridden is w.overridden, code.category


@pytest.mark.parametrize(("name", "case"), CASES, ids=[f"{n}:{c.id}" for n, c in CASES])
def test_budget_fixture(name: str, case: phase10.BudgetCase) -> None:
    view, months = _view(case)
    exp = case.expect
    assert view.window.months == exp.window
    assert view.window.enough is exp.enough
    assert [t.code for t in view.todos] == exp.todos
    assert view.months == exp.months
    if exp.income is None:
        assert view.income is None and not view.lines and view.housing is None
        return
    assert view.income is not None
    assert view.income.monthly == exp.income.monthly
    assert view.income.total == exp.income.total
    assert view.income.rental_monthly == exp.income.rental_monthly
    assert view.income.private_monthly == exp.income.private_monthly
    assert (view.no_shares is not None) is exp.no_shares
    assert [li.key for li in view.lines] == [li.key for li in exp.lines]
    # D308: „Einmalig im Zeitraum“ beside the partition, no target and no mark
    assert (view.one_off is None) is (exp.one_off is None)
    if view.one_off is not None:
        _same_line(view.one_off, exp.one_off)
        assert view.one_off.mark is None
    for got, want in zip(view.lines, exp.lines, strict=True):
        _same_line(got, want)
        if want.mark is None:
            assert got.mark is None
        else:
            assert got.mark is not None
            assert got.mark.colour == want.mark.colour
            assert got.mark.gap_points == want.mark.gap_points
            assert got.mark.gap_monthly == want.mark.gap_monthly
            assert got.mark.text == want.mark.text
            assert got.mark.at_target == got.monthly - got.mark.gap_monthly  # Haushalt (§7.6)
    h, wh = view.housing, exp.housing
    assert h is not None and wh is not None
    assert (h.monthly, h.total, h.share, h.target) == (wh.monthly, wh.total, wh.share, wh.target)
    assert [c.category for c in h.codes] == wh.codes
    in_lines: dict = {}
    for li in [*view.lines, *([view.one_off] if view.one_off else [])]:
        for c in li.codes:  # a code on two lines (a marked payment) counts once, summed
            m, t = in_lines.get(c.category, (Decimal("0.00"), Decimal("0.00")))
            in_lines[c.category] = (m + c.monthly, t + c.total)
    for code in h.codes:  # the same mean as in its lines (D276)
        assert (code.monthly, code.total) == in_lines[code.category]
    assert (h.mark is None) is (wh.mark is None)
    if h.mark is not None:
        assert (h.mark.colour, h.mark.gap_points, h.mark.gap_monthly, h.mark.text) == (
            wh.mark.colour,
            wh.mark.gap_points,
            wh.mark.gap_monthly,
            wh.mark.text,
        )
    # D273, D308: Sparen ÷ E is phase 9's Sparquote, to the last digit, one-offs left out.
    saving = next(li for li in view.lines if li.key is L.SAVING)
    assert saving.share == savings_rate(months).rate == exp.savings_rate


def test_no_mark_is_ever_red() -> None:
    for _, case in CASES:
        view, _ = _view(case)
        marks = [li.mark for li in view.lines] + [view.housing.mark if view.housing else None]
        assert {m.colour.value for m in marks if m is not None} <= {"green", "amber"}


def test_no_mark_without_a_target() -> None:
    case = next(c for c in phase10.load("budget_marks").cases if c.id == "targets_met")
    view, _ = _view(case.model_copy(update={"targets": BudgetTargets()}))
    assert all(li.mark is None for li in view.lines)
    assert view.housing is not None and view.housing.mark is None


@pytest.mark.parametrize("case", phase10.load("budget_partition").cases, ids=lambda c: c.id)
def test_identity_per_month_matches_phase9(case: phase10.BudgetCase) -> None:
    """Per month: need + want + one-off + uncategorized = phase 9's spending A, one-off is
    phase 9's one-off spending and saving is E − the regular spending (D308), whatever the
    class overrides."""
    built = [phase10.household(m, case.id) for m in case.months]
    for overrides in ({}, {C.HH_RESTAURANT: BudgetClass.NEED, C.HH_ENERGY: BudgetClass.WANT}):
        view = B.budget(
            [b[0] for b in built], [b[1] for b in built], overrides, BudgetTargets(), as_of=AS_OF
        )
        for row, (month, _) in zip(view.months, built, strict=True):
            assert row.need + row.want + row.one_off + row.uncategorized == month.spending
            assert row.one_off == month.one_off_spending
            assert row.saving == month.saved + month.one_off_spending
            assert row.saving == month.income - month.regular_spending


def test_too_few_months_still_checks_the_lines() -> None:
    case = phase10.load("budget_partition").cases[0]
    built = [phase10.household(m, case.id) for m in case.months]
    with pytest.raises(ValueError, match="one list of lines per month"):
        B.budget([b[0] for b in built], [built[0][1]], {}, BudgetTargets(), as_of=AS_OF)


# --- the classes (D272) -----------------------------------------------------------------


def test_every_spending_and_tax_code_has_a_class() -> None:
    for code, row in TAXONOMY.items():
        assert (row.budget_class is not None) == (row.relevance.value in ("spending", "tax")), code


def test_q_o176_answers() -> None:
    need, want = BudgetClass.NEED, BudgetClass.WANT
    assert TAXONOMY[C.HH_PERSONAL_CARE].budget_class is need
    assert TAXONOMY[C.HH_FAMILY_TRANSFERS].budget_class is need
    assert TAXONOMY[C.HH_CASH].budget_class is want
    assert TAXONOMY[C.HH_CREDIT_CARD].budget_class is want
    assert TAXONOMY[C.HH_REFUND].budget_class is want
    assert B.default_class(C.BANK_FEES) is need  # outside the taxonomy


def test_refused_overrides() -> None:
    assert B.refused_overrides({C.HH_RESTAURANT: BudgetClass.NEED}) == []
    one_off = next(iter(ONE_OFF))
    refused = B.refused_overrides(
        {
            one_off: BudgetClass.WANT,
            C.UNCATEGORIZED: BudgetClass.NEED,
            C.HH_SALARY: BudgetClass.NEED,
        }
    )
    assert refused == sorted([one_off, C.UNCATEGORIZED, C.HH_SALARY], key=lambda c: c.value)


# --- the references (D275, D276) --------------------------------------------------------


def test_references_file() -> None:
    fixture = phase10.load("budget_references")
    _, rows = references()
    assert [(r.key, r.kind.value) for r in rows] == [(r.key, r.kind) for r in fixture.references]


@pytest.mark.parametrize("case", phase10.load("budget_references").cases, ids=lambda c: c.id)
def test_fill(case) -> None:
    ref = reference(case.reference)
    if case.expect.refused:
        with pytest.raises(ValueError):
            fill(ref, case.form)
        return
    assert fill(ref, case.form) == case.expect.targets


def test_references_do_not_touch_the_rule_version() -> None:
    """D271, D275: the Budget adds no file under rules/health or rules/plan; the hash moved
    only with wave 3's one-off marks (§7.9 (c) 2), §7.12's one-offs by category (D307,
    D308) and §7.11 (g)'s need from needs (D305), pinned so any other change shows."""
    assert rule_version() == NEED_RULE_VERSION
    versions = {NEED_RULE_VERSION, ONE_OFF_RULE_VERSION, WAVE3_RULE_VERSION, MAIN_RULE_VERSION}
    assert len(versions) == 4
    root = Path(B.__file__).resolve().parents[1]
    assert not list((root / "health").glob("*budget*"))
    assert not list((root / "plan").glob("*budget*"))


# --- the stored levers -----------------------------------------------------------------


def test_stored_levers_without_the_new_fields_still_read() -> None:
    """A household_scenario row stored before phase 10 has no budget fields: it reads with
    empty targets and no overrides."""
    levers = HouseholdLevers.model_validate({})
    assert levers.budget_targets == BudgetTargets()
    assert levers.budget_class_overrides == {}
    # A full row as phase 9 wrote it: every lever but the two phase 10 adds.
    stored = HouseholdLevers().model_dump(mode="json")
    del stored["budget_targets"], stored["budget_class_overrides"]
    assert HouseholdLevers.model_validate(stored) == HouseholdLevers()


def test_levers_round_trip_the_budget_fields() -> None:
    levers = HouseholdLevers.model_validate(
        {
            "budget_targets": {"needs_max": "50.0", "housing_max": "30"},
            "budget_class_overrides": {"hh_restaurant": "need"},
        }
    )
    again = HouseholdLevers.model_validate(levers.model_dump(mode="json"))
    assert again == levers
    assert again.budget_targets.needs_max == Decimal("50.0")
    assert again.budget_class_overrides == {C.HH_RESTAURANT: BudgetClass.NEED}


def test_an_override_carried_unaltered_is_not_refused(monkeypatch) -> None:
    """D272 (2), as phase 9's carried spending change: a save refuses an override new or
    altered against the row it supersedes, never one carried as stored, so a code that stops
    being movable (here: pretended) cannot block saving the other levers."""
    from hausbuch.services import household_scenarios as HS
    from hausbuch.services.errors import RuleViolationError

    def pretend(overrides, *_):  # HH_RESTAURANT can no longer move
        return [c for c in overrides if c is C.HH_RESTAURANT]

    monkeypatch.setattr(HS, "refused_overrides", pretend)
    stored = {C.HH_RESTAURANT: BudgetClass.NEED}
    levers = HouseholdLevers(budget_class_overrides=stored)
    HS._check_budget_classes(levers, stored)  # carried unaltered
    for previous in ({}, {C.HH_RESTAURANT: BudgetClass.WANT}):  # new, altered
        with pytest.raises(RuleViolationError):
            HS._check_budget_classes(levers, previous)


# --- a group that nets to 0 in a month keeps its codes (review of PR #490) ---------------


def _three_months(extra: dict[int, list[tuple[C, str]]]):
    """Three months of salary 3,000 and rent 1,000, `extra` bookings (signed as on the
    account) added to the month of that index."""
    months = []
    for i, month in enumerate(("2027-01", "2027-02", "2027-03")):
        private = [
            phase10.Booking(category=C.HH_SALARY, amount=Decimal("3000.00")),
            phase10.Booking(category=C.HH_RENT, amount=Decimal("-1000.00")),
        ]
        private += [phase10.Booking(category=c, amount=Decimal(a)) for c, a in extra.get(i, [])]
        months.append(phase10.MonthInput(month=month, private=private))
    built = [phase10.household(m, "netting") for m in months]
    return B.budget([b[0] for b in built], [b[1] for b in built], {}, BudgetTargets(), as_of=AS_OF)


def _totals(view) -> dict:
    return {li.key: li.total for li in view.lines}


def test_a_fee_and_a_credit_netting_to_zero_in_sonstiges() -> None:
    view = _three_months({1: [(C.BANK_FEES, "-5.00"), (C.HH_OTHER, "5.00")]})
    totals = _totals(view)
    assert totals[L.NEED] == Decimal("3005.00")
    assert totals[L.WANT] == Decimal("-5.00")
    assert totals[L.SAVING] == Decimal("6000.00")
    assert sum(totals.values()) == view.income.total == Decimal("9000.00")
    assert view.months[1].need == Decimal("1005.00") and view.months[1].want == Decimal("-5.00")
    assert not view.todos


def test_an_uncategorized_line_netting_to_zero_in_sonstiges() -> None:
    view = _three_months({1: [(C.UNCATEGORIZED, "-10.00"), (C.HH_OTHER, "10.00")]})
    totals = _totals(view)
    assert totals[L.UNCATEGORIZED] == Decimal("10.00")
    assert totals[L.WANT] == Decimal("-10.00")
    assert totals[L.NEED] == Decimal("3000.00")
    assert [t.code for t in view.todos] == [HealthTodoCode.UNCATEGORIZED_LINES]


def test_a_need_and_a_want_netting_to_zero_in_mobilitaet() -> None:
    view = _three_months({1: [(C.HH_TAXI, "-30.00"), (C.HH_FUEL, "30.00")]})
    totals = _totals(view)
    assert totals[L.NEED] == Decimal("2970.00")
    assert totals[L.WANT] == Decimal("30.00")
    assert sum(totals.values()) == view.income.total


def test_uncategorized_lines_netting_to_zero_over_the_window_keep_the_todo() -> None:
    view = _three_months({0: [(C.UNCATEGORIZED, "-10.00")], 2: [(C.UNCATEGORIZED, "10.00")]})
    assert L.UNCATEGORIZED not in _totals(view)
    (todo,) = view.todos
    assert todo.code is HealthTodoCode.UNCATEGORIZED_LINES
    assert todo.action == "2 Zahlungen einordnen"


# --- a one-off payment whose group nets to 0 in its month (review of §7.12) ---------------


def _netting_one_off(extra: list[tuple[C, str]], switches: dict[C, bool] | None = None):
    """Three months of salary 3,000 and rent 1,000, `extra` (signed as on the account) in the
    second, the one-off codes the taxonomy's with `switches`: the months, their lines, the
    Budget and the codes in force."""
    codes = one_off_codes(switches)
    months = []
    for i, month in enumerate(("2027-01", "2027-02", "2027-03")):
        private = [
            phase10.Booking(category=C.HH_SALARY, amount=Decimal("3000.00")),
            phase10.Booking(category=C.HH_RENT, amount=Decimal("-1000.00")),
        ]
        if i == 1:
            private += [phase10.Booking(category=c, amount=Decimal(a)) for c, a in extra]
        months.append(phase10.MonthInput(month=month, private=private))
    built = [phase10.household(m, "netting-one-off", codes) for m in months]
    hms, lines = [b[0] for b in built], [b[1] for b in built]
    view = B.budget(hms, lines, {}, BudgetTargets(), as_of=AS_OF, one_off_codes=codes)
    return hms, lines, view, codes


@pytest.mark.parametrize(
    ("extra", "switches", "one_off_code", "refund_code"),
    [
        # a default one-off code (D307) and a regular refund in the same group (Gesundheit)
        (
            [(C.HH_DENTAL_OPTICAL, "-200.00"), (C.HH_DOCTOR, "200.00")],
            None,
            C.HH_DENTAL_OPTICAL,
            C.HH_DOCTOR,
        ),
        # a code the household switched on, and a refund in its group (Reisen)
        (
            [(C.HH_FLIGHTS, "-300.00"), (C.HH_TRAVEL, "300.00")],
            {C.HH_FLIGHTS: True},
            C.HH_FLIGHTS,
            C.HH_TRAVEL,
        ),
    ],
    ids=["default_code", "switched_on_code"],
)
def test_a_one_off_payment_whose_group_nets_to_zero(
    extra, switches, one_off_code, refund_code
) -> None:
    """month_cashflow drops a group that nets to 0 in the month; the one-off payment still
    leaves the regular spending, and the Budget, the Sparquote and spending in play agree."""
    hms, lines, view, codes = _netting_one_off(extra, switches)
    paid = -Decimal(extra[0][1])
    feb = hms[1]
    assert feb.spending == Decimal("1000.00")  # the group nets to 0
    assert feb.one_off_spending == paid
    assert feb.regular_spending == Decimal("1000.00") - paid
    assert feb.saved + feb.one_off_spending == feb.income - feb.regular_spending
    # the Budget reads it without raising: one-off beside the partition, the refund regular
    assert view.one_off is not None and view.one_off.total == paid
    assert {c.category: c.total for c in view.one_off.codes} == {one_off_code: paid}
    assert view.months[1].one_off == paid
    by_code = {c.category: c.total for li in view.lines for c in li.codes}
    assert by_code[refund_code] == -paid
    saving = next(li for li in view.lines if li.key is L.SAVING)
    assert saving.total == Decimal("6000.00") + paid
    assert saving.share == savings_rate(hms).rate
    # spending in play's regular spending per code adds up to the month's
    per_code = SP.month_spending(_cashflow(feb, lines[1]), lines[1], frozenset(), codes)
    assert per_code == {C.HH_RENT: Decimal("1000.00"), refund_code: -paid}
    assert sum(per_code.values()) == feb.regular_spending


def test_every_code_the_month_counts_as_spending_is_switchable() -> None:
    """D307 (review of §7.12): a household may switch any code the household month counts as
    spending, the tax codes too (hh_tax_payment, hh_donation, capital_income_tax, ...), and
    no other taxonomy code; the defaults stay spending codes."""
    from hausbuch.rules.health.month import _role

    assert {c for c in TAXONOMY if _role(c) == "spending"} == SWITCHABLE
    assert {C.HH_TAX_PAYMENT, C.HH_DONATION, C.CAPITAL_INCOME_TAX} <= SWITCHABLE
    assert ONE_OFF <= SWITCHABLE and C.HH_SALARY not in SWITCHABLE
    assert C.HH_TAX_PAYMENT in one_off_codes({C.HH_TAX_PAYMENT: True})
    assert C.HH_SALARY not in one_off_codes({C.HH_SALARY: True})


def test_a_switched_on_tax_payment_counts_once_as_one_off() -> None:
    """A tax code switched on (an Einkommensteuer back-payment): one-off in the month, in
    „Einmalig im Zeitraum“ and out of the Sparquote, counted once."""
    hms, lines, view, codes = _netting_one_off(
        [(C.HH_TAX_PAYMENT, "-500.00")], {C.HH_TAX_PAYMENT: True}
    )
    feb = hms[1]
    assert C.HH_TAX_PAYMENT in codes
    assert feb.spending == Decimal("1500.00")
    assert feb.one_off_spending == Decimal("500.00")
    assert feb.regular_spending == Decimal("1000.00")
    assert view.one_off is not None and view.one_off.total == Decimal("500.00")
    assert {c.category: c.total for c in view.one_off.codes} == {
        C.HH_TAX_PAYMENT: Decimal("500.00")
    }
    placed = [c.category for li in view.lines for c in li.codes]
    assert C.HH_TAX_PAYMENT not in placed  # not in Notwendiges as well
    saving = next(li for li in view.lines if li.key is L.SAVING)
    assert saving.total == Decimal("6000.00")
    assert saving.share == savings_rate(hms).rate == Decimal("0.6667")
    per_code = SP.month_spending(_cashflow(feb, lines[1]), lines[1], frozenset(), codes)
    assert per_code == {C.HH_RENT: Decimal("1000.00")}
    # switched off (the default), the same payment is a need and in the Sparquote
    hms, _, view, _ = _netting_one_off([(C.HH_TAX_PAYMENT, "-500.00")])
    assert hms[1].one_off_spending == 0 and view.one_off is None
    assert next(li for li in view.lines if li.key is L.SAVING).total == Decimal("5500.00")


def _cashflow(month, lines):
    return month_cashflow(month.month, cashflow_rows(lines), saved=Decimal("0.00"))


# --- marks at the precision the sentence prints (review of PR #490) ----------------------


@pytest.mark.parametrize(
    ("share", "minimum"),
    [
        (Decimal("0.5004"), False),  # 50,0 % against höchstens 50,0 %
        (Decimal("0.4996"), False),  # 50,0 % from below
        (Decimal("0.1996"), True),  # 20,0 % against mindestens 20,0 %
        (Decimal("0.2004"), True),
    ],
)
def test_a_share_printed_as_the_target_meets_it(share: Decimal, minimum: bool) -> None:
    target = Decimal("20.0") if minimum else Decimal("50.0")
    m = B.mark("Linie", share, Decimal("1000.00"), Decimal("2000.00"), target, minimum=minimum)
    assert m is not None
    assert m.colour.value == "green"
    assert m.gap_points == 0 and str(m.gap_points) == "0.0"  # never −0.0
    assert ", genau auf eurem Ziel von" in m.text


def test_a_share_printed_past_the_target_misses_it() -> None:
    m = B.mark("Linie", Decimal("0.5005"), Decimal("1001.00"), Decimal("2000.00"), Decimal("50.0"))
    assert m is not None and m.colour.value == "amber"
    assert m.gap_points == Decimal("0.1")
    assert "50,1 %" in m.text and "0,1 Prozentpunkte über" in m.text
    m = B.mark(
        "Sparen",
        Decimal("0.1995"),
        Decimal("399.00"),
        Decimal("2000.00"),
        Decimal("20.0"),
        minimum=True,
    )
    assert m is not None and m.colour.value == "green"  # 19,95 % prints 20,0 %
    m = B.mark(
        "Sparen",
        Decimal("0.1994"),
        Decimal("398.80"),
        Decimal("2000.00"),
        Decimal("20.0"),
        minimum=True,
    )
    assert m is not None and m.colour.value == "amber" and m.gap_points == Decimal("-0.1")


def test_spending_per_code_is_what_household_month_counts_as_spending() -> None:
    """rules/budget sums the lines month_cashflow counts as spending; rules.health.month's
    own reading of month_cashflow must agree for every code, or the check against A fails."""
    from hausbuch.rules.health.month import _role

    for code in C:
        assert B.is_spending(code) is (_role(code) == "spending"), code
