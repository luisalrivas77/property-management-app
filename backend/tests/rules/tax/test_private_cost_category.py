"""Phase 2 D135: a cost paid from another account carries the category of the bill's part,
and the draft books it on that category's line of the rule set, as a confirmed allocation of
the category would land."""

from decimal import Decimal
from uuid import uuid4

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import DraftFixture, load

from hausbuch.rules import prefill
from hausbuch.rules.categories import TAX_TREATMENT
from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.registry import available_years, rule_set
from hausbuch.schemas.allocation import AllocationCategory, TaxTreatment
from hausbuch.schemas.tax import PRIVATE_COST_CATEGORIES, PrivateCostValue, TaxInputFact

C = AllocationCategory
DRAFT = load("anlage_v_2026")
assert isinstance(DRAFT, DraftFixture)


@pytest.mark.rule
def test_the_categories_are_the_deductible_ones_everywhere():
    deductible = {c for c, t in TAX_TREATMENT.items() if t is TaxTreatment.DEDUCTIBLE}
    assert deductible == PRIVATE_COST_CATEGORIES
    assert set(prefill.PRIVATE_COST_CATEGORIES) == deductible
    for never in (C.LOAN_PRINCIPAL, C.HAUSGELD_RUECKLAGE, C.PURCHASE_PRICE, C.ACQUISITION_COST):
        assert never not in PRIVATE_COST_CATEGORIES


@pytest.mark.rule
@pytest.mark.parametrize("year", available_years())
def test_every_category_lands_on_a_cost_line(year):
    lines = rule_set(year).form.category_lines
    for category in PRIVATE_COST_CATEGORIES:
        assert (lines.get(category) or "").startswith("wk."), category


def _value(**overrides):
    return {"cost_class": "other", "label": "Rechnung", "amount": "100.00"} | overrides


@pytest.mark.rule
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"category": "renovation"}, "deductible"),
        ({"category": "loan_principal"}, "deductible"),
        ({"category": "purchase_price"}, "deductible"),
        ({"category": "repairs", "cost_class": "postage"}, "other or financing"),
        ({"category": "repairs", "cost_class": "financing"}, "loan_fees"),
        ({"category": "loan_fees"}, "loan_fees"),
    ],
)
def test_a_category_is_checked(overrides, message):
    with pytest.raises(ValueError, match=message):
        PrivateCostValue.model_validate(_value(**overrides))


@pytest.mark.rule
def test_old_rows_and_categorized_rows_are_valid():
    assert PrivateCostValue.model_validate(_value()).category is None
    assert PrivateCostValue.model_validate(_value(cost_class="financing")).category is None
    v = PrivateCostValue.model_validate(_value(category="loan_fees", cost_class="financing"))
    assert v.category is C.LOAN_FEES
    assert PrivateCostValue.model_validate(_value(category="repairs")).category is C.REPAIRS


def _private(case, value: dict) -> TaxInputFact:
    return TaxInputFact.model_validate(
        {
            "id": uuid4(),
            "property_id": case.facts.property.id,
            "kind": "private_cost",
            "tax_year": 2026,
            "value": value,
        }
    )


@pytest.mark.rule
@pytest.mark.parametrize(
    ("category", "line"),
    [
        ("repairs", "wk.maintenance"),
        ("insurance", "wk.allocable_costs"),
        ("property_management", "wk.non_allocable_costs"),
        ("professional_fees", "wk.other"),
    ],
)
def test_a_categorized_private_cost_lands_on_its_categorys_line(category, line):
    """case_a plus a repair bill of 297.50 paid from another account: the Erhaltungsaufwand
    line grows by 297.50 and names the row among its sources; wk.other does not move."""
    case = next(c for c in DRAFT.cases if c.id == "case_a")
    before = {x.key: x.amount for x in draft(case.facts, 2026, rule_set(2026)).lines}
    row = _private(case, _value(category=category, amount="297.50"))
    facts = case.facts.model_copy(update={"tax_inputs": [*case.facts.tax_inputs, row]})
    after = {x.key: x for x in draft(facts, 2026, rule_set(2026)).lines}
    assert after[line].amount == before.get(line, Decimal("0")) + Decimal("297.50")
    assert ("tax_input", row.id) in cmp.refs(after[line].sources)
    for key, amount in before.items():
        if key != line and key.startswith("wk.") and key != "wk.total":
            assert after[key].amount == amount, key


@pytest.mark.rule
def test_a_categorized_financing_cost_still_lands_on_the_financing_line():
    case = next(c for c in DRAFT.cases if c.id == "case_a")
    row = _private(case, _value(category="loan_fees", cost_class="financing", amount="238.00"))
    facts = case.facts.model_copy(update={"tax_inputs": [*case.facts.tax_inputs, row]})
    lines = {x.key: x for x in draft(facts, 2026, rule_set(2026)).lines}
    assert lines["wk.financing_costs"].amount == Decimal("718.00")
