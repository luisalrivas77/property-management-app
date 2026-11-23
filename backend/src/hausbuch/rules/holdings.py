"""The Krypto tab's figures (docs/phases/phase-7.md D188 (5); step 6): pure functions over the
open lots of `rules.lots` and the day's price.

- `coin(...)`: one coin on one account: its value (units × price), its cost (Σ of its lots,
  None when one has no cost: never a total containing an unpriced lot, D188 (4)), the
  unrealized result (value − cost, only when both are known), the units and value free of
  the §23 holding period today (lots with `tax_free_from ≤ on`) and the next day a lot of it
  becomes free.
- `summary(...)`: the four cards: Krypto gesamt (Σ valued coins), Unrealisiert (Σ over the
  coins whose value and cost are known, with how many were left out), heute steuerfrei
  verkaufbar (Σ value of the free lots of priced coins, with how many free coins have no
  price) and nächste Tranche steuerfrei (the earliest future `tax_free_from` over all lots).

Every amount is rounded to the cent half up once, where it is written. Informational until
phase 8: no figure here is a tax figure.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from hausbuch.schemas.chain import Lot, LotSource, PriceNeed
from hausbuch.schemas.holdings import (
    CryptoAccount,
    CryptoCoin,
    CryptoHoldings,
    CryptoLot,
    LotPriceNeed,
    NextTranche,
    TranchePart,
)
from hausbuch.schemas.prices import PricePoint, PriceSourceKind

from .lots import cost_total, exact

CENT = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def _value(units: Decimal, price: PricePoint | None) -> Decimal | None:
    return None if price is None else _cents(units * price.price_eur)


@exact
def coin(
    *,
    account_id: UUID,
    asset_id: UUID,
    symbol: str | None,
    name: str,
    units: Decimal,
    lots: Sequence[Lot],
    price: PricePoint | None,
    needs: Mapping[UUID, Sequence[PriceNeed]],
    symbols: Mapping[UUID, str | None],
    on: date,
    reconciled: bool = True,
) -> CryptoCoin:
    """`lots`: the coin's open lots on this account in FIFO order; `needs`: per creating
    movement id, the prices that would price its lot (`rules.lots.LotBook.price_needs`).
    A lot whose origin no movement explains (`source` unknown) has no known acquisition day,
    so it is never counted free of the holding period nor as a coming tranche: its
    `tax_free_from` is the day it arrived, which the screen and the statement do not show
    (they say „Herkunft offen“). Units are summed with `rules.lots`' precision."""
    shown: list[CryptoLot] = []
    for lot in lots:
        known = lot.source is not LotSource.UNKNOWN
        lot_needs = (
            []
            if lot.cost_eur is not None or lot.source is LotSource.UNKNOWN
            else [
                LotPriceNeed(asset_id=n.asset_id, symbol=symbols.get(n.asset_id), on=n.on)
                for n in needs.get(lot.movement_id, [])
            ]
        )
        shown.append(
            CryptoLot(
                **lot.model_dump(),
                tax_free=known and lot.tax_free_from <= on,
                value_eur=_value(lot.units_open, price),
                price_needs=lot_needs,
            )
        )
    cost = cost_total(lots)
    value = _value(units, price)
    free = [lot for lot in shown if lot.tax_free]
    free_units = sum((lot.units_open for lot in free), Decimal(0))
    waiting = [lot for lot in shown if not lot.tax_free and lot.source is not LotSource.UNKNOWN]
    next_on = min((lot.tax_free_from for lot in waiting), default=None)
    next_units = (
        None
        if next_on is None
        else sum((lot.units_open for lot in waiting if lot.tax_free_from == next_on), Decimal(0))
    )
    return CryptoCoin(
        account_id=account_id,
        asset_id=asset_id,
        symbol=symbol,
        name=name,
        units=units,
        price_eur=None if price is None else price.price_eur,
        price_on=None if price is None else price.on,
        price_source=None if price is None else price.source,
        price_read_at=(
            price.fetched_at if price is not None and price.source is PriceSourceKind.SPOT else None
        ),
        value_eur=value,
        cost_eur=cost.eur,
        cost_priced_eur=cost.priced_eur,
        unpriced_lots=cost.unpriced,
        unrealized_eur=None if value is None or cost.eur is None else value - cost.eur,
        tax_free_units=free_units,
        tax_free_value_eur=_value(free_units, price),
        next_tax_free_from=next_on,
        next_tax_free_units=next_units,
        reconciled=reconciled,
        lots=shown,
    )


def _next_tranche(coins: Iterable[CryptoCoin]) -> NextTranche | None:
    coins = [c for c in coins if c.next_tax_free_from is not None]
    if not coins:
        return None
    on = min(c.next_tax_free_from for c in coins if c.next_tax_free_from is not None)
    parts = []
    for c in coins:
        if c.next_tax_free_from != on or c.next_tax_free_units is None:
            continue
        price = c.price_eur
        parts.append(
            TranchePart(
                account_id=c.account_id,
                asset_id=c.asset_id,
                symbol=c.symbol,
                units=c.next_tax_free_units,
                value_eur=None if price is None else _cents(c.next_tax_free_units * price),
            )
        )
    values = [p.value_eur for p in parts]
    total = None if any(v is None for v in values) else sum(values, Decimal("0.00"))
    return NextTranche(on=on, parts=parts, value_eur=total)


def summary(
    on: date,
    coins: Sequence[CryptoCoin],
    accounts: Sequence[CryptoAccount],
    unfilled_movements: int = 0,
    unfilled_account_ids: Sequence[UUID] = (),
) -> CryptoHoldings:
    """The tab: `accounts` carry their label, kind and owners; their value and unvalued count
    are summed here from `coins`."""
    by_account: dict[UUID, list[CryptoCoin]] = {}
    for c in coins:
        by_account.setdefault(c.account_id, []).append(c)
    shown_accounts = [
        a.model_copy(
            update={
                "value_eur": sum(
                    (c.value_eur for c in by_account.get(a.account_id, []) if c.value_eur),
                    Decimal("0.00"),
                ),
                "unvalued_coins": sum(
                    1 for c in by_account.get(a.account_id, []) if c.value_eur is None
                ),
            }
        )
        for a in accounts
    ]
    known = [c for c in coins if c.unrealized_eur is not None]
    free = [c for c in coins if c.tax_free_units > 0]
    return CryptoHoldings(
        on=on,
        total_eur=sum((c.value_eur for c in coins if c.value_eur is not None), Decimal("0.00")),
        unvalued_coins=sum(1 for c in coins if c.value_eur is None),
        unrealized_eur=sum(
            (c.unrealized_eur for c in known if c.unrealized_eur is not None), Decimal("0.00")
        ),
        unrealized_excluded=len(coins) - len(known),
        tax_free_today_eur=sum(
            (c.tax_free_value_eur for c in free if c.tax_free_value_eur is not None),
            Decimal("0.00"),
        ),
        tax_free_today_excluded=sum(1 for c in free if c.tax_free_value_eur is None),
        next_tranche=_next_tranche(coins),
        accounts=shown_accounts,
        coins=list(coins),
        unfilled_movements=unfilled_movements,
        unfilled_account_ids=list(unfilled_account_ids),
    )
