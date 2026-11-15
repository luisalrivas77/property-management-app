"""D195: „Kein Arbeitslohn in <Jahr>“ (a `no_pay` return input) in the Anlage N draft, the
whole-return estimate, the D98 contexts and phase 4's zvE base. The fixtures pin the draft
cases and the estimate's twin of `benefits_without_pay`; this file pins what they cannot:
that the marker counts nothing a marked owner typed, that phase 4 takes gross 0 for that
year instead of a carried expected pay, and that the year after reads as if no marker
existed."""

from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase4b import current, load

from hausbuch.rules.performance.income import zve_base
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.draft import draft_n
from hausbuch.rules.tax.anlage_n.summary import TaxContexts
from hausbuch.rules.tax.return_estimate import return_estimate
from hausbuch.schemas.employment import OwnerReturnFacts, PropertyIncome, ReturnInputFact
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import IncomeInputFact

ZERO = Decimal("0")
RENTAL = Decimal("-7948.66")
_FIELDS = (
    "status",
    "total_income",
    "vorsorge_total",
    "church_tax_paid",
    "donations",
    "childcare",
    "special_expenses",
    "zve",
    "assessed",
    "withheld",
    "result",
    "filing_required",
    "missing",
    "warnings",
)


def _case():
    return next(c for c in load("return_estimate").cases if c.id == "benefits_without_pay_declared")


def _row(n: int, owner_id: UUID, kind: str, value: dict, **extra) -> ReturnInputFact:
    return ReturnInputFact.model_validate(
        {
            "id": UUID(int=0xD195_00 + n),
            "owner_id": owner_id,
            "kind": kind,
            "tax_year": 2026,
            "value": value,
            **extra,
        }
    )


def _typed_costs(owner_id: UUID) -> list[ReturnInputFact]:
    """Rows a marked owner might still have typed: a route, home-office days, a union fee and
    other insurance (Vorsorge, Nr. 3a)."""
    return [
        _row(
            1,
            owner_id,
            "commute",
            {
                "kind": "first_workplace",
                "workplace_label": "Muster GmbH",
                "period_from": "2026-01-01",
                "period_to": "2026-12-31",
                "days_per_week": 5,
                "days": 100,
                "km": 25,
                "km_car": 25,
            },
        ),
        _row(2, owner_id, "home_office", {"days_other_workplace": 90}),
        _row(
            3,
            owner_id,
            "professional_association",
            {"name": "Gewerkschaft", "amount": "240.00", "is_union": True},
        ),
        _row(4, owner_id, "other_insurance", {"amount": "300.00"}),
    ]


def _without_marker(facts: OwnerReturnFacts, *, expected: bool) -> OwnerReturnFacts:
    return facts.model_copy(
        update={
            "inputs": [r for r in facts.inputs if r.kind is not K.NO_PAY],
            "expected_gross_pay": facts.expected_gross_pay if expected else None,
        }
    )


def _estimate(case, facts):
    return return_estimate(
        case.tax_year,
        case.owners,
        facts,
        current(case.household),
        [PropertyIncome(property_id=p.property_id, result=p.result) for p in case.properties],
        case.questionnaire,
        current(case.answers),
        registry.tariff(case.tax_year),
        rules_for(case.tax_year),
    )


@pytest.mark.rule
def test_a_marked_owner_counts_nothing_typed_and_no_expected_pay():
    """The marker changes no figure against an owner with no pay source at all, whatever the
    owner typed (Werbungskosten and Vorsorge rows are dropped on both sides, PR #338's open
    point) and whatever pay was expected."""
    case = _case()
    a, b = case.resolved_owners()
    typed = _typed_costs(b.owner_id)
    marked = b.model_copy(update={"inputs": [*b.inputs, *typed]})
    nobody = _without_marker(marked, expected=False)
    got, plain = _estimate(case, [a, marked]), _estimate(case, [a, nobody])
    for name in _FIELDS:
        assert getattr(got, name) == getattr(plain, name), name
    assert got.income_n[1].status == plain.income_n[1].status == "none"
    assert got.income_n[1].no_pay_declared and not plain.income_n[1].no_pay_declared
    marker = next(r for r in b.inputs if r.kind is K.NO_PAY)
    assert [s.id for s in got.income_n[1].sources] == [marker.id]

    draft = draft_n(marked, 2026, rules_for(2026), registry.tariff(2026))
    assert (draft.status, draft.gross_source) == ("none", "declared_none")
    assert draft.gross == draft.wk_applied == draft.wk_itemised == draft.result == ZERO
    assert {line.key for line in draft.lines} == {
        "pay.gross",
        "wk.itemised",
        "wk.allowance",
        "wk.applied",
    }
    assert all(line.amount == 0 for line in draft.lines)
    assert [w.code.value for w in draft.warnings] == ["anlage_n.form_provisional"]


@pytest.mark.rule
def test_an_overruled_marker_cites_itself():
    case = next(c for c in load("anlage_n").cases if c.id == "certificate_overrules_no_pay")
    draft = draft_n(case.facts, 2026, rules_for(2026), registry.tariff(2026))
    overruled = [w for w in draft.warnings if w.code.value == "anlage_n.no_pay_overruled"]
    marker = next(r for r in case.facts.inputs if r.kind is K.NO_PAY)
    assert [[ref.id for ref in w.refs] for w in overruled] == [[marker.id]]
    assert draft.gross_source == "certificate"


def _expected(n: int, owner_id: UUID, year: int, amount: str) -> IncomeInputFact:
    return IncomeInputFact.model_validate(
        {
            "id": UUID(int=0xD195_80 + n),
            "owner_id": owner_id,
            "kind": "expected_gross_pay",
            "tax_year": year,
            "value": {"amount": amount},
        }
    )


def _contexts(case, owner_facts, expected_pay):
    return TaxContexts(
        owner_facts, current(case.household), rules_for, registry.tariff, expected_pay
    )


@pytest.mark.rule
def test_phase_4_counts_a_marked_owner_with_gross_0_that_year_only():
    """B marked 2026 while her expected pay of 2025 would carry: phase 4's per-owner estimate
    takes gross 0 for 2026 (D98 summary of source declared_none), A's figures unchanged; in
    2027 everything reads as if no marker existed."""
    case = _case()
    a, b = case.resolved_owners()
    a, b = (f.model_copy(update={"expected_gross_pay": None}) for f in (a, b))
    pay = [_expected(1, a.owner_id, 2025, "60000.00"), _expected(2, b.owner_id, 2025, "32000.00")]
    unmarked_b = _without_marker(b, expected=False)
    marked, plain = _contexts(case, [a, b], pay), _contexts(case, [a, unmarked_b], pay)

    def base(tc, year):
        employment, household = tc(year, RENTAL)
        got = zve_base(
            year,
            pay,
            case.owners,
            registry.tariff(year),
            employment=employment,
            household=household,
        )
        assert got is not None
        return {o.owner_id: o for o in got.per_owner}, employment, household

    owners, employment, household = base(marked, 2026)
    by_plain, _, plain_household = base(plain, 2026)
    assert employment is not None
    declared = next(e for e in employment if e.owner_id == b.owner_id)
    assert (declared.gross, declared.gross_source, declared.vorsorge) == (
        ZERO,
        "declared_none",
        ZERO,
    )
    assert (owners[b.owner_id].gross, owners[b.owner_id].gross_source) == (ZERO, "declared_none")
    assert owners[b.owner_id].amount == ZERO
    assert (by_plain[b.owner_id].gross, by_plain[b.owner_id].gross_source) == (
        Decimal("32000.00"),
        "expected",
    )
    assert owners[a.owner_id] == by_plain[a.owner_id]
    # B's Elterngeld less the whole Pauschbetrag (gross 0), as benefits_without_pay; with the
    # expected pay counted, the Pauschbetrag was used against it and nothing came off
    assert household is not None and plain_household is not None
    assert household.replacement_benefits == Decimal("10570.00")
    assert plain_household.replacement_benefits == Decimal("11800.00")

    later, later_employment, later_household = base(marked, 2027)
    later_plain, later_plain_employment, later_plain_household = base(plain, 2027)
    assert later == later_plain
    assert later_employment == later_plain_employment
    assert later_household == later_plain_household
    assert later[b.owner_id].gross_source == "expected"


@pytest.mark.rule
def test_a_marker_alone_makes_no_year_and_no_base():
    """A marker is never the year the household rows are taken from, and a marked owner's
    summary alone does not stand for the household's income (the other owner's is missing)."""
    case = _case()
    a, b = case.resolved_owners()
    marker = next(r for r in b.inputs if r.kind is K.NO_PAY)
    only = [
        a.model_copy(update={"inputs": [], "expected_gross_pay": None}),
        b.model_copy(update={"inputs": [marker], "expected_gross_pay": None}),
    ]
    tc = _contexts(case, only, [])
    assert tc.source_year(2026) is None
    employment, household = tc(2026, RENTAL)
    assert household is None
    assert employment is not None and [e.gross_source for e in employment] == ["declared_none"]
    assert zve_base(2026, [], case.owners, registry.tariff(2026), employment=employment) is None
    with_a = [_expected(3, a.owner_id, 2026, "60000.00")]
    got = zve_base(2026, with_a, case.owners, registry.tariff(2026), employment=employment)
    assert got is not None
    assert [(o.gross, o.gross_source) for o in got.per_owner] == [
        (Decimal("60000.00"), "expected"),
        (ZERO, "declared_none"),
    ]
