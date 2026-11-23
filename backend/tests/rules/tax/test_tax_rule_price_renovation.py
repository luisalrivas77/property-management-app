"""Phase 3 D287: the seller's renovation inside the purchase price, claimed as the household's
own renovation (price_renovation.yaml), and the draft unchanged to the byte when the claim is
declined or absent. Invented figures only."""

from decimal import Decimal
from uuid import uuid4

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import DraftFixture, load
from test_tax_rule_draft import test_draft as _check_draft

from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.registry import rule_set
from hausbuch.schemas.tax import DraftFacts, TaxInputFact

FIXTURE = load("price_renovation")
assert isinstance(FIXTURE, DraftFixture)
CASE_A = next(c for c in load("anlage_v_2026").cases if c.id == "case_a")


@pytest.mark.rule
@pytest.mark.parametrize("case", FIXTURE.cases, ids=lambda c: c.id)
def test_price_renovation(case):
    _check_draft(case)


def _claim(facts: DraftFacts, status: str = "claimed", supersedes=None, **value) -> TaxInputFact:
    return TaxInputFact.model_validate(
        {
            "id": uuid4(),
            "property_id": facts.property.id,
            "kind": "price_renovation",
            "document_id": facts.split.evidence_document_id,
            "supersedes_id": supersedes,
            "value": {
                "status": status,
                "amount": "11900.00",
                "amount_net": "10000.00",
                "period_start": "2026-07-01",
                "period_end": "2026-09-30",
                "paid_on": "2026-07-01",
            }
            | value,
        }
    )


def _with(facts: DraftFacts, *rows: TaxInputFact) -> DraftFacts:
    return facts.model_copy(update={"tax_inputs": [*facts.tax_inputs, *rows]})


@pytest.mark.rule
@pytest.mark.parametrize("year", [2026, 2027])
def test_declined_or_absent_leaves_the_draft_byte_identical(year):
    """„Nein“ on case A: the declined row supersedes the claim, and the draft serialises to the
    same bytes as the draft that never had a claim, in the year paid and after it."""
    base = draft(CASE_A.facts, year, rule_set(2026))
    claim = _claim(CASE_A.facts)
    declined = _claim(CASE_A.facts, "declined", supersedes=claim.id)
    for facts in (_with(CASE_A.facts, claim, declined), _with(CASE_A.facts, declined)):
        assessed = facts.model_copy(update={"assessed_years": [2026]})
        for f in (facts, assessed):
            got = draft(f, year, rule_set(2026))
            assert got.model_dump_json() == base.model_dump_json()


@pytest.mark.rule
def test_a_claim_on_case_a_moves_its_gross_from_the_basis_to_maintenance():
    """Case A plus a claim of 11,900.00 gross, 10,000.00 net: the basis falls to 222,100.00
    (AfA 2,221.00 instead of 2,340.00), the 15 % limit to 33,315.00 with the claim counted at
    its net, and wk.maintenance rises by the gross (still provisional for the Rücklage)."""
    base = draft(CASE_A.facts, 2026, rule_set(2026))
    claim = _claim(CASE_A.facts)
    got = draft(_with(CASE_A.facts, claim), 2026, rule_set(2026))
    before = {line.key: line for line in base.lines}
    lines = {line.key: line for line in got.lines}
    assert (got.afa.basis, lines["wk.afa"].amount) == (Decimal("222100.00"), Decimal("2221.00"))
    assert lines["wk.maintenance"].amount == before["wk.maintenance"].amount + Decimal("11900.00")
    assert lines["wk.maintenance"].provisional
    assert ("tax_input", claim.id) in cmp.refs(lines["wk.maintenance"].sources)
    assert ("tax_input", claim.id) in cmp.refs(lines["wk.afa"].sources)
    lim = got.limit_15
    assert (lim.basis_building_cost, lim.limit) == (Decimal("222100.00"), Decimal("33315.00"))
    assert lim.used_net == base.limit_15.used_net + Decimal("10000.00")
    assert claim.id in {c.tax_input_id for c in lim.counted} and not lim.exceeded
    delta = Decimal("11900.00") - (Decimal("2340.00") - Decimal("2221.00"))
    assert got.totals.costs == base.totals.costs + delta
    # Paid with the price on 1 Jul 2026: the renovation needs no further proof of payment.
    assert [w.code for w in got.warnings] == [w.code for w in base.warnings]


@pytest.mark.rule
def test_a_kitchen_and_a_claim_both_leave_the_building_share():
    """D80 and D287 together: 234,000.00 − the kitchen's 9,208.33 − 11,900.00 = 212,891.67;
    the land's share is never touched."""
    kitchen = TaxInputFact.model_validate(
        {
            "id": uuid4(),
            "property_id": CASE_A.facts.property.id,
            "kind": "movable_asset",
            "tax_year": 2026,
            "value": {"label": "einbaukueche", "amount": "8500.00"},
        }
    )
    got = draft(_with(CASE_A.facts, kitchen, _claim(CASE_A.facts)), 2026, rule_set(2026))
    assert got.afa.basis == Decimal("212891.67")
    assert got.limit_15.basis_building_cost == Decimal("212891.67")


@pytest.mark.rule
def test_a_withdrawn_claim_is_as_if_none():
    claim = _claim(CASE_A.facts)
    withdrawn = TaxInputFact.model_validate(
        claim.model_dump() | {"id": uuid4(), "supersedes_id": claim.id, "withdrawn": True}
    )
    base = draft(CASE_A.facts, 2026, rule_set(2026))
    got = draft(_with(CASE_A.facts, claim, withdrawn), 2026, rule_set(2026))
    assert got.model_dump_json() == base.model_dump_json()


@pytest.mark.rule
def test_a_taxable_sale_reads_the_price_without_the_claim():
    """§23 (phase 4 sale.py): while the claim counts, its gross is not an acquisition cost, so
    the gain rises by it within the limit; over the limit it is back as a basis addition and
    the gain is as without the claim, counted once either way."""
    from test_perf_sale import SALE, facts_of

    from hausbuch.rules.performance.sale import SaleFacts, sale
    from hausbuch.rules.tax import registry

    case = next(c for c in SALE.cases if c.id == "scenario_c_sale")
    f = facts_of(case)
    tariff = registry.tariff(2030)
    base = sale(f, tariff).gain
    within = sale(SaleFacts(**{**f.__dict__, "price_renovation": Decimal("11900.00")}), tariff)
    over = sale(
        SaleFacts(
            **{
                **f.__dict__,
                "price_renovation": Decimal("11900.00"),
                "basis_additions": Decimal("11900.00"),
            }
        ),
        tariff,
    )
    assert within.gain == base + Decimal("11900.00")
    assert over.gain == base


@pytest.mark.rule
def test_the_projection_hands_the_standing_claim_to_the_sale():
    from hausbuch.rules.performance.projection import _price_renovation

    claim = _claim(CASE_A.facts)
    declined = _claim(CASE_A.facts, "declined", supersedes=claim.id)
    assert _price_renovation(CASE_A.facts) == Decimal("0.00")
    assert _price_renovation(_with(CASE_A.facts, claim)) == Decimal("11900.00")
    assert _price_renovation(_with(CASE_A.facts, claim, declined)) == Decimal("0.00")
