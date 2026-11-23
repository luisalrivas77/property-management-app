"""Finanzielle Gesundheit in one word (phase 10 §7.6, rules/verdict): each overall word, the
household's confirmed limits winning per figure and per side, too little data, no loan, the
standard limits, the English of every text, and that the rule version of the stored key figures
does not move with it."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

import pytest

from hausbuch import i18n
from hausbuch.rules.budget.references import reference
from hausbuch.rules.verdict import verdict as V
from hausbuch.schemas.health import (
    BandLimits,
    Debt,
    EstimateSource,
    Health,
    HealthTodo,
    HealthTodoCode,
    MarkedFigure,
    PropertyLtv,
    Reserve,
    ReserveState,
    SavingsRate,
    VerdictOrigin,
    VerdictState,
    VerdictWord,
    Window,
)
from hausbuch.services import read_texts, todo_texts

ZERO = Decimal("0.00")
INCOME = Decimal("10000.00")
WINDOW = Window(months=["2027-03", "2027-04", "2027-05"], enough=True)
FLAT = UUID(int=1)
SAVING = MarkedFigure.SAVINGS_RATE
RESERVE = MarkedFigure.RESERVE
DEBT = MarkedFigure.DEBT_SERVICE_RATIO


def _rate(rate: str | None) -> SavingsRate | None:
    if rate is None:
        return None
    value = Decimal(rate)
    return SavingsRate(
        saved=value * INCOME,
        income=INCOME,
        one_off_spending=ZERO,
        principal=ZERO,
        rate=value,
        rate_regular=value,
        rate_without_principal=value,
    )


def _reserve(amount: str, target: str = "12000.00") -> Reserve:
    have, want = Decimal(amount), Decimal(target)
    need = (want / 6).quantize(Decimal("0.01"))
    want = (6 * need).quantize(Decimal("0.01"))
    return Reserve(
        amount=have,
        need=need,
        months=(have / need).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        target_months=Decimal("6"),
        target=want,
        gap=max(ZERO, want - have),
        state=ReserveState.REACHED if have >= want else ReserveState.BELOW,
    )


def _debt(ratio: str | None, *, loan: str = "200000.00", service: str = "18000.00") -> Debt:
    return Debt(
        properties=[
            PropertyLtv(
                property_id=FLAT,
                loan_balance=Decimal(loan),
                estimate=Decimal("300000.00"),
                estimate_source=EstimateSource.SCENARIO,
                ltv=Decimal("0.6667") if Decimal(loan) else None,
            )
        ],
        liabilities=Decimal(loan),
        gross_assets=Decimal("400000.00"),
        interest=ZERO,
        principal=ZERO,
        debt_service_year=Decimal(service),
        income_year=Decimal("120000.00"),
        debt_service_ratio=None if ratio is None else Decimal(ratio),
        months=3,
    )


def health(
    rate: str | None = "0.1500",
    reserve: str | None = "12000.00",
    debt: Debt | None = None,
    *,
    window: Window = WINDOW,
    todos: list[HealthTodo] | None = None,
) -> Health:
    return Health(
        as_of=date(2027, 6, 3),
        window=window,
        savings_rate=_rate(rate),
        reserve=None if reserve is None else _reserve(reserve),
        debt=_debt("0.1500") if debt is None else debt,
        todos=todos or [],
    )


def parts(found):
    return {p.figure: p for p in found.parts}


# --- The standard limits -----------------------------------------------------------------


def test_the_standard_limits_are_the_agreed_ones():
    """Reserve good at its target, strained below half of it; Schuldendienstquote good up to
    30 %, strained above 40 %; savings rate strained below 0, good from 10 %."""
    assert V.defaults() == {
        SAVING: (Decimal("0.10"), Decimal("0")),
        RESERVE: (None, Decimal("0.5")),
        DEBT: (Decimal("0.30"), Decimal("0.40")),
    }


def test_the_savings_reference_names_the_budget_rule_it_quotes():
    """The 50/30/20 rule's 20 % in verdict.yaml's text is the Budget's reference point."""
    assert V.data().budget_references == ("split_50_30_20",)
    assert reference("split_50_30_20").saving == Decimal("20")
    assert "20 %" in V.data().texts["savings_rate.reference"]


# --- Each overall word -------------------------------------------------------------------


def test_very_good_when_all_three_are_good():
    found = V.verdict(health("0.1500", "12000.00", _debt("0.2000")), [])
    assert found.word is VerdictWord.VERY_GOOD
    assert found.weakest is None
    assert {p.state for p in found.parts} == {VerdictState.GOOD}
    assert [p.figure for p in found.parts] == [SAVING, RESERVE, DEBT]
    assert found.line == "Sparen, Reserve und Kredit: alles im guten Bereich"


def test_good_when_one_is_ok_and_none_strained():
    """A reserve of 8,000 € against 12,000 €: below the target, above half of it."""
    found = V.verdict(health("0.1500", "8000.00", _debt("0.2000")), [])
    assert found.word is VerdictWord.GOOD
    assert parts(found)[RESERVE].state is VerdictState.OK
    assert found.weakest is RESERVE
    assert found.line == "Reserve unter eurem Ziel"


def test_strained_when_any_one_is_strained():
    found = V.verdict(health("0.1500", "12000.00", _debt("0.4500")), [])
    assert found.word is VerdictWord.STRAINED
    assert found.weakest is DEBT
    assert found.line == "Kreditrate belastet stark"


def test_a_strained_part_is_named_before_an_ok_one():
    found = V.verdict(health("0.0500", "4000.00", _debt("0.2000")), [])
    assert parts(found)[SAVING].state is VerdictState.OK
    assert parts(found)[RESERVE].state is VerdictState.STRAINED
    assert (found.word, found.weakest) == (VerdictWord.STRAINED, RESERVE)
    assert found.line == "Reserve weit unter eurem Ziel"


@pytest.mark.parametrize(
    ("rate", "state", "headline"),
    [
        ("0.1000", VerdictState.GOOD, "Ihr spart genug"),
        ("0.0999", VerdictState.OK, "Ihr spart, aber wenig"),
        ("0.0000", VerdictState.OK, "Ihr spart, aber wenig"),
        ("-0.0001", VerdictState.STRAINED, "Ihr gebt mehr aus, als ihr verdient"),
    ],
)
def test_the_savings_rate_on_its_limits(rate, state, headline):
    part = parts(V.verdict(health(rate), []))[SAVING]
    assert (part.state, part.headline) == (state, headline)
    assert part.value == Decimal(rate)


@pytest.mark.parametrize(
    ("ratio", "state"),
    [("0.3000", VerdictState.GOOD), ("0.3001", VerdictState.OK), ("0.4000", VerdictState.OK)]
    + [("0.4001", VerdictState.STRAINED)],
)
def test_the_debt_service_ratio_on_its_limits(ratio, state):
    assert parts(V.verdict(health(debt=_debt(ratio)), []))[DEBT].state is state


@pytest.mark.parametrize(
    ("amount", "state"),
    [("12000.00", VerdictState.GOOD), ("11999.99", VerdictState.OK)]
    + [("6000.00", VerdictState.OK), ("5999.99", VerdictState.STRAINED)],
)
def test_the_reserve_in_euros_against_its_target(amount, state):
    part = parts(V.verdict(health(reserve=amount), []))[RESERVE]
    assert part.state is state
    assert (part.target, part.strained_amount) == (Decimal("12000.00"), Decimal("6000.00"))
    assert (part.good, part.strained) == (None, Decimal("0.5"))
    assert part.origin is VerdictOrigin.DEFAULT


# --- The household's limits win ----------------------------------------------------------


def test_household_limits_win_per_figure():
    """The household's 20 % good savings rate makes 15 % ok; the reserve and the debt keep
    the standard limits."""
    bands = [BandLimits(figure=SAVING, green=Decimal("0.20"), red=Decimal("0"))]
    found = V.verdict(health("0.1500", "12000.00", _debt("0.2000")), bands)
    saving = parts(found)[SAVING]
    assert saving.state is VerdictState.OK
    assert (saving.good, saving.good_origin) == (Decimal("0.20"), VerdictOrigin.HOUSEHOLD)
    assert saving.origin is VerdictOrigin.HOUSEHOLD
    assert parts(found)[DEBT].origin is VerdictOrigin.DEFAULT
    assert found.word is VerdictWord.GOOD


def test_a_one_sided_household_limit_keeps_the_standard_on_the_other_side():
    """„Übernehmen“ stores the savings rate's red 0 alone: good still comes from the standard
    10 %, so „Sehr gut“ stays reachable."""
    bands = [BandLimits(figure=SAVING, red=Decimal("0"))]
    saving = parts(V.verdict(health("0.1500"), bands))[SAVING]
    assert saving.state is VerdictState.GOOD
    assert (saving.good_origin, saving.strained_origin) == (
        VerdictOrigin.DEFAULT,
        VerdictOrigin.HOUSEHOLD,
    )


def test_a_household_side_beyond_the_standard_one_moves_it():
    """A household red of 15 % above the standard good of 10 %: good moves to 15 %, no ok band."""
    bands = [BandLimits(figure=SAVING, red=Decimal("0.15"))]
    found = parts(V.verdict(health("0.1200"), bands))[SAVING]
    assert found.state is VerdictState.STRAINED
    assert found.headline == "Ihr spart zu wenig"
    assert (found.good, found.good_origin) == (Decimal("0.15"), VerdictOrigin.HOUSEHOLD)
    debt = [BandLimits(figure=DEBT, green=Decimal("0.45"))]
    part = parts(V.verdict(health(debt=_debt("0.4200")), debt))[DEBT]
    assert part.state is VerdictState.GOOD
    assert (part.strained, part.strained_origin) == (Decimal("0.45"), VerdictOrigin.HOUSEHOLD)


def test_the_households_reserve_share_wins():
    bands = [BandLimits(figure=RESERVE, red=Decimal("0.75"))]
    part = parts(V.verdict(health(reserve="8000.00"), bands))[RESERVE]
    assert part.state is VerdictState.STRAINED
    assert part.strained_amount == Decimal("9000.00")
    assert part.strained_origin is VerdictOrigin.HOUSEHOLD


# --- No loan, too little data ------------------------------------------------------------


def test_no_loan_is_good():
    debt = _debt("0.0000", loan="0.00", service="0.00")
    found = V.verdict(health("0.1500", "12000.00", debt), [])
    part = parts(found)[DEBT]
    assert (part.state, part.no_loan, part.headline) == (VerdictState.GOOD, True, "Kein Kredit")
    assert found.word is VerdictWord.VERY_GOOD


def test_a_loan_without_a_debit_in_the_window_is_not_no_loan():
    """A ratio of 0 with a loan still owed is measured, not „Kein Kredit“."""
    part = parts(V.verdict(health(debt=_debt("0.0000", service="0.00")), []))[DEBT]
    assert (part.state, part.no_loan) == (VerdictState.GOOD, False)
    assert part.headline == "Kreditrate gut tragbar"


def test_too_few_months_names_what_is_missing():
    short = Window(months=["2027-05"], enough=False)
    wait = HealthTodo(
        code=HealthTodoCode.TOO_FEW_MONTHS, text="erst ein Monat", action="Öffnen", href="/inbox"
    )
    limits = HealthTodo(
        code=HealthTodoCode.BANDS_UNCONFIRMED, text="Grenzen", action="Festlegen", href="/"
    )
    found = V.verdict(
        Health(as_of=date(2027, 6, 3), window=short, todos=[wait, limits]),
        [],
    )
    assert found.word is VerdictWord.TOO_LITTLE_DATA
    assert found.line == "Für die Bewertung braucht es drei abgeschlossene Monate in Folge"
    assert found.todos == [wait]
    assert {p.state for p in found.parts} == {VerdictState.MISSING}


def test_a_missing_savings_rate_alone_is_too_little_data():
    """A full window with no income: the rate is None, the reserve is there."""
    found = V.verdict(health(rate=None, debt=_debt(None)), [])
    assert found.word is VerdictWord.TOO_LITTLE_DATA
    assert (found.weakest, found.line) == (SAVING, "Sparquote noch nicht berechenbar")
    assert parts(found)[RESERVE].state is VerdictState.GOOD


def test_a_missing_reserve_is_too_little_data():
    found = V.verdict(health(reserve=None), [])
    assert (found.word, found.weakest) == (VerdictWord.TOO_LITTLE_DATA, RESERVE)


# --- Texts -------------------------------------------------------------------------------


def test_every_text_has_english():
    assert set(V.data().texts) == set(read_texts.VERDICT)


def test_the_verdict_reads_in_english():
    found = V.verdict(health("0.1500", "8000.00", _debt("0.2000")), [])
    whole = health("0.1500", "8000.00", _debt("0.2000")).model_copy(update={"verdict": found})
    with i18n.use(i18n.EN):
        english = todo_texts.english(whole).verdict
    assert english is not None
    assert english.line == "Reserve below your target"
    assert [p.question for p in english.parts] == [
        "Do you spend less than you earn?",
        "Could you absorb a surprise?",
        "Can you carry your loan comfortably?",
    ]
    assert all("ä" not in p.why and "ü" not in p.why for p in english.parts)
    # German unchanged without the header
    assert todo_texts.english(whole).verdict == found
