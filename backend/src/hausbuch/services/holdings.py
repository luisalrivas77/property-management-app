"""Lots, the Krypto tab and the year-end holdings statement, computed on read
(docs/phases/phase-7.md D188, D190, §8, §13; step 6).

What is read, under the workspace's row-level security:
- the wallet and exchange accounts (a depot is not crypto: its funds are phase 6's);
- their confirmed, current movements of the crypto assets (an exchange's EUR is cash, not a
  lot: a trade's EUR fee is already the acquisition's `fee_eur`, phase 6 D178 amended (3));
  a spam token (D187) is never a lot and is listed in the statement's appendix;
- every `transfer_link` between two movements, as stored (phase 6's kinds and phase 7's
  `chain`, `bridge`, `wrap`); `rules.lots` keeps the live ones over confirmed legs. This
  module never writes a link (the chain matcher does);
- the price rows of the Berlin days the movements fall on, each chain token's `priced_as`
  asset's rows with its own (D180 (3)), for the lots' costs (`rules.lots` takes only a price
  of the movement's own day, D188 (4)); and the price `price_on` picks on the read day for
  the value (phase 6 D167, `services.prices.overview`).

`rules.lots.book` runs once over every account, so a lot moved between two own accounts keeps
its day and cost (D184 (4)); the figures are `rules.holdings`'. Nothing is cached: a re-booked
movement changes the next read (D184 (5)).

- `crypto_holdings(conn, on)`: GET /holdings/crypto, the Krypto tab.
- `account_lots(conn, account_id, on, asset_id)`: GET /wallets/{id}/lots.
- `statement(conn, on)`: GET /wallets/holdings?on=, D190's statement per account, address
  and asset: the units at the end of the Berlin day, the open lots, the value and whether the
  last sync on or before the day reconciled. Fixture tests/fixtures/phase7/holdings_statement
  .yaml. Readings (step 6): a position is valued by the price `price_on` picks on the day (a
  price of an earlier day included, as the Anlagen overview does); the appendix lists the
  spam tokens only, an unpriced coin stays a position without a value; an address is marked
  when its last finished sync on or before the day did not reconcile (`reconciled` not true);
  an exchange has no sync and is never marked.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, and_, or_, select

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import price as price_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.db.tables import wallet_address as address_table
from hausbuch.db.tables import wallet_sync as sync_table
from hausbuch.rules import holdings as figures
from hausbuch.rules import lots as lot_rules
from hausbuch.rules.lots import berlin_day
from hausbuch.schemas.bank import Account
from hausbuch.schemas.chain import (
    AccountHoldings,
    AddressHoldings,
    Chain,
    HoldingsStatement,
    Lot,
    PositionHoldings,
    UnvaluedToken,
)
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.holdings import CryptoAccount, CryptoCoin, CryptoHoldings, CryptoLot
from hausbuch.schemas.household import (
    AssetKind,
    AssetMovement,
    MovementStatus,
    PricePoint,
    TransferLink,
)
from hausbuch.schemas.prices import PriceSourceKind

from .accounts import list_accounts
from .prices.overview import _latest_prices
from .wallet_groups import waiting

CRYPTO_ACCOUNT_KINDS = (AccountKind.WALLET, AccountKind.EXCHANGE)


@dataclass
class _Asset:
    id: UUID
    symbol: str | None
    name: str
    spam: bool
    priced_as: UUID | None


@dataclass
class _Ledger:
    accounts: list[Account]
    assets: dict[UUID, _Asset]
    movements: list[AssetMovement]  # confirmed, current, crypto (spam included)
    links: list[TransferLink]
    prices: list[PricePoint]  # the movement days' rows, twins included


def _accounts(conn: Connection) -> list[Account]:
    return [a for a in list_accounts(conn) if a.kind in CRYPTO_ACCOUNT_KINDS]


def _assets(conn: Connection) -> dict[UUID, _Asset]:
    a = asset_table.c
    rows = conn.execute(
        select(a.id, a.symbol, a.name, a.spam, a.priced_as_asset_id).where(
            a.kind == AssetKind.CRYPTO.value
        )
    ).all()
    return {r.id: _Asset(r.id, r.symbol, r.name, r.spam, r.priced_as_asset_id) for r in rows}


def _movements(
    conn: Connection, account_ids: Sequence[UUID], assets: Iterable[UUID]
) -> list[AssetMovement]:
    m = movement_table.c
    asset_ids = list(assets)
    if not account_ids or not asset_ids:
        return []
    rows = conn.execute(
        select(movement_table).where(
            m.account_id.in_(account_ids),
            m.asset_id.in_(asset_ids),
            m.status == MovementStatus.CONFIRMED.value,
            m.superseded_by.is_(None),
        )
    ).all()
    return [AssetMovement.model_validate(dict(r._mapping)) for r in rows]


def _links(conn: Connection, movement_ids: set[UUID]) -> list[TransferLink]:
    """Every link with a movement on both sides that touches one of the movements; superseded
    ones too (rules.lots reads only the live ones)."""
    if not movement_ids:
        return []
    k = link_table.c
    rows = conn.execute(
        select(link_table).where(
            k.out_movement_id.is_not(None),
            k.in_movement_id.is_not(None),
            or_(k.out_movement_id.in_(movement_ids), k.in_movement_id.in_(movement_ids)),
        )
    ).all()
    return [TransferLink.model_validate(dict(r._mapping)) for r in rows]


def _day_prices(
    conn: Connection, assets: dict[UUID, _Asset], movements: Sequence[AssetMovement]
) -> list[PricePoint]:
    """The price rows of every (asset, Berlin day) a movement could need: each movement's
    asset and its `priced_as` asset on the movement's day. A swap's given coin and a fee's
    coin are movements of the same group, so their days are in the set."""
    days: dict[UUID, set[date]] = defaultdict(set)
    for mv in movements:
        day = berlin_day(mv.occurred_at)
        days[mv.asset_id].add(day)
        twin = assets[mv.asset_id].priced_as if mv.asset_id in assets else None
        if twin is not None:
            days[twin].add(day)
    if not days:
        return []
    p = price_table.c
    rows = conn.execute(
        select(p.asset_id, p.on, p.price_eur, p.source, p.fetched_at).where(
            or_(*(and_(p.asset_id == asset_id, p.on.in_(on)) for asset_id, on in days.items()))
        )
    ).all()
    return [
        PricePoint(
            asset_id=r.asset_id,
            on=r.on,
            price_eur=r.price_eur,
            source=PriceSourceKind(r.source),
            fetched_at=r.fetched_at,
        )
        for r in rows
    ]


def _ledger(conn: Connection) -> _Ledger:
    accounts = _accounts(conn)
    assets = _assets(conn)
    movements = _movements(conn, [a.id for a in accounts], assets)
    links = _links(conn, {mv.id for mv in movements})
    return _Ledger(accounts, assets, movements, links, _day_prices(conn, assets, movements))


def _book(ledger: _Ledger, on: date) -> lot_rules.LotBook:
    lotted = [mv for mv in ledger.movements if not ledger.assets[mv.asset_id].spam]
    twins = {a.id: a.priced_as for a in ledger.assets.values() if a.priced_as is not None}
    return lot_rules.book(lotted, ledger.links, ledger.prices, on, priced_as=twins)


def _through(ledger: _Ledger, on: date) -> list[AssetMovement]:
    """The movements read at the end of the Berlin day `on`, as the lots read them
    (`rules.lots.read_through`): a linked pair whole at its earlier leg, so the units and the
    open lots never disagree about a transfer across the day; spam included (the appendix)."""
    return lot_rules.read_through(ledger.movements, ledger.links, on)


def _units(ledger: _Ledger, on: date) -> dict[tuple[UUID, UUID], Decimal]:
    """Σ quantity per (account, asset) of `_through`, at the lots' precision."""
    return lot_rules.units_through(ledger.movements, ledger.links, on)


def held_accounts(conn: Connection, days: Sequence[date]) -> dict[date, set[UUID]]:
    """Per day, the wallet and exchange accounts holding a non-spam asset with units > 0 at
    the end of that Berlin day, read as the statement reads them (the year-end reminder)."""
    ledger = _ledger(conn)
    return {
        on: {
            account
            for (account, asset), units in _units(ledger, on).items()
            if units > 0 and not ledger.assets[asset].spam
        }
        for on in days
    }


def _open_lots(book: lot_rules.LotBook) -> dict[tuple[UUID, UUID], list[Lot]]:
    out: dict[tuple[UUID, UUID], list[Lot]] = defaultdict(list)
    for lot in book.lots:
        if lot.units_open > 0:
            out[(lot.account_id, lot.asset_id)].append(lot)
    return out


@dataclass
class _Address:
    id: UUID
    account_id: UUID
    chain: str
    masked: str
    reconciled: bool  # its last finished sync on or before the day reconciled (or none ran)
    synced: bool  # a sync finished on or before the day


def _addresses(conn: Connection, account_ids: Sequence[UUID], on: date) -> dict[UUID, _Address]:
    if not account_ids:
        return {}
    w = address_table.c
    rows = conn.execute(
        select(w.id, w.account_id, w.chain, w.address_masked).where(w.account_id.in_(account_ids))
    ).all()
    s = sync_table.c
    syncs = conn.execute(
        select(s.address_id, s.finished_at, s.started_at, s.created_at, s.reconciled).where(
            s.account_id.in_(account_ids), s.finished_at.is_not(None)
        )
    ).all()
    last: dict[UUID, Any] = {}
    for r in syncs:
        if berlin_day(r.finished_at) > on:
            continue
        held = last.get(r.address_id)
        if held is None or (r.started_at, r.created_at) > (held.started_at, held.created_at):
            last[r.address_id] = r
    return {
        r.id: _Address(
            r.id,
            r.account_id,
            r.chain,
            r.address_masked,
            reconciled=r.id not in last or last[r.id].reconciled is True,
            synced=r.id in last,
        )
        for r in rows
    }


def _symbols(ledger: _Ledger) -> dict[UUID, str | None]:
    return {a.id: a.symbol for a in ledger.assets.values()}


def _coins(
    conn: Connection, ledger: _Ledger, on: date, book: lot_rules.LotBook
) -> list[CryptoCoin]:
    units = _units(ledger, on)
    open_lots = _open_lots(book)
    keys = sorted(
        {
            key
            for key in set(units) | set(open_lots)
            if not ledger.assets[key[1]].spam and (units.get(key) or open_lots.get(key))
        },
        key=lambda k: (str(k[0]), ledger.assets[k[1]].symbol or "", str(k[1])),
    )
    prices = _latest_prices(conn, {asset_id for _, asset_id in keys}, on)
    addresses = _addresses(conn, [a.id for a in ledger.accounts], on)
    unreconciled = _unreconciled_positions(_through(ledger, on), addresses)
    symbols = _symbols(ledger)
    return [
        figures.coin(
            account_id=account_id,
            asset_id=asset_id,
            symbol=ledger.assets[asset_id].symbol,
            name=ledger.assets[asset_id].name,
            units=units.get((account_id, asset_id), Decimal(0)),
            lots=open_lots.get((account_id, asset_id), []),
            price=prices.get(asset_id),
            needs=book.price_needs,
            symbols=symbols,
            on=on,
            reconciled=(account_id, asset_id) not in unreconciled,
        )
        for account_id, asset_id in keys
    ]


def _unreconciled_positions(
    through: Iterable[AssetMovement], addresses: dict[UUID, _Address]
) -> set[tuple[UUID, UUID]]:
    """The positions with a movement read on the day (`_through`) at an address whose last
    sync on or before the day did not reconcile."""
    out = set()
    for mv in through:
        address = addresses.get(mv.address_id) if mv.address_id is not None else None
        if address is not None and not address.reconciled:
            out.add((mv.account_id, mv.asset_id))
    return out


def crypto_holdings(conn: Connection, on: date) -> CryptoHoldings:
    """GET /holdings/crypto: every wallet and exchange account, its coins and their open lots
    at the end of the Berlin day `on`, and the tab's four cards (D188 (5))."""
    ledger = _ledger(conn)
    book = _book(ledger, on)
    coins = _coins(conn, ledger, on, book)
    held = {c.account_id for c in coins}
    accounts = [
        CryptoAccount(
            account_id=a.id,
            label=a.label,
            kind=a.kind.value,
            provider=a.provider,
            ownership=a.ownership,
            value_eur=Decimal("0.00"),
        )
        for a in ledger.accounts
        if a.id in held or a.kind is AccountKind.WALLET
    ]
    unfilled_accounts = {mv.account_id for mv in ledger.movements if mv.id in book.unfilled}
    summary = figures.summary(
        on,
        coins,
        accounts,
        unfilled_movements=len(book.unfilled),
        unfilled_account_ids=sorted(unfilled_accounts, key=str),
    )
    # The coins a wallet's proposed movements hold, which the figures above leave out.
    return summary.model_copy(update={"waiting": waiting(conn, on)})


def account_lots(
    conn: Connection, account_id: UUID, on: date, asset_id: UUID | None = None
) -> list[CryptoLot]:
    """GET /wallets/{id}/lots?asset=: the account's open lots (every coin, or one), FIFO."""
    seen = crypto_holdings(conn, on)
    return [
        lot
        for coin in seen.coins
        if coin.account_id == account_id and (asset_id is None or coin.asset_id == asset_id)
        for lot in coin.lots
    ]


# --- The statement (D190) -------------------------------------------------------------------


def statement(conn: Connection, on: date) -> HoldingsStatement:
    """GET /wallets/holdings?on=: per wallet and exchange account with a movement read on the
    day (`_through`: a linked pair whole at its earlier leg, so an account that received a
    transfer whose own leg is after the day is listed with the lots it holds), its addresses'
    units per asset, its positions with their open lots and value, the spam tokens in the
    appendix, and the addresses whose last sync on or before the day did not reconcile.
    Positions of zero units are left out."""
    ledger = _ledger(conn)
    book = _book(ledger, on)
    coins = {(c.account_id, c.asset_id): c for c in _coins(conn, ledger, on, book)}
    addresses = _addresses(conn, [a.id for a in ledger.accounts], on)
    through = _through(ledger, on)
    out = []
    for account in ledger.accounts:
        mine = [mv for mv in through if mv.account_id == account.id]
        if not mine:
            continue
        out.append(_account_statement(account, mine, coins, addresses, ledger))
    return HoldingsStatement(on=on, accounts=out)


@lot_rules.exact
def _account_statement(
    account: Account,
    movements: list[AssetMovement],
    coins: dict[tuple[UUID, UUID], CryptoCoin],
    addresses: dict[UUID, _Address],
    ledger: _Ledger,
) -> AccountHoldings:
    by_address: dict[UUID, dict[UUID, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    totals: dict[UUID, Decimal] = defaultdict(Decimal)
    for mv in movements:
        totals[mv.asset_id] += mv.quantity
        if mv.address_id is not None and not ledger.assets[mv.asset_id].spam:
            by_address[mv.address_id][mv.asset_id] += mv.quantity
    shown = [
        addresses[aid]
        for aid in sorted(by_address, key=lambda a: (addresses[a].chain, addresses[a].masked))
        if aid in addresses
    ]
    positions = [
        PositionHoldings(
            asset_id=coin.asset_id,
            symbol=coin.symbol,
            name=coin.name,
            units=coin.units,
            lots=[
                Lot.model_validate(lot.model_dump(include=set(Lot.model_fields)))
                for lot in coin.lots
            ],
            price_eur=coin.price_eur,
            price_on=coin.price_on,
            value_eur=coin.value_eur,
            reconciled=coin.reconciled,
        )
        for (account_id, _), coin in coins.items()
        if account_id == account.id and coin.units != 0
    ]
    return AccountHoldings(
        account_id=account.id,
        label=account.label,
        kind=account.kind.value,
        provider=account.provider,
        addresses=[
            AddressHoldings(
                address_masked=a.masked,
                chain=Chain(a.chain),
                units_by_asset={
                    str(asset_id): units
                    for asset_id, units in by_address[a.id].items()
                    if units != 0
                },
            )
            for a in shown
        ],
        positions=positions,
        unvalued_tokens=[
            UnvaluedToken(asset_id=asset_id, units=units, reason="spam")
            for asset_id, units in sorted(totals.items(), key=lambda t: str(t[0]))
            if ledger.assets[asset_id].spam and units != 0
        ],
        unreconciled_addresses=[a.masked for a in shown if not a.reconciled],
        movement_ids=sorted((mv.id for mv in movements), key=str),
        unsynced_addresses=[a.masked for a in shown if not a.synced],
    )
