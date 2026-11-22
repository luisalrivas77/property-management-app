"""Phase 6 step 4's pure depot rules against their fixtures (docs/phases/phase-6.md D165, §7):
depot_lines.yaml (rules.depot.categorise) and holdings_reconcile.yaml
(rules.depot.reconcile_holdings). A case whose `pending` names a later step is skipped."""

from datetime import date
from decimal import Decimal

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import depot, net_worth
from hausbuch.rules import errors as E
from hausbuch.schemas.household import MovementStatus

BUILT = ("phase 6 step 1", "phase 6 step 4")

LINES = phase6.load("depot_lines")
HOLDINGS = phase6.load("holdings_reconcile")


def skip_unless_built(pending: str | None) -> None:
    if pending and not pending.startswith(BUILT):
        pytest.skip(pending)


@pytest.mark.rule
@pytest.mark.parametrize("case", LINES.cases, ids=lambda c: c.id)
def test_categorise(case):
    skip_unless_built(case.pending)
    assert depot.categorise(case.booking_text) is case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", HOLDINGS.cases, ids=lambda c: c.id)
def test_reconcile_holdings(case):
    skip_unless_built(case.pending)
    movements = [
        depot.UnitsMovement(
            asset=m.asset,
            quantity=m.quantity,
            occurred_on=m.occurred_on,
            status=m.status,
            external_ref=m.external_ref,
        )
        for m in case.movements
    ]
    references: dict[str, list[str]] = {}
    for trade in case.report_trades:
        references.setdefault(trade.asset, []).append(trade.reference)
    checks = depot.reconcile_holdings(
        case.as_of, {p.asset: p.units for p in case.declared}, movements, references
    )
    got = [
        {
            "asset": c.asset,
            "listed": c.listed,
            "reconciled": c.reconciled,
            "difference_units": c.difference_units,
            "price_stored": c.price_stored,
            "error": None if c.reconciled else E.DEPOT_HOLDINGS_MISMATCH,
            "missing_references": None if c.reconciled else list(c.missing_references),
        }
        for c in checks
    ]
    want = [p.model_dump() for p in case.expect.positions]
    assert got == want
    assert depot.reconciled_through(case.as_of, checks) == case.expect.reconciled_through


def test_a_proposed_movement_never_counts_but_its_reference_is_present():
    checks = depot.reconcile_holdings(
        date(2026, 6, 30),
        {"a": Decimal("2")},
        [
            depot.UnitsMovement(
                asset="a",
                quantity=Decimal("1"),
                occurred_on=date(2026, 6, 1),
                status=MovementStatus.CONFIRMED,
                external_ref="R1",
            ),
            depot.UnitsMovement(
                asset="a",
                quantity=Decimal("1"),
                occurred_on=date(2026, 6, 2),
                status=MovementStatus.PROPOSED,
                external_ref="R2",
            ),
        ],
        {"a": ["R1", "R2", "R3"]},
    )
    assert [(c.reconciled, c.difference_units, c.missing_references) for c in checks] == [
        (False, Decimal("1"), ("R3",))
    ]


# --- D292: a trade read from its cash statement line (depot_statement_trades.yaml) ----------

STATEMENT = phase6.load("depot_statement_trades")


@pytest.mark.rule
@pytest.mark.parametrize("case", STATEMENT.lines, ids=lambda c: c.id)
def test_statement_trade(case):
    found = depot.statement_trade(case.category, case.amount, case.raw)
    if case.expect is None:
        assert found is None
    else:
        assert found is not None
        assert (found.kind, found.isin, found.units, found.tax_withheld) == (
            case.expect.kind,
            case.expect.isin,
            case.expect.units,
            case.expect.tax_withheld,
        )


@pytest.mark.rule
@pytest.mark.parametrize("case", STATEMENT.windows, ids=lambda c: c.id)
def test_statement_window(case):
    assert depot.statement_window(case.trade_on, case.line_on) is case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", STATEMENT.report_trades, ids=lambda c: c.id)
def test_statement_booked(case):
    trade = depot.ProposedTrade(
        isin=case.trade.isin,
        trade_date=case.trade.trade_date,
        units=case.trade.units,
        amount=Decimal("0"),
        reference="0000000000",
    )
    booked = [(b.isin, b.units, b.value_date) for b in case.booked]
    assert (depot.statement_booked(trade, booked) is not None) is case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", STATEMENT.transit, ids=lambda c: c.id)
def test_in_transit(case):
    found = net_worth.in_transit(
        case.paid_on, case.opened_on, case.through, case.receiver_history, linked=case.linked
    )
    assert found is case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", STATEMENT.transit_to_card, ids=lambda c: c.id)
def test_transit_counted(case):
    assert net_worth.transit_counted(case.amounts, case.card_balance) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", STATEMENT.twins, ids=lambda c: c.id)
def test_statement_twin(case):
    found = depot.statement_twin(
        case.line_reference, case.line_on, case.other_reference, case.trade_on
    )
    assert found is case.expect
