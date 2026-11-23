"""A depot's payments in and out from its cash statement (docs/phases/phase-10.md §7.6 Anlagen,
build decision (b)): a cash line booked as a transfer is money paid in, a line nobody booked yet
is counted as unbooked and is no payment, and money on its way to the depot (phase 6 D292) is
paid in on its payment day, so the depot's value (its net-worth balance and the money in
transit) and the money paid in agree. Invented IBANs and amounts only."""

from datetime import date
from decimal import Decimal

import pytest
from db.test_services_net_worth import ON, _account, _import
from db.test_services_net_worth_transit import (
    CURRENT_IBAN,
    DEPOT_IBAN,
    PAID_ON,
    _book,
    _line,
)

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.portfolio import NoRateReason, PortfolioFlowKind
from hausbuch.services.portfolio_performance import assets_performance
from hausbuch.services.prices.overview import assets_overview

pytestmark = pytest.mark.db

C = AllocationCategory
K = PortfolioFlowKind


def test_a_depots_cash_transfers_and_money_in_transit_are_paid_in(run, read, fresh_workspaces):
    ws, other = fresh_workspaces
    current = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=CURRENT_IBAN, opening="1000.00"
    )
    depot = _account(run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN)

    def stored(ctx):
        statement = _import(ctx, current, date(2026, 7, 1), ON)
        debit = _line(ctx, current, statement, PAID_ON, "-500.00", iban=DEPOT_IBAN)
        cash = _import(ctx, depot, date(2026, 7, 1), date(2026, 10, 20))
        credit = _line(ctx, depot, cash, date(2026, 10, 1), "200.00")
        return debit, credit

    debit, credit = run(ws, stored)
    _book(run, ws, debit, "-500.00", C.INTERNAL_TRANSFER_OUT)

    # The depot's 200 € line is not booked yet: no payment, named as unbooked.
    seen = read(ws, assets_performance, ON)
    [portfolio] = seen.portfolios
    assert portfolio.account_id == depot and portfolio.unbooked_lines == 1
    [moving] = portfolio.flows
    assert (moving.kind, moving.on, moving.amount_eur) == (
        K.IN_TRANSIT,
        PAID_ON,
        Decimal("500.00"),
    )
    assert moving.transaction_id == debit and not moving.internal

    # Booked as an Einlage it is money paid in; the value is the overview's depot total.
    _book(run, ws, credit, "200.00", C.INTERNAL_TRANSFER_IN)
    seen = read(ws, assets_performance, ON)
    [portfolio] = seen.portfolios
    [overview] = read(ws, assets_overview, ON, today=ON).accounts
    assert portfolio.figures.value_eur == overview.total_eur == Decimal("700.00")
    assert portfolio.unbooked_lines == 0
    assert [(f.kind, f.on, f.amount_eur) for f in portfolio.flows] == [
        (K.TRANSFER, date(2026, 10, 1), Decimal("200.00")),
        (K.IN_TRANSIT, PAID_ON, Decimal("500.00")),
    ]
    figures = portfolio.figures
    assert (figures.start, figures.paid_in_eur, figures.gain_eur) == (
        date(2026, 10, 1),
        Decimal("700.00"),
        Decimal("0.00"),
    )
    assert figures.no_rate is NoRateReason.SHORT_HISTORY and figures.yearly_return is None
    assert seen.etf == figures
    assert seen.crypto.value_eur == Decimal("0.00")

    assert read(other, assets_performance, ON).portfolios == []
