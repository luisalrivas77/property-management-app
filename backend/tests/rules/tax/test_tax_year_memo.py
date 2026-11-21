"""The memoised §5.1 tax year (rules/tax/tax_year.py, 21 Nov 2026) changes no answer.

1. tax_year_of equals a verbatim copy of the rule before the memo, over a grid of payments
   around two year ends (short periods of 10 and 5 days, due dates in and out of them, value
   dates across the year end, periods within one year and across it).
2. Every Anlage V fixture draft and every projection fixture (13 drafts per run in a payback
   request) is equal with the memo and with the memo switched off.
3. Each caller gets its own notes list, so one caller's change can never reach another's.
"""

from datetime import date, timedelta
from itertools import product

import pytest
from fixture_models_phase3 import DraftFixture
from fixture_models_phase3 import load as load3
from fixture_models_phase4 import ProjectionFixture
from fixture_models_phase4 import load as load4

from hausbuch.rules.performance.projection import project
from hausbuch.rules.tax import tax_year as module
from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.registry import rule_set
from hausbuch.schemas.allocation import AllocationCategory, ServicePeriod
from hausbuch.schemas.tax import PaymentFacts, RuleSet, TaxYearNote, TaxYearResult


def _before(payment: PaymentFacts, rule_set: RuleSet) -> TaxYearResult:
    """tax_year_of as it was before the memo (commit 45dedb9c), verbatim."""
    booked = payment.booking_date
    paid_year = booked.year
    notes: list[TaxYearNote] = []
    if payment.value_date is not None and payment.value_date.year != paid_year:
        notes.append(TaxYearNote.YEAR_END_STRADDLE)

    period = payment.service_period
    if not payment.recurring or period is None or period.start.year != period.end.year:
        e = None
    else:
        e = period.start.year
    if e is not None and abs(e - paid_year) == 1:
        year_end = min(e, paid_year)
        start, end = module.short_period(year_end, rule_set.short_period_days)
        if start <= booked <= end:
            if payment.due_on is None:
                notes.append(TaxYearNote.DUE_DATE_MISSING)
            elif start <= payment.due_on <= end:
                return TaxYearResult(year=e, rule="short_period_shift", notes=notes)
    return TaxYearResult(year=paid_year, rule="paid", notes=notes)


def _days(first: date, last: date, step: int = 1) -> list[date]:
    return [first + timedelta(days=n) for n in range(0, (last - first).days + 1, step)]


PERIODS = [
    None,
    ServicePeriod(start=date(2025, 12, 1), end=date(2025, 12, 31)),
    ServicePeriod(start=date(2026, 1, 1), end=date(2026, 1, 31)),
    ServicePeriod(start=date(2026, 12, 1), end=date(2026, 12, 31)),
    ServicePeriod(start=date(2027, 1, 1), end=date(2027, 1, 31)),
    ServicePeriod(start=date(2025, 12, 15), end=date(2026, 1, 14)),  # across a year end
    ServicePeriod(start=date(2026, 1, 1), end=date(2026, 12, 31)),
]
BOOKED = _days(date(2025, 12, 15), date(2026, 1, 17)) + _days(date(2026, 12, 18), date(2027, 1, 14))
DUE = [None, *_days(date(2025, 12, 18), date(2026, 1, 14), 3), date(2026, 12, 27), date(2027, 1, 3)]
VALUE = [None, date(2025, 12, 31), date(2026, 1, 2), date(2027, 1, 1)]


def _rule_sets() -> list[RuleSet]:
    base = rule_set(2026)
    return [base, base.model_copy(update={"short_period_days": 5})]


def test_tax_year_equals_the_rule_before_the_memo():
    module._resolve.cache_clear()
    checked = shifted = 0
    for rs, period, recurring, booked, due, value in product(
        _rule_sets(), PERIODS, (True, False), BOOKED, DUE, VALUE
    ):
        payment = PaymentFacts(
            booking_date=booked,
            value_date=value,
            amount="-100.00",
            category=AllocationCategory.RENT_COLD,
            recurring=recurring,
            service_period=period,
            due_on=due,
        )
        want = _before(payment, rs)
        assert module.tax_year_of(payment, rs) == want, payment
        assert module.tax_year(payment, rs) == want.year, payment
        checked += 1
        shifted += want.rule == "short_period_shift"
    assert shifted > 100 and checked > 10_000  # the grid reaches the shifting branch


def test_each_caller_gets_its_own_notes():
    payment = PaymentFacts(
        booking_date=date(2026, 12, 31),
        value_date=date(2027, 1, 2),
        amount="-1.00",
        category=AllocationCategory.RENT_COLD,
    )
    rs = rule_set(2026)
    first = module.tax_year_of(payment, rs)
    first.notes.append(TaxYearNote.DUE_DATE_MISSING)
    assert module.tax_year_of(payment, rs).notes == [TaxYearNote.YEAR_END_STRADDLE]


@pytest.fixture
def no_memo(monkeypatch):
    """The rule with its memo switched off: the same function, uncached."""
    monkeypatch.setattr(module, "_resolve", module._resolve.__wrapped__)


DRAFTS = load3("anlage_v_2026")
assert isinstance(DRAFTS, DraftFixture)
PROJECTION = load4("projection")
assert isinstance(PROJECTION, ProjectionFixture)


def _drafts():
    return [
        draft(case.facts, case.tax_year, rule_set(case.rule_set_year)).model_dump()
        for case in DRAFTS.cases
    ]


def _projections():
    return [
        project(
            case.facts.resolve(),
            case.levers,
            case.owners,
            case.income_inputs,
            case.today,
            {loan.loan_id: loan.terms for loan in case.loans},
        ).model_dump()
        for case in PROJECTION.cases
    ]


@pytest.fixture(scope="module")
def with_memo():
    module._resolve.cache_clear()
    return _drafts(), _projections()


def test_drafts_and_projections_equal_without_the_memo(with_memo, no_memo):
    drafts, projections = with_memo
    assert _drafts() == drafts
    assert _projections() == projections
