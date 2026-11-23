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


def _moved(ctx, depot, fund, kind, units, on, eur=None, line=None):
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
            transaction_id=line,
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


@pytest.mark.parametrize("sent", [False, True])
def test_a_purchase_after_the_last_cash_statement_is_paid_in(run, read, fresh_workspaces, sent):
    """A depot whose cash statement (300 € paid in on 30 Sep) reaches 30 Sep. A trade
    confirmation buys 5 units for 500 € on 10 Oct; no statement holds its cash yet, so the
    balance still reads 300 € while the 5 units are in today's value: the purchase is money
    paid in, not the depot's own business. Today one unit costs 100 €:
      value 300 + 500 = 800; paid in 300 + 500 = 800 → gain 0.
    With a 500 € bank payment to the depot on 29 Oct (in transit, the statement ending
    before it), both the value and the money paid in grow by 500 €: gain 0 again."""
    ws, _ = fresh_workspaces
    current = _account(
        run, ws, "Girokonto", AccountKind.BANK_CURRENT, iban=CURRENT_IBAN, opening="1000.00"
    )
    depot = _account(run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN)

    def stored(ctx):
        fund = _asset(ctx, kind="etf", isin="IE00BEISPL09", name="Fonds Welt")
        _moved(ctx, depot, fund, "acquisition", "5", date(2026, 10, 10), eur="-500.00")
        _price(ctx, fund, ON, "100", "user")
        cash = _import(ctx, depot, date(2026, 7, 1), date(2026, 9, 30))
        credit = _line(ctx, depot, cash, date(2026, 9, 30), "300.00")
        statement = _import(ctx, current, date(2026, 7, 1), ON)
        debit = _line(ctx, current, statement, PAID_ON, "-500.00", iban=DEPOT_IBAN)
        return credit, debit

    credit, debit = run(ws, stored)
    _book(run, ws, credit, "300.00", C.INTERNAL_TRANSFER_IN)
    if sent:
        _book(run, ws, debit, "-500.00", C.INTERNAL_TRANSFER_OUT)

    [portfolio] = read(ws, assets_performance, ON).portfolios
    [overview] = read(ws, assets_overview, ON, today=ON).accounts
    moved = Decimal("500.00") if sent else Decimal("0.00")
    assert portfolio.figures.value_eur == overview.total_eur == Decimal("800.00") + moved
    kinds = [(f.kind, f.on, f.amount_eur) for f in portfolio.flows]
    assert (K.TRADE, date(2026, 10, 10), Decimal("500.00")) in kinds
    assert (K.IN_TRANSIT in {k for k, _, _ in kinds}) is sent
    figures = portfolio.figures
    assert (figures.paid_in_eur, figures.gain_eur) == (Decimal("800.00") + moved, Decimal("0.00"))


def test_a_purchase_settled_after_the_anchor_day_is_not_counted_twice(run, read, fresh_workspaces):
    """A trade confirmation of 29 Jun buys 5 units for 500 €; its cash line is booked on
    1 Jul, after the anchor of 800 € on 30 Jun (a balance by booking date, so it still holds
    the 500 €). The 5 units are in the start value, and the balance after 1 Jul is 300 €:
    the start takes the line off, 800 − 500 + 5 × 100 = 800. Today one unit costs 100 €:
      value 300 + 500 = 800; paid in 800 → gain 0 (it read −500 € before)."""
    ws, _ = fresh_workspaces
    depot = _account(
        run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN, opening="800.00", on=date(2026, 6, 30)
    )

    def stored(ctx):
        fund = _asset(ctx, kind="etf", isin="IE00BEISPL09", name="Fonds Welt")
        cash = _import(ctx, depot, date(2026, 7, 1), date(2026, 10, 20))
        settled = _line(ctx, depot, cash, date(2026, 7, 1), "-500.00")
        _moved(ctx, depot, fund, "acquisition", "5", date(2026, 6, 29), eur="-500.00", line=settled)
        _price(ctx, fund, date(2026, 6, 30), "100", "user")
        _price(ctx, fund, ON, "100", "user")

    run(ws, stored)
    [portfolio] = read(ws, assets_performance, ON).portfolios
    assert portfolio.figures.value_eur == Decimal("800.00")
    assert [(f.kind, f.on, f.amount_eur) for f in portfolio.flows] == [
        (K.START, date(2026, 6, 30), Decimal("800.00")),
    ]
    assert portfolio.figures.gain_eur == Decimal("0.00")


def test_a_start_priced_only_a_month_before_the_anchor_is_unknown(run, read, fresh_workspaces):
    """A depot anchored at 300 € on 30 Jun holding 10 units bought on 15 May; the fund's only
    price before the anchor is of 31 May, 30 days earlier (stale, D167 (2)). The start value
    would be priced on another day than the anchor, so it is unknown: no gain, the start
    counted as a payment whose euros are missing."""
    ws, _ = fresh_workspaces
    depot = _account(
        run, ws, "Depot", AccountKind.DEPOT, iban=DEPOT_IBAN, opening="300.00", on=date(2026, 6, 30)
    )

    def stored(ctx):
        fund = _asset(ctx, kind="etf", isin="IE00BEISPL09", name="Fonds Welt")
        _moved(ctx, depot, fund, "acquisition", "10", date(2026, 5, 15), eur="-1000.00")
        _price(ctx, fund, date(2026, 5, 31), "100", "user")
        _price(ctx, fund, ON, "120", "user")
        cash = _import(ctx, depot, date(2026, 7, 1), date(2026, 10, 20))
        return _line(ctx, depot, cash, date(2026, 7, 15), "500.00")

    credit = run(ws, stored)
    _book(run, ws, credit, "500.00", C.INTERNAL_TRANSFER_IN)
    [portfolio] = read(ws, assets_performance, ON).portfolios
    assert (K.START, date(2026, 6, 30), None) in [
        (f.kind, f.on, f.amount_eur) for f in portfolio.flows
    ]
    figures = portfolio.figures
    assert figures.gain_eur is None and figures.paid_in_eur is None
    assert figures.no_rate is NoRateReason.UNPRICED and figures.unpriced_flows == 1


def test_coins_given_or_paid_count_at_the_days_price_on_a_wallet(run, read, fresh_workspaces):
    """A wallet: 2 SOL received on 1 Oct and answered „Geschenk oder Zahlung erhalten“ (a
    typed cost of 150 € for the lot) are paid in at that day's price, 2 × 100 = 200 €; 0.5 SOL
    sent on 15 Oct and answered „Bezahlung“ are taken out at 0.5 × 120 = 60 €. A swap of
    0.5 SOL for 100 USDC on 20 Oct moves nothing in or out, nor does a dust receipt booked
    as a 0 € acquisition (D262, worth 0.001 € that day). Without the answers both readings
    left the value moving with no money in or out."""
    from db.test_services_holdings import _account as _holdings_account
    from db.test_services_holdings import _movement
    from db.test_services_holdings import _price as _coin_price
    from db.test_wallet_dust_and_swap import _address, _coin, _group

    from hausbuch.schemas.wallet_cards import WalletAnswer, WalletGroupAnswerBody
    from hausbuch.services.wallet_groups import answer_group

    ws, _ = fresh_workspaces
    day = {n: datetime(2026, 10, n, 12, tzinfo=UTC) for n in (1, 5, 15, 20)}

    def stored(ctx):
        wallet = _holdings_account(ctx, AccountKind.WALLET, "Wallet")
        sol = _coin(ctx, "SOL", "Solana")
        usdc = _coin(
            ctx, "USDC", "USD Coin (Test)", contract="UsdcTestMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
        )
        gift = _group(ctx, wallet, [(sol, "2", "uncategorized")], at=day[1])
        paid = _group(ctx, wallet, [(sol, "-0.5", "uncategorized")], at=day[15])
        _group(
            ctx,
            wallet,
            [(sol, "-0.5", "disposal"), (usdc, "100", "acquisition")],
            kind="swap",
            status="confirmed",
            at=day[20],
        )
        dust = _group(ctx, wallet, [], status="confirmed", at=day[5])
        _movement(ctx, wallet, sol, "0.00001", day[5], address_id=_address(ctx, wallet),
                  kind="acquisition", group_id=dust, eur_amount=Decimal("0"))  # fmt: skip
        for on, price in ((date(2026, 10, 1), "100"), (date(2026, 10, 5), "100")):
            _coin_price(ctx, sol, on, price)
        _coin_price(ctx, sol, date(2026, 10, 15), "120")
        _coin_price(ctx, sol, date(2026, 10, 20), "120")
        _coin_price(ctx, sol, ON, "110")
        _coin_price(ctx, usdc, ON, "1")
        return wallet, gift, paid

    wallet, gift, paid = run(ws, stored)
    run(
        ws,
        answer_group,
        gift,
        WalletGroupAnswerBody(answer=WalletAnswer.GIFT, cost_eur=Decimal("150.00")),
    )
    run(ws, answer_group, paid, WalletGroupAnswerBody(answer=WalletAnswer.PAYMENT))

    seen = read(ws, assets_performance, ON)
    [portfolio] = seen.portfolios
    [overview] = read(ws, assets_overview, ON, today=ON).accounts
    assert portfolio.account_id == wallet
    assert portfolio.figures.value_eur == overview.total_eur
    assert [(f.kind, f.on, f.amount_eur) for f in portfolio.flows] == [
        (K.UNITS, date(2026, 10, 1), Decimal("200.00")),
        (K.UNITS, date(2026, 10, 15), Decimal("-60.00")),
    ]
    figures = portfolio.figures
    assert (figures.paid_in_eur, figures.taken_out_eur) == (Decimal("200.00"), Decimal("60.00"))
    assert figures.gain_eur == overview.total_eur + Decimal("60.00") - Decimal("200.00")
    assert seen.crypto == figures


def test_a_coin_withdrawal_answered_as_a_disposal_is_taken_out(run, read, fresh_workspaces):
    """An exchange: 1 SOL bought for 100 € (a confirmed trade's leg stays inside), then a
    proposed coin withdrawal of 0.4 SOL on 10 Oct answered as a disposal: the coins went to
    someone else, taken out at 0.4 × 125 = 50 €. A disposal answered from an unrecognised row
    stays inside (it may be a trade's leg)."""
    from db.test_services_holdings import _account as _holdings_account
    from db.test_services_holdings import _movement
    from db.test_services_holdings import _price as _coin_price
    from db.test_wallet_dust_and_swap import _coin

    from hausbuch.schemas.household import AnswerMovementBody, MovementKind
    from hausbuch.services.exchange.imports import answer_movement

    ws, _ = fresh_workspaces

    def stored(ctx):
        exchange = _holdings_account(ctx, AccountKind.EXCHANGE, "Börse")
        sol = _coin(ctx, "SOL", "Solana")
        euro = _asset(ctx, kind="cash", symbol="EUR", name="Euro")
        _movement(ctx, exchange, euro, "100", datetime(2026, 9, 1, 9, tzinfo=UTC),
                  kind="internal_transfer_in", eur_amount=Decimal("100"))  # fmt: skip
        _movement(ctx, exchange, euro, "-100", datetime(2026, 9, 2, 9, tzinfo=UTC),
                  kind="disposal", eur_amount=Decimal("-100"))  # fmt: skip
        _movement(ctx, exchange, sol, "1", datetime(2026, 9, 2, 9, tzinfo=UTC),
                  kind="acquisition", eur_amount=Decimal("-100"))  # fmt: skip
        out = _movement(ctx, exchange, sol, "-0.4", datetime(2026, 10, 10, 9, tzinfo=UTC),
                        kind="internal_transfer_out", status="proposed")  # fmt: skip
        odd = _movement(ctx, exchange, sol, "-0.1", datetime(2026, 10, 12, 9, tzinfo=UTC),
                        kind="uncategorized", status="proposed")  # fmt: skip
        _coin_price(ctx, sol, date(2026, 10, 10), "125")
        _coin_price(ctx, sol, date(2026, 10, 12), "125")
        _coin_price(ctx, sol, ON, "130")
        return exchange, out, odd

    exchange, out, odd = run(ws, stored)
    for movement in (out, odd):
        run(ws, answer_movement, movement, AnswerMovementBody(kind=MovementKind.DISPOSAL))

    [portfolio] = read(ws, assets_performance, ON).portfolios
    flows = [(f.kind, f.on, f.amount_eur) for f in portfolio.flows]
    assert (K.UNITS, date(2026, 10, 10), Decimal("-50.00")) in flows
    assert not any(on == date(2026, 10, 12) for _, on, _ in flows)
    assert portfolio.figures.taken_out_eur == Decimal("50.00")
