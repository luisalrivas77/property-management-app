"""A depot's payments in and out from its cash statement (docs/phases/phase-10.md §7.6 Anlagen,
build decision (b)): a cash line booked as a transfer is money paid in, a line nobody booked yet
is counted as unbooked and is no payment, and money on its way to the depot (phase 6 D292) is
paid in on its payment day, so the depot's value (its net-worth balance and the money in
transit) and the money paid in agree. Invented IBANs and amounts only."""

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from db.test_services_net_worth import ON, _account, _asset, _import, _price
from db.test_services_net_worth_transit import (
    CURRENT_IBAN,
    DEPOT_IBAN,
    PAID_ON,
    _book,
    _line,
)
from sqlalchemy import insert

from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import holding_statement as holding_table
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


def _moved(ctx, depot, fund, kind, units, on, eur=None):
    ctx.conn.execute(
        insert(movement_table).values(
            workspace_id=ctx.workspace_id,
            account_id=depot,
            asset_id=fund,
            kind=kind,
            quantity=Decimal(units),
            eur_amount=None if eur is None else Decimal(eur),
            occurred_at=datetime.combine(on, time(10), UTC),
            source="user",
            status="confirmed",
            created_at=ctx.now,
        )
    )


def test_a_depots_start_value_holds_what_came_before_its_first_statement(
    run, read, fresh_workspaces
):
    """A depot whose cash balance is anchored at 300 € on 30 Jun, holding 12 units of a fund
    (10 bought, 2 moved in from another depot, both before that day) worth 110 € each then:
    its first money in is 300 + 12 × 110 = 1,620 €, and neither the earlier purchase nor the
    earlier move is counted again. A 500 € Einlage on the statement is paid in; a purchase
    on 1 Aug is the depot's own business and no payment (its cash line is left out of this
    invented statement, so the cash stays 800 €). Today one unit costs 120 €:
      value 800 + 13 × 120 = 2,360; paid in 1,620 + 500 = 2,120 → gain 240."""
    ws, _ = fresh_workspaces
    depot = _account(
        run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN, opening="300.00", on=date(2026, 6, 30)
    )

    def stored(ctx):
        fund = _asset(ctx, kind="etf", isin="IE00BEISPL09", name="Fonds Welt")
        _moved(ctx, depot, fund, "acquisition", "10", date(2026, 6, 15), eur="-1000.00")
        _moved(ctx, depot, fund, "internal_transfer_in", "2", date(2026, 6, 20))
        _moved(ctx, depot, fund, "acquisition", "1", date(2026, 8, 1), eur="-115.00")
        _price(ctx, fund, date(2026, 6, 30), "110", "user")
        _price(ctx, fund, ON, "120", "user")
        cash = _import(ctx, depot, date(2026, 7, 1), date(2026, 10, 20))
        return _line(ctx, depot, cash, date(2026, 7, 15), "500.00")

    deposit = run(ws, stored)
    _book(run, ws, deposit, "500.00", C.INTERNAL_TRANSFER_IN)

    [portfolio] = read(ws, assets_performance, ON).portfolios
    [overview] = read(ws, assets_overview, ON, today=ON).accounts
    assert portfolio.figures.value_eur == overview.total_eur == Decimal("2360.00")
    assert [(f.kind, f.on, f.amount_eur) for f in portfolio.flows] == [
        (K.START, date(2026, 6, 30), Decimal("1620.00")),
        (K.TRANSFER, date(2026, 7, 15), Decimal("500.00")),
    ]
    figures = portfolio.figures
    assert (figures.start, figures.paid_in_eur, figures.taken_out_eur, figures.gain_eur) == (
        date(2026, 6, 30),
        Decimal("2120.00"),
        Decimal("0.00"),
        Decimal("240.00"),
    )


def _report(ctx, depot, fund, as_of, units):
    """A current depot report's position, as finish_report stores it."""
    ctx.conn.execute(
        insert(holding_table).values(
            workspace_id=ctx.workspace_id,
            account_id=depot,
            asset_id=fund,
            as_of=as_of,
            declared_units=Decimal(units),
            source="report",
            created_at=ctx.now,
        )
    )


def test_a_fund_its_report_contradicts_leaves_no_gain(run, read, fresh_workspaces):
    """A depot without cash statements bought 10 units for 1,000 €; its report says 12, so the
    overview leaves the fund unvalued and the depot's value reads 0. Its gain would read
    −1,000 €: no gain, no percent and no rate are shown, with the reason, on the depot and
    on the ETF total; the value stays the overview's."""
    ws, _ = fresh_workspaces
    depot = _account(run, ws, "Depot", AccountKind.DEPOT)

    def stored(ctx):
        fund = _asset(ctx, kind="etf", isin="IE00BEISPL09", name="Fonds Welt")
        _moved(ctx, depot, fund, "acquisition", "10", date(2026, 7, 1), eur="-1000.00")
        _price(ctx, fund, ON, "110", "user")
        _report(ctx, depot, fund, date(2026, 9, 30), "12")

    run(ws, stored)
    seen = read(ws, assets_performance, ON)
    [portfolio] = seen.portfolios
    [overview] = read(ws, assets_overview, ON, today=ON).accounts
    assert portfolio.figures.value_eur == overview.total_eur == Decimal("0.00")
    for figures in (portfolio.figures, seen.etf, seen.invested):
        assert (figures.gain_eur, figures.gain_ratio, figures.yearly_return) == (None,) * 3
        assert (figures.paid_in_eur, figures.taken_out_eur) == (None, None)
        assert figures.no_rate is NoRateReason.UNVALUED
        assert figures.unvalued_positions == 1 and figures.unpriced_flows == 0
    # The crypto total holds no such portfolio.
    assert seen.crypto.unvalued_positions == 0 and seen.crypto.no_rate is NoRateReason.NO_FLOWS
