"""rules/lots.py against lots.yaml and holding_period.yaml (docs/phases/phase-7.md §8, D184 (4),
D188): FIFO per account and asset over real AssetMovement and TransferLink rows built from
each fixture event, the Berlin day and the one-year holding period. The cases' `pending`
markers name step 6, which is this one, so they run. Plus what the fixtures cannot hold: the
price paths of a cost, a swap group's fee movement, links that move nothing, the shortfall,
the readings of the step 6 report, and Q-O92's weekend anniversary."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import fixture_models_phase7 as phase7
import pytest

from hausbuch.rules import lots as L
from hausbuch.schemas.chain import LotSource
from hausbuch.schemas.household import (
    AssetMovement,
    MovementKind,
    MovementSource,
    MovementStatus,
    PricePoint,
    PriceSourceKind,
    TransferLink,
    TransferLinkKind,
    TransferMatchedBy,
)

LOTS = phase7.load("lots")
PERIOD = phase7.load("holding_period")
WORKSPACE = uuid4()
D = Decimal
K = MovementKind


def _live(case) -> None:
    if case.pending:
        pytest.skip(case.pending)


def movement(
    kind: MovementKind,
    quantity: str | Decimal,
    at: datetime,
    *,
    account: UUID,
    asset: UUID,
    eur_amount: Decimal | None = None,
    fee_eur: Decimal | None = None,
    group: UUID | None = None,
    superseded: bool = False,
    status: MovementStatus = MovementStatus.CONFIRMED,
    index: str | None = None,
) -> AssetMovement:
    return AssetMovement(
        id=uuid4(),
        workspace_id=WORKSPACE,
        created_at=datetime(2027, 1, 1, tzinfo=UTC),
        account_id=account,
        asset_id=asset,
        kind=kind,
        quantity=D(quantity),
        eur_amount=eur_amount,
        fee_eur=fee_eur,
        occurred_at=at,
        source=MovementSource.USER,
        status=status,
        superseded_by=uuid4() if superseded else None,
        group_id=group,
        index=index,
    )


def link(
    out: AssetMovement,
    into: AssetMovement,
    kind: TransferLinkKind,
    *,
    superseded: bool = False,
    created_at: datetime = datetime(2027, 1, 1, tzinfo=UTC),
) -> TransferLink:
    return TransferLink(
        id=uuid4(),
        workspace_id=WORKSPACE,
        created_at=created_at,
        kind=kind,
        out_movement_id=out.id,
        in_movement_id=into.id,
        matched_by=TransferMatchedBy.MATCHER,
        superseded_by=uuid4() if superseded else None,
    )


def price(asset: UUID, on: date, eur: str) -> PricePoint:
    return PricePoint(
        asset_id=asset,
        on=on,
        price_eur=D(eur),
        source=PriceSourceKind.COINGECKO_DAILY,
        fetched_at=datetime.combine(on, datetime.min.time(), UTC),
    )


# --- lots.yaml ---------------------------------------------------------------------------


class _Names:
    def __init__(self) -> None:
        self.ids: dict[str, UUID] = {}

    def __call__(self, name: str) -> UUID:
        return self.ids.setdefault(name, uuid4())

    def name(self, id_: UUID) -> str:
        return next(n for n, i in self.ids.items() if i == id_)


def _ledger(case):
    """The case's events as the rows the service will pass: an acquisition's EUR leg signed
    like the account's cash (BISON's import: negative), a reward priced by the day's price
    row, a transfer, wrap or bridge as an out-leg, an in-leg and their link."""
    accounts, assets = _Names(), _Names()
    movements: list[AssetMovement] = []
    links: list[TransferLink] = []
    prices: list[PricePoint] = []
    by_event: dict[str, AssetMovement] = {}
    for ev in case.events:
        account, asset = accounts(ev.account), assets(ev.asset or "X")
        fee = ev.fee_eur or None
        cost = None if ev.cost_eur is None else -ev.cost_eur
        common = {"account": account, "asset": asset, "superseded": ev.superseded}
        if ev.kind == "acquisition":
            m = movement(K.ACQUISITION, ev.units, ev.at, eur_amount=cost, fee_eur=fee, **common)
        elif ev.kind == "swap_in":
            m = movement(
                K.ACQUISITION,
                ev.units,
                ev.at,
                eur_amount=cost,
                fee_eur=fee,
                group=uuid4(),
                **common,
            )
        elif ev.kind == "reward":
            m = movement(K.STAKING_REWARD, ev.units, ev.at, **common)
            if ev.cost_eur is not None:
                prices.append(price(asset, L.berlin_day(ev.at), str(ev.cost_eur / ev.units)))
        elif ev.kind in ("disposal", "fee", "lp_deposit"):
            kind = {"disposal": K.DISPOSAL, "fee": K.FEE, "lp_deposit": K.LP_DEPOSIT}[ev.kind]
            m = movement(kind, -ev.units, ev.at, **common)
        elif ev.kind == "lp_withdrawal":
            m = movement(K.LP_WITHDRAWAL, ev.units, ev.at, **common)
        else:  # transfer_out, wrap, bridge
            m = movement(K.INTERNAL_TRANSFER_OUT, -ev.units, ev.at, **common)
            into = movement(
                K.INTERNAL_TRANSFER_IN,
                ev.units,
                ev.at,
                account=accounts(ev.to_account) if ev.to_account else account,
                asset=assets(ev.to_asset) if ev.to_asset else asset,
                superseded=ev.superseded,
            )
            movements.append(into)
            kind = {
                "transfer_out": TransferLinkKind.CHAIN,
                "wrap": TransferLinkKind.WRAP,
                "bridge": TransferLinkKind.BRIDGE,
            }[ev.kind]
            links.append(link(m, into, kind))
        movements.append(m)
        by_event[ev.name] = m
    return movements, links, prices, accounts, assets, by_event


@pytest.mark.parametrize("case", LOTS.cases, ids=lambda c: c.id)
def test_lots_match_the_fixture(case):
    _live(case)
    movements, links, prices, accounts, assets, by_event = _ledger(case)
    on = max(L.berlin_day(ev.at) for ev in case.events)
    got = L.book(movements, links, prices, on)
    assert got.unfilled == {}
    open_lots = L.lots(movements, links, prices, on)
    assert open_lots == [lot for lot in got.lots if lot.units_open > 0]
    has_assets = any(e.asset for e in case.events)
    named = sorted(
        (
            phase7.LotExpect(
                account=accounts.name(lot.account_id),
                asset=assets.name(lot.asset_id) if has_assets else None,
                acquired_on=lot.acquired_on,
                units_open=lot.units_open,
                cost_eur=lot.cost_eur,
                source=lot.source,
                tax_free_from=lot.tax_free_from,
                moved=bool(lot.moved_through),
            )
            for lot in open_lots
        ),
        key=lambda x: (x.account, x.asset or "", x.acquired_on),
    )
    assert named == case.expect_lots

    every = {lot.id: lot for lot in got.lots}
    uses = [
        phase7.UseExpect(
            event=ev.name,
            acquired_on=every[use.lot_id].acquired_on,
            units=use.units,
            cost_eur=use.cost_eur,
        )
        for ev in case.events
        for use in got.uses.get(by_event[ev.name].id, [])
    ]
    assert uses == case.expect_uses


def test_rebooking_changes_the_lots_nothing_is_cached():
    before = next(c for c in LOTS.cases if c.id == "before_rebooking")
    after = next(c for c in LOTS.cases if c.id == "after_rebooking_superseded")
    assert [e.name for e in before.events] == [e.name for e in after.events]
    assert before.expect_lots != after.expect_lots


def test_a_moved_lot_records_its_link_and_keeps_its_origin():
    case = next(c for c in LOTS.cases if c.id == "fifo_transfer_disposal_reward_fee")
    movements, links, prices, accounts, _, by_event = _ledger(case)
    got = L.lots(movements, links, prices, date(2026, 11, 30))
    wallet = [lot for lot in got if lot.account_id == accounts("wallet") and lot.moved_through]
    assert {tuple(lot.moved_through) for lot in wallet} == {(links[0].id,)}
    assert {lot.movement_id for lot in wallet} == {by_event["buy_feb"].id, by_event["buy_jun"].id}
    assert {lot.acquired_at for lot in wallet} == {
        by_event["buy_feb"].occurred_at,
        by_event["buy_jun"].occurred_at,
    }
    # The same ledger read twice gives the same lot ids (derived, never stored).
    assert [lot.id for lot in got] == [
        lot.id for lot in L.lots(movements, links, prices, date(2026, 11, 30))
    ]


def test_on_reads_only_the_movements_of_its_berlin_day_and_before():
    case = next(c for c in LOTS.cases if c.id == "fifo_transfer_disposal_reward_fee")
    movements, links, prices, accounts, _, _ = _ledger(case)
    sept = L.lots(movements, links, prices, date(2026, 9, 30))
    assert [(lot.acquired_on, lot.units_open) for lot in sept] == [
        (date(2026, 2, 1), D(10)),
        (date(2026, 6, 1), D(10)),
        (date(2026, 9, 1), D(5)),
    ]
    assert L.lots(movements, links, prices, date(2026, 1, 31)) == []


# --- holding_period.yaml -----------------------------------------------------------------


@pytest.mark.parametrize("case", PERIOD.days, ids=lambda c: c.id)
def test_the_berlin_day_and_tax_free_from(case):
    _live(case)
    day = L.berlin_day(case.acquired_at)
    assert day == case.expect_acquired_on
    assert L.tax_free_from(day) == case.expect_tax_free_from


@pytest.mark.parametrize("case", PERIOD.sales, ids=lambda c: c.id)
def test_a_sale_inside_or_after_the_year(case):
    _live(case)
    assert L.within_holding_period(case.acquired_on, case.sold_on) is case.expect_taxable


@pytest.mark.parametrize("case", PERIOD.kept, ids=lambda c: c.id)
def test_a_moved_lot_keeps_its_day_a_swap_gets_its_own(case):
    """Through the rule: a lot acquired at `acquired_at`, then moved to another account, or a
    swap's received leg at `moved_or_swapped_at`."""
    _live(case)
    wallet, exchange, asset = uuid4(), uuid4(), uuid4()
    if case.kind == "moved":
        buy = movement(
            K.ACQUISITION, "1", case.acquired_at, account=exchange, asset=asset, eur_amount=D(-10)
        )
        out = movement(
            K.INTERNAL_TRANSFER_OUT, "-1", case.moved_or_swapped_at, account=exchange, asset=asset
        )
        into = movement(
            K.INTERNAL_TRANSFER_IN, "1", case.moved_or_swapped_at, account=wallet, asset=asset
        )
        got = L.lots(
            [buy, out, into], [link(out, into, TransferLinkKind.CHAIN)], [], date(2028, 1, 1)
        )
        assert [(lot.account_id, lot.moved_through != []) for lot in got] == [(wallet, True)]
    else:
        given = movement(K.DISPOSAL, "-1", case.moved_or_swapped_at, account=wallet, asset=uuid4())
        received = movement(
            K.ACQUISITION,
            "150",
            case.moved_or_swapped_at,
            account=wallet,
            asset=asset,
            eur_amount=D("-138.00"),
            group=uuid4(),
        )
        got = L.lots([given, received], [], [], date(2028, 1, 1))
        assert [lot.source for lot in got] == [LotSource.ACQUISITION]
    assert got[0].acquired_on == case.expect_acquired_on


def test_q_o92_a_sunday_anniversary_does_not_move_the_day():
    """Q-O92 (settled 15 Nov 2026): §108 Abs. 3 AO does not reach the holding period. Bought
    Saturday 3 Jan 2026; the anniversary is Sunday 3 Jan 2027, so the lot is tax-free from
    Monday 4 Jan 2027 only because Monday is the day after, not Tuesday 5 Jan, which a shift of
    the year's end to the next working day would give."""
    bought = date(2026, 1, 3)
    anniversary = date(2027, 1, 3)
    assert anniversary.weekday() == 6  # Sunday
    assert L.tax_free_from(bought) == date(2027, 1, 4)
    assert L.tax_free_from(bought).weekday() == 0  # Monday
    assert L.within_holding_period(bought, anniversary)
    assert not L.within_holding_period(bought, date(2027, 1, 4))
    # And D188 (3)'s own example: the year ends Saturday 1 Jan 2028, a holiday too; tax-free
    # Sunday 2 Jan 2028, never Tuesday 4 Jan.
    assert date(2028, 1, 1).weekday() == 5
    assert L.tax_free_from(date(2027, 1, 1)) == date(2028, 1, 2)


def test_tax_free_from_is_a_calendar_year_for_every_day_of_a_leap_cycle():
    day = date(2027, 1, 1)
    while day < date(2029, 1, 1):
        free = L.tax_free_from(day)
        if (day.month, day.day) == (2, 29):
            assert free == date(day.year + 1, 3, 1)
        else:
            assert free == day.replace(year=day.year + 1) + timedelta(days=1)
        day += timedelta(days=1)


def test_a_naive_time_is_refused():
    with pytest.raises(ValueError):
        L.berlin_day(datetime(2026, 12, 31, 23, 30))


# --- Costs: the price paths, a swap group, never 0 € ------------------------------------

AT = datetime(2026, 3, 12, 9, tzinfo=UTC)
DAY = date(2026, 3, 12)


def test_an_acquisition_without_eur_leg_takes_the_days_price():
    acct, asset = uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "2", AT, account=acct, asset=asset, fee_eur=D("1.00"))
    got = L.lots([buy], [], [price(asset, DAY, "100.00")], DAY)
    assert got[0].cost_eur == D("201.00")
    assert got[0].cost_per_unit == D("100.50000000")


def test_a_price_of_another_day_leaves_the_cost_unpriced():
    acct, asset = uuid4(), uuid4()
    reward = movement(K.STAKING_REWARD, "1", AT, account=acct, asset=asset)
    stale = price(asset, DAY - timedelta(days=1), "100.00")
    got = L.lots([reward], [], [stale], DAY)
    assert got[0].cost_eur is None and got[0].cost_per_unit is None
    assert got[0].source is LotSource.REWARD


def test_an_airdrop_and_a_cashback_are_reward_lots_spam_is_none():
    acct, asset, spam = uuid4(), uuid4(), uuid4()
    rows = [
        movement(K.AIRDROP, "5", AT, account=acct, asset=asset),
        movement(K.CASHBACK_REWARD, "1", AT, account=acct, asset=asset, eur_amount=D("2.50")),
        movement(K.SPAM, "1000", AT, account=acct, asset=spam),
    ]
    got = L.lots(rows, [], [price(asset, DAY, "2.00")], DAY)
    assert {(lot.source, lot.units, lot.cost_eur) for lot in got} == {
        (LotSource.REWARD, D(5), D("10.00")),
        (LotSource.REWARD, D(1), D("2.50")),
    }


def test_a_swap_received_leg_unpriced_takes_the_given_legs_value_and_the_group_fee():
    """D188 (4): the received asset's price first, else the given asset's; D188 (1): the
    group's fee movement valued on the day joins the one lot it creates."""
    acct, sol, usdc, group = uuid4(), uuid4(), uuid4(), uuid4()
    given = movement(K.DISPOSAL, "-1", AT, account=acct, asset=sol, group=group)
    fee = movement(K.FEE, "-0.005", AT, account=acct, asset=sol, group=group)
    received = movement(K.ACQUISITION, "150", AT, account=acct, asset=usdc, group=group)
    buy_sol = movement(
        K.ACQUISITION, "2", AT - timedelta(days=30), account=acct, asset=sol, eur_amount=D("-200")
    )
    rows = [buy_sol, given, fee, received]
    got = L.book(rows, [], [price(sol, DAY, "140.00")], DAY)
    usdc_lot = next(lot for lot in got.lots if lot.asset_id == usdc)
    assert usdc_lot.cost_eur == D("140.70")  # 1 SOL × 140 + 0.005 SOL × 140
    # The given SOL and the fee SOL were taken from the SOL lot at its cost.
    assert [u.cost_eur for u in got.uses[given.id]] == [D("100.00")]
    assert [u.cost_eur for u in got.uses[fee.id]] == [D("0.50")]
    # With the received asset priced, its own price wins.
    got = L.lots(rows, [], [price(sol, DAY, "140.00"), price(usdc, DAY, "0.92")], DAY)
    assert next(lot for lot in got if lot.asset_id == usdc).cost_eur == D("138.70")
    # Neither priced: no cost, never 0 €.
    got = L.lots(rows, [], [], DAY)
    assert next(lot for lot in got if lot.asset_id == usdc).cost_eur is None


def test_an_unpriced_group_fee_withholds_the_cost():
    acct, sol, usdc, group = uuid4(), uuid4(), uuid4(), uuid4()
    rows = [
        movement(K.FEE, "-0.005", AT, account=acct, asset=sol, group=group),
        movement(
            K.ACQUISITION, "150", AT, account=acct, asset=usdc, group=group, eur_amount=D("138")
        ),
    ]
    got = L.book(rows, [], [], DAY)
    assert [lot.cost_eur for lot in got.lots if lot.asset_id == usdc] == [None]
    assert got.unfilled == {rows[0].id: D("0.005")}


def test_cost_total_withholds_a_total_with_an_unpriced_lot():
    acct, asset = uuid4(), uuid4()
    rows = [
        movement(K.ACQUISITION, "1", AT, account=acct, asset=asset, eur_amount=D("-100.00")),
        movement(K.AIRDROP, "3", AT, account=acct, asset=asset),
        movement(K.ACQUISITION, "1", AT, account=acct, asset=asset, eur_amount=D("-50.25")),
    ]
    total = L.cost_total(L.lots(rows, [], [], DAY))
    assert total == L.CostTotal(eur=None, priced_eur=D("150.25"), unpriced=1)
    priced = L.cost_total(L.lots([rows[0], rows[2]], [], [], DAY))
    assert priced == L.CostTotal(eur=D("150.25"), priced_eur=D("150.25"), unpriced=0)
    assert L.cost_total([]) == L.CostTotal(eur=D("0.00"), priced_eur=D("0.00"), unpriced=0)


# --- consume -----------------------------------------------------------------------------


def test_consume_takes_fifo_and_never_more_than_open():
    acct, asset = uuid4(), uuid4()
    rows = [
        movement(K.ACQUISITION, "10", AT, account=acct, asset=asset, eur_amount=D("-1000")),
        movement(
            K.ACQUISITION,
            "5",
            AT + timedelta(days=1),
            account=acct,
            asset=asset,
            eur_amount=D("-600"),
        ),
        movement(K.AIRDROP, "1", AT + timedelta(days=2), account=acct, asset=asset),
    ]
    open_lots = list(reversed(L.lots(rows, [], [], DAY + timedelta(days=2))))
    uses = L.consume(open_lots, D(12))
    assert [(u.lot_id, u.units, u.cost_eur) for u in uses] == [
        (rows[0].id, D(10), D("1000.00")),
        (rows[1].id, D(2), D("240.00")),
    ]
    short = L.consume(open_lots, D(20))
    assert sum(u.units for u in short) == D(16)
    assert short[-1].cost_eur is None  # the airdrop, unpriced
    assert L.consume(open_lots, D(0)) == []
    with pytest.raises(ValueError):
        L.consume(open_lots, D(-1))


def test_same_day_lots_go_by_their_instant():
    acct, asset = uuid4(), uuid4()
    late = movement(
        K.ACQUISITION, "1", AT + timedelta(hours=5), account=acct, asset=asset, eur_amount=D("-2")
    )
    early = movement(K.ACQUISITION, "1", AT, account=acct, asset=asset, eur_amount=D("-1"))
    open_lots = L.lots([late, early], [], [], DAY)
    assert [u.lot_id for u in L.consume(open_lots, D(1))] == [early.id]


# --- Links and the readings of the report -------------------------------------------------


def test_a_withdrawal_fee_inside_the_quantity_is_a_use_that_moves_nowhere():
    """D184 (1)(a): Binance's withdrawal of 0.3 with 0.0005 fee credits 0.2995 on the wallet."""
    exchange, wallet, eth = uuid4(), uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "1", AT, account=exchange, asset=eth, eur_amount=D("-2000"))
    later = AT + timedelta(days=1)
    out = movement(K.INTERNAL_TRANSFER_OUT, "-0.3", later, account=exchange, asset=eth)
    into = movement(K.INTERNAL_TRANSFER_IN, "0.2995", later, account=wallet, asset=eth)
    got = L.book([buy, out, into], [link(out, into, TransferLinkKind.CHAIN)], [], later.date())
    assert [(u.units, u.cost_eur) for u in got.uses[out.id]] == [
        (D("0.2995"), D("599.00")),
        (D("0.0005"), D("1.00")),
    ]
    moved = [lot for lot in got.lots if lot.account_id == wallet]
    assert [(lot.units_open, lot.cost_eur, lot.source) for lot in moved] == [
        (D("0.2995"), D("599.00"), LotSource.ACQUISITION)
    ]


def test_an_in_leg_above_its_out_leg_adds_an_unknown_lot():
    exchange, wallet, eth = uuid4(), uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "1", AT, account=exchange, asset=eth, eur_amount=D("-2000"))
    out = movement(K.INTERNAL_TRANSFER_OUT, "-1", AT, account=exchange, asset=eth)
    into = movement(K.INTERNAL_TRANSFER_IN, "1.000001", AT, account=wallet, asset=eth)
    got = L.lots([buy, out, into], [link(out, into, TransferLinkKind.CHAIN)], [], DAY)
    assert {(lot.units_open, lot.source, lot.cost_eur) for lot in got} == {
        (D("1"), LotSource.ACQUISITION, D("2000.00")),
        (D("0.000001"), LotSource.UNKNOWN, None),
    }


def test_a_credit_seen_before_its_debit_moves_the_lot_at_the_credit():
    exchange, wallet, eth = uuid4(), uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "1", AT, account=exchange, asset=eth, eur_amount=D("-2000"))
    into = movement(K.INTERNAL_TRANSFER_IN, "1", AT + timedelta(days=1), account=wallet, asset=eth)
    sell = movement(K.DISPOSAL, "-1", AT + timedelta(days=2), account=wallet, asset=eth)
    out = movement(
        K.INTERNAL_TRANSFER_OUT, "-1", AT + timedelta(days=3), account=exchange, asset=eth
    )
    got = L.book(
        [buy, into, sell, out], [link(out, into, TransferLinkKind.CHAIN)], [], DAY + timedelta(3)
    )
    assert got.unfilled == {}
    assert [(u.units, u.cost_eur) for u in got.uses[sell.id]] == [(D(1), D("2000.00"))]


def test_address_to_address_inside_one_account_moves_nothing():
    wallet, sol = uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "2", AT, account=wallet, asset=sol, eur_amount=D("-280"))
    out = movement(K.INTERNAL_TRANSFER_OUT, "-1", AT, account=wallet, asset=sol)
    into = movement(K.INTERNAL_TRANSFER_IN, "1", AT, account=wallet, asset=sol)
    got = L.book([buy, out, into], [link(out, into, TransferLinkKind.CHAIN)], [], DAY)
    assert [(lot.units_open, lot.moved_through) for lot in got.lots] == [(D(2), [])]
    assert got.uses == {}


def test_unlinked_or_superseded_links_take_and_add_without_moving():
    exchange, wallet, eth = uuid4(), uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "1", AT, account=exchange, asset=eth, eur_amount=D("-2000"))
    out = movement(K.INTERNAL_TRANSFER_OUT, "-1", AT, account=exchange, asset=eth)
    into = movement(K.INTERNAL_TRANSFER_IN, "1", AT, account=wallet, asset=eth)
    ended = link(out, into, TransferLinkKind.CHAIN, superseded=True)
    got = L.book([buy, out, into], [ended], [], DAY)
    assert [(u.units, u.cost_eur) for u in got.uses[out.id]] == [(D(1), D("2000.00"))]
    open_lots = [lot for lot in got.lots if lot.units_open]
    assert [(lot.account_id, lot.source, lot.cost_eur) for lot in open_lots] == [
        (wallet, LotSource.UNKNOWN, None)
    ]
    # A bank link (a transaction leg) moves no lot either.
    bank = TransferLink(
        id=uuid4(),
        workspace_id=WORKSPACE,
        created_at=AT,
        kind=TransferLinkKind.EXCHANGE_EUR,
        out_transaction_id=uuid4(),
        in_movement_id=into.id,
        matched_by=TransferMatchedBy.USER,
    )
    assert L.lots([buy, out, into], [bank], [], DAY) == open_lots


def test_proposed_and_superseded_movements_are_not_read():
    acct, asset = uuid4(), uuid4()
    rows = [
        movement(K.ACQUISITION, "1", AT, account=acct, asset=asset, eur_amount=D("-1")),
        movement(
            K.ACQUISITION,
            "1",
            AT,
            account=acct,
            asset=asset,
            eur_amount=D("-1"),
            status=MovementStatus.PROPOSED,
        ),
        movement(
            K.ACQUISITION, "1", AT, account=acct, asset=asset, eur_amount=D("-1"), superseded=True
        ),
    ]
    assert [lot.id for lot in L.lots(rows, [], [], DAY)] == [rows[0].id]


def test_a_balance_correction_adds_an_unknown_lot_or_takes_fifo():
    acct, btc = uuid4(), uuid4()
    rows = [
        movement(K.ACQUISITION, "1", AT, account=acct, asset=btc, eur_amount=D("-50000")),
        movement(K.BALANCE_CORRECTION, "0.01", AT + timedelta(hours=1), account=acct, asset=btc),
        movement(K.BALANCE_CORRECTION, "-0.5", AT + timedelta(hours=2), account=acct, asset=btc),
    ]
    got = L.book(rows, [], [], DAY)
    assert [(u.units, u.cost_eur) for u in got.uses[rows[2].id]] == [(D("0.5"), D("25000.00"))]
    assert [(lot.source, lot.units_open, lot.cost_eur) for lot in got.lots] == [
        (LotSource.ACQUISITION, D("0.5"), D("25000.00")),
        (LotSource.UNKNOWN, D("0.01"), None),
    ]


def test_a_disposal_beyond_the_lots_is_unfilled_never_priced():
    acct, asset = uuid4(), uuid4()
    rows = [
        movement(K.ACQUISITION, "1", AT, account=acct, asset=asset, eur_amount=D("-10")),
        movement(K.DISPOSAL, "-3", AT + timedelta(hours=1), account=acct, asset=asset),
    ]
    got = L.book(rows, [], [], DAY)
    assert [u.units for u in got.uses[rows[1].id]] == [D(1)]
    assert got.unfilled == {rows[1].id: D(2)}


def test_lp_spam_and_distribution_neither_add_nor_take():
    acct, asset = uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "10", AT, account=acct, asset=asset, eur_amount=D("-10"))
    rows = [
        buy,
        movement(K.LP_DEPOSIT, "-4", AT, account=acct, asset=asset),
        movement(K.LP_WITHDRAWAL, "5", AT, account=acct, asset=asset),
        movement(K.DISTRIBUTION, "1", AT, account=acct, asset=asset),
        movement(K.SPAM, "7", AT, account=acct, asset=asset),
    ]
    got = L.book(rows, [], [], DAY)
    assert [(lot.id, lot.units_open) for lot in got.lots] == [(buy.id, D(10))]
    assert got.uses == {} and got.unfilled == {}


def test_the_year_end_on_the_berlin_calendar():
    """holdings_statement.yaml's boundary through `on`: 22:30 UTC on 31 Dec is 23:30 in Berlin
    and read on 31 Dec; 23:30 UTC is 1 Jan in Berlin and read only from 1 Jan."""
    acct, eth = uuid4(), uuid4()
    evening = movement(
        K.ACQUISITION,
        "0.1",
        datetime(2026, 12, 31, 22, 30, tzinfo=UTC),
        account=acct,
        asset=eth,
        eur_amount=D("-300"),
    )
    late = movement(
        K.ACQUISITION,
        "1",
        datetime(2026, 12, 31, 23, 30, tzinfo=UTC),
        account=acct,
        asset=eth,
        eur_amount=D("-3000"),
    )
    year_end = L.lots([evening, late], [], [], date(2026, 12, 31))
    assert [lot.id for lot in year_end] == [evening.id]
    new_year = L.lots([evening, late], [], [], date(2027, 1, 1))
    assert [(lot.id, lot.acquired_on) for lot in new_year] == [
        (evening.id, date(2026, 12, 31)),
        (late.id, date(2027, 1, 1)),
    ]


# --- PR #368 review: order at one instant, pairs across `on`, groups, consume, precision --


def test_a_transfer_and_its_fee_at_one_instant_never_depend_on_the_ids():
    """The linked move runs before the fee at one instant: B gets the whole oldest lot and the
    fee takes from the next, for any ids."""
    for _ in range(40):
        a, b, eth = uuid4(), uuid4(), uuid4()
        old = movement(K.ACQUISITION, "1", AT, account=a, asset=eth, eur_amount=D("-1000"))
        new = movement(
            K.ACQUISITION, "1", AT + timedelta(hours=1), account=a, asset=eth, eur_amount=D("-3000")
        )
        at = AT + timedelta(hours=2)
        out = movement(K.INTERNAL_TRANSFER_OUT, "-1", at, account=a, asset=eth)
        fee = movement(K.FEE, "-0.01", at, account=a, asset=eth)
        into = movement(K.INTERNAL_TRANSFER_IN, "1", at, account=b, asset=eth)
        got = L.book([old, new, out, fee, into], [link(out, into, TransferLinkKind.CHAIN)], [], DAY)
        on_b = [
            (lot.movement_id, lot.units_open, lot.cost_eur)
            for lot in got.lots
            if lot.account_id == b
        ]
        assert on_b == [(old.id, D(1), D("1000.00"))]
        assert [(u.lot_id, u.units, u.cost_eur) for u in got.uses[fee.id]] == [
            (new.id, D("0.01"), D("30.00"))
        ]


def test_takes_at_one_instant_go_disposal_before_fee_then_by_index():
    """At one instant the disposals go before the fee, and between the disposals the lower
    index first, an all-digit index by its number (9 before 10, not as text)."""
    acct, sol = uuid4(), uuid4()
    first = movement(K.ACQUISITION, "1", AT, account=acct, asset=sol, eur_amount=D("-100"))
    second = movement(
        K.ACQUISITION, "1", AT + timedelta(hours=1), account=acct, asset=sol, eur_amount=D("-200")
    )
    at = AT + timedelta(hours=2)
    for _ in range(20):
        fee = movement(K.FEE, "-0.5", at, account=acct, asset=sol, index="0")
        sale_10 = movement(K.DISPOSAL, "-0.6", at, account=acct, asset=sol, index="10")
        sale_9 = movement(K.DISPOSAL, "-0.6", at, account=acct, asset=sol, index="9")
        got = L.book([first, second, fee, sale_10, sale_9], [], [], DAY)
        assert [(u.lot_id, u.units) for u in got.uses[sale_9.id]] == [(first.id, D("0.6"))]
        assert [(u.lot_id, u.units) for u in got.uses[sale_10.id]] == [
            (first.id, D("0.4")),
            (second.id, D("0.2")),
        ]
        assert [(u.lot_id, u.units) for u in got.uses[fee.id]] == [(second.id, D("0.5"))]


def test_a_pair_and_a_sale_on_the_receiver_at_one_instant_leave_nothing_unfilled():
    for _ in range(20):
        a, b, eth = uuid4(), uuid4(), uuid4()
        buy = movement(K.ACQUISITION, "1", AT, account=a, asset=eth, eur_amount=D("-2000"))
        at = AT + timedelta(hours=1)
        out = movement(K.INTERNAL_TRANSFER_OUT, "-1", at, account=a, asset=eth)
        into = movement(K.INTERNAL_TRANSFER_IN, "1", at, account=b, asset=eth)
        sell = movement(K.DISPOSAL, "-1", at, account=b, asset=eth)
        got = L.book([buy, out, into, sell], [link(out, into, TransferLinkKind.CHAIN)], [], DAY)
        assert got.unfilled == {}
        assert [lot for lot in got.lots if lot.units_open] == []
        assert [(u.units, u.cost_eur) for u in got.uses[sell.id]] == [(D(1), D("2000.00"))]


def test_a_wrap_and_a_sale_of_the_wrapped_coin_at_one_instant_leave_nothing_unfilled():
    for _ in range(20):
        wallet, sol, wsol = uuid4(), uuid4(), uuid4()
        buy = movement(K.ACQUISITION, "2", AT, account=wallet, asset=sol, eur_amount=D("-280"))
        at = AT + timedelta(hours=1)
        out = movement(K.INTERNAL_TRANSFER_OUT, "-2", at, account=wallet, asset=sol)
        into = movement(K.INTERNAL_TRANSFER_IN, "2", at, account=wallet, asset=wsol)
        sell = movement(K.DISPOSAL, "-2", at, account=wallet, asset=wsol)
        got = L.book([sell, into, out, buy], [link(out, into, TransferLinkKind.WRAP)], [], DAY)
        assert got.unfilled == {}
        assert [lot for lot in got.lots if lot.units_open] == []


def test_a_chain_of_moves_at_one_instant_runs_in_its_order():
    """A→B and B→C at one instant: A→B first whatever the ids, so C gets A's lot."""
    for _ in range(20):
        a, b, c, eth = uuid4(), uuid4(), uuid4(), uuid4()
        buy = movement(K.ACQUISITION, "1", AT, account=a, asset=eth, eur_amount=D("-2000"))
        at = AT + timedelta(hours=1)
        ab_out = movement(K.INTERNAL_TRANSFER_OUT, "-1", at, account=a, asset=eth)
        ab_in = movement(K.INTERNAL_TRANSFER_IN, "1", at, account=b, asset=eth)
        bc_out = movement(K.INTERNAL_TRANSFER_OUT, "-1", at, account=b, asset=eth)
        bc_in = movement(K.INTERNAL_TRANSFER_IN, "1", at, account=c, asset=eth)
        links = [
            link(bc_out, bc_in, TransferLinkKind.CHAIN),
            link(ab_out, ab_in, TransferLinkKind.CHAIN),
        ]
        got = L.book([buy, bc_out, bc_in, ab_out, ab_in], links, [], DAY)
        assert got.unfilled == {}
        assert [
            (lot.account_id, lot.movement_id, lot.source) for lot in got.lots if lot.units_open
        ] == [(c, buy.id, LotSource.ACQUISITION)]


@pytest.mark.parametrize("credit_first", [False, True])
def test_a_pair_across_the_year_end_is_read_whole(credit_first):
    """The pair rule: a pair whose earlier leg is on or before `on` is read whole, so on 31
    Dec the lot is on the receiver once, with its own day, never an unknown lot."""
    a, b, eth = uuid4(), uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "1", AT, account=a, asset=eth, eur_amount=D("-2000"))
    dec = datetime(2026, 12, 31, 20, tzinfo=UTC)
    jan = datetime(2027, 1, 1, 9, tzinfo=UTC)
    out_at, in_at = (jan, dec) if credit_first else (dec, jan)
    out = movement(K.INTERNAL_TRANSFER_OUT, "-1", out_at, account=a, asset=eth)
    into = movement(K.INTERNAL_TRANSFER_IN, "1", in_at, account=b, asset=eth)
    rows, links = [buy, out, into], [link(out, into, TransferLinkKind.CHAIN)]
    for on in (date(2026, 12, 31), date(2027, 1, 1)):
        held = L.lots(rows, links, [], on)
        assert [(lot.account_id, lot.units_open, lot.acquired_on, lot.source) for lot in held] == [
            (b, D(1), DAY, LotSource.ACQUISITION)
        ]
    # Before the earlier leg's day nothing moved.
    assert [lot.account_id for lot in L.lots(rows, links, [], date(2026, 12, 30))] == [a]


def test_a_group_creating_two_lots_gives_neither_the_given_value():
    """The given-leg fallback is for a group that creates exactly one lot: two received legs
    cannot each take the whole given value, so both stay unpriced."""
    acct, sol, usdc, usdt, group = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    rows = [
        movement(
            K.ACQUISITION, "2", AT - timedelta(days=9), account=acct, asset=sol, eur_amount=D("-1")
        ),
        movement(K.DISPOSAL, "-1", AT, account=acct, asset=sol, group=group),
        movement(K.ACQUISITION, "70", AT, account=acct, asset=usdc, group=group),
        movement(K.ACQUISITION, "70", AT, account=acct, asset=usdt, group=group),
    ]
    got = L.lots(rows, [], [price(sol, DAY, "140.00")], DAY)
    assert {lot.asset_id: lot.cost_eur for lot in got if lot.asset_id != sol} == {
        usdc: None,
        usdt: None,
    }


def test_consume_refuses_lots_of_two_accounts_or_assets():
    a, b, eth = uuid4(), uuid4(), uuid4()
    rows = [
        movement(K.ACQUISITION, "1", AT, account=a, asset=eth, eur_amount=D("-1")),
        movement(K.ACQUISITION, "1", AT, account=b, asset=eth, eur_amount=D("-1")),
    ]
    held = L.lots(rows, [], [], DAY)
    with pytest.raises(ValueError):
        L.consume(held, D(1))
    assert len(L.consume([held[0]], D(1))) == 1


def test_competing_links_for_one_leg_choose_the_earliest_created_in_any_order():
    a, b, c, eth = uuid4(), uuid4(), uuid4(), uuid4()
    buy = movement(K.ACQUISITION, "1", AT, account=a, asset=eth, eur_amount=D("-2000"))
    at = AT + timedelta(hours=1)
    out = movement(K.INTERNAL_TRANSFER_OUT, "-1", at, account=a, asset=eth)
    to_b = movement(K.INTERNAL_TRANSFER_IN, "1", at, account=b, asset=eth)
    to_c = movement(K.INTERNAL_TRANSFER_IN, "1", at, account=c, asset=eth)
    first = link(out, to_b, TransferLinkKind.CHAIN, created_at=datetime(2027, 1, 1, tzinfo=UTC))
    second = link(out, to_c, TransferLinkKind.CHAIN, created_at=datetime(2027, 1, 2, tzinfo=UTC))
    rows = [buy, out, to_b, to_c]
    for links in ([first, second], [second, first]):
        held = {lot.account_id: lot.source for lot in L.lots(rows, links, [], DAY)}
        assert held == {b: LotSource.ACQUISITION, c: LotSource.UNKNOWN}


def test_units_of_18_decimals_on_a_large_balance_subtract_exactly():
    """Fix 6 at the Decimal level: the Quantity type still refuses more than 10 decimals
    (migration 0042 widens it on another branch), so the arithmetic is tested on the book's
    own parts. With Python's default 28 digits the open units would round."""
    acct, asset = uuid4(), uuid4()
    holder = movement(K.DISPOSAL, "-1", AT, account=acct, asset=asset)
    big = D("12345678901.123456789012345678")
    tiny = D("0.000000000000000001")
    part = L._Part(
        id=uuid4(),
        movement_id=uuid4(),
        account_id=acct,
        asset_id=asset,
        acquired_at=AT,
        acquired_on=DAY,
        units=big,
        open=big,
        per=D("0.5"),
        source=LotSource.ACQUISITION,
    )
    ledger = L._Book()
    ledger.add(part)
    taken = ledger.take(holder, tiny, record=False)
    assert taken == [(part, tiny)]
    assert part.open == D("12345678901.123456789012345677")
    ledger.take(holder, big, record=False)
    assert part.open == 0
    assert ledger.unfilled == {holder.id: tiny}
