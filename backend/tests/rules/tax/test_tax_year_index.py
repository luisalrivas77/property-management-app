"""The §5.1 year computed once per draft and per projection run (phase 10 §7.3 (3), YearIndex in
rules/tax/_common.py) changes no answer.

1. Every Anlage V fixture draft, every projection fixture and every actuals fixture serialises
   to the same JSON with the index and with it switched off (every rule then computes each
   year itself, as before), and the index was really used (not a vacuous equality).
2. An index answers only for the very list it was built from under the same short period; any
   other list or short period gets the years computed as without it.
"""

from datetime import date

import pytest
from fixture_models_phase3 import DraftFixture
from fixture_models_phase3 import load as load3
from fixture_models_phase4 import ActualsFixture, ProjectionFixture
from fixture_models_phase4 import load as load4

from hausbuch.rules.performance.projection import actual_years, project
from hausbuch.rules.tax import _common
from hausbuch.rules.tax._common import YearIndex, of_year, with_year_numbers
from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.registry import rule_set
from hausbuch.schemas.allocation import AllocationCategory, ServicePeriod
from hausbuch.schemas.enums import AccountPurpose
from hausbuch.schemas.tax import AllocationFact

DUE = date(2027, 1, 3)
DRAFTS = load3("anlage_v_2026")
assert isinstance(DRAFTS, DraftFixture)
PROJECTION = load4("projection")
assert isinstance(PROJECTION, ProjectionFixture)
ACTUALS = load4("actuals")
assert isinstance(ACTUALS, ActualsFixture)


def _answers() -> list[str]:
    out = [
        draft(case.facts, case.tax_year, rule_set(case.rule_set_year)).model_dump_json()
        for case in DRAFTS.cases
    ]
    out += [
        project(
            case.facts.resolve(),
            case.levers,
            case.owners,
            case.income_inputs,
            case.today,
            {loan.loan_id: loan.terms for loan in case.loans},
        ).model_dump_json()
        for case in PROJECTION.cases
    ]
    for case in ACTUALS.cases:
        years = actual_years(case.facts.resolve(), case.today, case.owners, case.income_inputs)
        out += [y.model_dump_json() for y in years]
    return out


def test_drafts_projections_and_actuals_equal_without_the_index(monkeypatch):
    served = {"yes": 0, "no": 0}
    serves = YearIndex.serves

    def counting(self, allocations, rs):
        answer = serves(self, allocations, rs)
        served["yes" if answer else "no"] += 1
        return answer

    monkeypatch.setattr(YearIndex, "serves", counting)
    with_index = _answers()
    assert served["yes"] > 1000  # the index answered the rules' lookups

    monkeypatch.setattr(YearIndex, "serves", lambda self, allocations, rs: False)
    without = _answers()
    assert len(with_index) == len(without) > len(DRAFTS.cases)
    for got, want in zip(with_index, without, strict=True):
        assert got == want


def test_a_draft_with_private_lines_reads_the_rental_index():
    """The fixture drafts include private-account lines (phase 6 D161), so rental() is reached."""
    cases = [
        c
        for c in DRAFTS.cases
        if any(a.account_purpose is AccountPurpose.PRIVATE for a in c.facts.allocations)
    ]
    assert cases
    for case in cases:
        rs = rule_set(case.rule_set_year)
        index = YearIndex.of(case.facts.allocations, rs)
        rental = index.rental()
        assert all(a.account_purpose is AccountPurpose.RENTAL for a in rental.allocations)
        assert draft(case.facts, case.tax_year, rs, years=index) == draft(
            case.facts, case.tax_year, rs
        )


def _row(booked: date, amount: str, **extra) -> AllocationFact:
    return AllocationFact.model_validate(
        {
            "allocation_id": "00000000-0000-0000-0000-0000000000" + f"{booked.day:02d}",
            "transaction_id": "00000000-0000-0000-0000-000000000001",
            "booking_date": booked,
            "amount": amount,
            "category": AllocationCategory.RENT_COLD,
            "account_purpose": AccountPurpose.RENTAL,
            **extra,
        }
    )


@pytest.fixture
def rows() -> list[AllocationFact]:
    january = ServicePeriod(start=date(2027, 1, 1), end=date(2027, 1, 31))
    return [
        _row(date(2026, 12, 29), "800.00", recurring=True, service_period=january, due_on=DUE),
        _row(date(2026, 12, 20), "800.00", recurring=True, service_period=january, due_on=DUE),
        _row(date(2026, 12, 30), "0.00"),
    ]


def test_an_index_serves_only_its_own_list_and_short_period(rows, monkeypatch):
    rs = rule_set(2026)
    index = YearIndex.of(rows, rs)
    assert [a for a, _ in index.pairs] == rows[:2]  # D70: the 0.00 row is no evidence
    assert index.serves(rows, rs)
    assert not index.serves(list(reversed(rows)), rs)
    assert not index.serves(rows[:2], rs)
    assert not index.serves([r.model_copy() for r in rows], rs)
    assert not index.serves(iter(rows), rs)
    other = rs.model_copy(update={"short_period_days": rs.short_period_days - 1})
    assert not index.serves(rows, other)

    # A forged index for a different list is never read: its years are not the list's.
    forged = YearIndex(
        allocations=index.allocations, short_period_days=rs.short_period_days, pairs=()
    )
    want = [y for _, y in with_year_numbers(rows, rs)]
    assert list(with_year_numbers(list(rows), rs, forged)) == []  # the same objects: served
    copies = [r.model_copy() for r in rows]
    assert [y for _, y in with_year_numbers(copies, rs, forged)] == want  # computed instead
    assert want == [2027, 2026]  # the short period shifts the first row only
    assert of_year(rows, 2027, rs, index) == of_year(rows, 2027, rs) == rows[:1]


def test_run_years_builds_one_index_per_short_period(rows):
    rs = rule_set(2026)
    other = rs.model_copy(update={"short_period_days": 5})
    run = _common.RunYears(rows)
    assert run(rs) is run(rs)
    assert run(other) is not run(rs)
    assert run(other).short_period_days == 5
    assert [y for _, y in run(rs).pairs] == [y for _, y in with_year_numbers(rows, rs)]
    assert [y for _, y in run(other).pairs] == [y for _, y in with_year_numbers(rows, other)]
