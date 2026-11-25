"""Route 3's invested mix read from today's net worth (phase 10 §7.11 (c) 3, D310 amended;
services.goal._invested): the depots in the reserve left out by today's lines, the crypto
lines by the symbol of their coin. The net worth is built by hand; the asset rows are
invented, no chain is read."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import asset as asset_table
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import Reserve, ReserveAccount, ReserveOrigin, ReserveState
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.net_worth import NetWorth, NetWorthLine, NetWorthLineKind
from hausbuch.services import goal, plan_inputs

pytestmark = pytest.mark.db

TODAY = date(2027, 4, 10)
D = Decimal
DEPOT, CRYPTO = NetWorthClass.DEPOT, NetWorthClass.CRYPTO


def _line(cls: NetWorthClass, account: UUID, value: str | None, asset: UUID | None = None):
    return NetWorthLine(
        kind=NetWorthLineKind.ACCOUNT if asset is None else NetWorthLineKind.POSITION,
        net_worth_class=cls,
        label="Muster",
        account_id=account,
        asset_id=asset,
        value_eur=None if value is None else D(value),
    )


def _net_worth(lines: list[NetWorthLine]) -> NetWorth:
    by_class: dict[NetWorthClass, Decimal] = {}
    for line in lines:
        if line.value_eur is not None:
            by_class[line.net_worth_class] = (
                by_class.get(line.net_worth_class, D("0")) + line.value_eur
            )
    total = sum(by_class.values(), D("0"))
    return NetWorth(
        on=TODAY,
        total=total,
        by_class=by_class,
        by_owner={},
        property_estimate=D("0"),
        loan_balances=D("0"),
        property_net=D("0"),
        lines=lines,
    )


def _asset(conn, ws, symbol: str | None, *, spam: bool = False, twin: UUID | None = None):
    """A coin without a chain; a token (a twin, spam or no symbol) on Ethereum by an invented
    contract, as phase 7's asset check asks."""
    asset_id = uuid4()
    token = spam or twin is not None or symbol is None
    conn.execute(
        insert(asset_table).values(
            id=asset_id,
            workspace_id=ws,
            kind="crypto",
            symbol=symbol,
            name=f"Muster {symbol or 'ohne Symbol'}",
            spam=spam,
            priced_as_asset_id=twin,
            chain="ethereum" if token else None,
            contract_address=f"0x{asset_id.hex:0>40}" if token else None,
        )
    )
    return asset_id


def test_the_depots_in_the_reserve_and_the_coins_by_symbol(app_engine, fresh_workspaces):
    ws = fresh_workspaces[0]
    reserve_depot, other_depot, wallet, exchange = uuid4(), uuid4(), uuid4(), uuid4()
    plan_inputs.clear()
    with workspace_transaction(app_engine, ws) as conn:
        eth = _asset(conn, ws, "ETH")
        weth = _asset(conn, ws, "WETH", twin=eth)
        btc = _asset(conn, ws, "BTC")
        junk = _asset(conn, ws, "JUNK", spam=True)
        nameless = _asset(conn, ws, None)
        now = _net_worth(
            [
                # the reserve's depot: cash and a fund, today's values
                _line(DEPOT, reserve_depot, "100.00"),
                _line(DEPOT, reserve_depot, "900.00", uuid4()),
                _line(DEPOT, other_depot, "2000.00", uuid4()),
                _line(CRYPTO, wallet, "300.00", eth),
                _line(CRYPTO, exchange, "200.00", eth),  # one coin on two accounts
                _line(CRYPTO, wallet, "50.00", weth),  # priced as ETH
                _line(CRYPTO, wallet, "400.00", btc),
                _line(CRYPTO, wallet, None, btc),  # not valued today: nowhere
                _line(CRYPTO, wallet, "7.00", junk),  # spam: nowhere
                _line(CRYPTO, wallet, "9.00", nameless),  # no symbol: no series to look up
                _line(CRYPTO, exchange, "25.00"),  # the exchange's euros
            ]
        )
        # The reserve's balance is the stored month-end's (another day's price): never
        # subtracted from today's figure.
        reserve = Reserve(
            accounts=[
                ReserveAccount(
                    account_id=reserve_depot,
                    kind=AccountKind.DEPOT,
                    balance=D("950.00"),
                    as_of=date(2027, 3, 31),
                    origin=ReserveOrigin.ADDED,
                )
            ],
            amount=D("950.00"),
            need=D("0.00"),
            target_months=D("6"),
            target=D("0.00"),
            gap=D("0.00"),
            state=ReserveState.REACHED,
        )
        inputs = plan_inputs.PlanInputs(conn, TODAY)
        got = goal._invested(inputs, now, goal._reserve_depots(reserve))
    assert got.funds == D("2000.00")
    assert dict(got.coins) == {"ETH": D("550.00"), "BTC": D("400.00"), "EUR": D("25.00")}


def test_a_household_whose_only_depot_is_in_the_reserve_has_no_funds(app_engine, fresh_workspaces):
    ws = fresh_workspaces[0]
    depot = uuid4()
    plan_inputs.clear()
    with workspace_transaction(app_engine, ws) as conn:
        now = _net_worth([_line(DEPOT, depot, "1030.00", uuid4())])
        reserve = Reserve(
            accounts=[
                ReserveAccount(
                    account_id=depot,
                    kind=AccountKind.DEPOT,
                    balance=D("1000.00"),  # the month-end's price
                    as_of=date(2027, 3, 31),
                    origin=ReserveOrigin.ADDED,
                )
            ],
            amount=D("1000.00"),
            need=D("0.00"),
            target_months=D("6"),
            target=D("0.00"),
            gap=D("0.00"),
            state=ReserveState.REACHED,
        )
        got = goal._invested(
            plan_inputs.PlanInputs(conn, TODAY), now, goal._reserve_depots(reserve)
        )
    assert (got.funds, dict(got.coins), got.total) == (D("0.00"), {}, D("0.00"))
