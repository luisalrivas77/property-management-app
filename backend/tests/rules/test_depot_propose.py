"""What code proposes from a depot reading (rules.depot.propose; docs/phases/phase-6.md D165
(2), (3), step 4; fixture tests/fixtures/phase6/quirion_documents.yaml, its `proposes`)."""

from decimal import Decimal

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import depot

CASES = phase6.load("quirion_documents").cases
PROPOSING = [case for case in CASES if case.proposes is not None]


def _values(case) -> dict:
    return {name: value.value for name, value in case.expect.items()}


@pytest.mark.rule
@pytest.mark.parametrize("case", PROPOSING, ids=lambda c: c.id)
def test_the_reading_proposes_what_the_fixture_names(case):
    proposal = depot.propose(case.kind, _values(case))
    assert proposal is not None
    assert proposal.warnings == []
    want = case.proposes
    got_movements = [m.model_dump(exclude={"name"}) for m in proposal.movements]
    assert got_movements == [m.model_dump() for m in want.movements]
    got_holdings = [h.model_dump(exclude={"name"}) for h in proposal.holdings]
    assert got_holdings == [h.model_dump() for h in want.holdings]
    assert [t.model_dump() for t in proposal.trades] == [t.model_dump() for t in want.trades]


def test_every_kind_with_a_proposal_in_the_fixture_is_covered():
    assert {case.kind for case in PROPOSING} == {
        "trade_confirmation",
        "distribution_note",
        "depot_report",
        "securities_transfer",
    }


def test_a_reading_without_its_isin_or_date_proposes_nothing():
    assert depot.propose("trade_confirmation", {"side": "Kauf", "units": "1"}) is None
    assert depot.propose("distribution_note", {"isin": "IE00BEISPL01"}) is None
    assert depot.propose("depot_report", {"positions": []}) is None
    assert depot.propose("securities_transfer", {"direction": "Umbuchung"}) is None
    assert depot.propose("general", {}) is None


def test_a_trade_whose_net_does_not_add_up_is_proposed_with_a_warning():
    values = {
        "isin": "IE00BEISPL01",
        "side": "Kauf",
        "trade_date": "2026-05-14",
        "units": "1.0000",
        "gross_amount": "100.00",
        "fees": "1.00",
        "taxes": "0.00",
        "net_amount": "100.00",
    }
    proposal = depot.propose("trade_confirmation", values)
    assert proposal.warnings == [depot.NET_MISMATCH]
    [movement] = proposal.movements
    assert movement.eur_amount == Decimal("-100.00")


def test_an_unreadable_report_row_is_left_out_with_a_warning():
    values = {
        "report_date": "2026-06-30",
        "positions": ["IE00BEISPL01 | Beispiel ETF | 1,0000 | 10,00 | 10,00 | EUR", "unbekannt"],
        "trades": [],
    }
    proposal = depot.propose("depot_report", values)
    assert proposal.warnings == [depot.ROW_UNREADABLE]
    assert [h.isin for h in proposal.holdings] == ["IE00BEISPL01"]
