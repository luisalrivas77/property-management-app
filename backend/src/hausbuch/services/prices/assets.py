"""Assets from the registry and the price sources each one has (docs/phases/phase-6.md D167
(1), D174 (2)).

- `asset_for_symbol`: the workspace's asset of a symbol, created on first sight; a registry
  coin gets its name and its CoinGecko id and Kraken pair, a currency code is a cash asset.
- `cash_asset`: the cash asset of a currency, where the ECB rates are stored.
- `price_ids` and `fund_source`: how an asset is priced. The asset row's own ids win (a
  person may set them, PATCH /assets/{id}); otherwise the registry's. An ETF is priced by its
  issuer's adapter when the registry names one with a fund reference.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, insert, select

from hausbuch.db.tables import asset as asset_table
from hausbuch.rules.household.assets import FundEntry, registry
from hausbuch.schemas.events import AssetRecorded, EventType
from hausbuch.schemas.household import AssetKind
from hausbuch.services.context import CommandContext
from hausbuch.services.events import append_event

from .issuer_nav import ADAPTERS

CASH_CODES = frozenset({"EUR", "USD", "GBP", "CHF"})  # the cash assets a price may be in
CASH_NAMES = {
    "EUR": "Euro",
    "USD": "US-Dollar",
    "GBP": "Britisches Pfund",
    "CHF": "Schweizer Franken",
}


@dataclass(frozen=True)
class PriceIds:
    coingecko_id: str | None
    kraken_pair: str | None

    @property
    def any(self) -> bool:
        return self.coingecko_id is not None or self.kraken_pair is not None


def price_ids(symbol: str | None, coingecko_id: str | None, kraken_pair: str | None) -> PriceIds:
    """A crypto asset's ids: its own row's, else the registry's for its symbol."""
    coin = registry().coin(symbol) if symbol else None
    return PriceIds(
        coingecko_id=coingecko_id or (coin.coingecko_id if coin else None),
        kraken_pair=kraken_pair or (coin.kraken_pair if coin else None),
    )


def fund_source(isin: str | None) -> FundEntry | None:
    """The registry entry of an ETF whose issuer has an adapter and a fund reference."""
    fund = registry().fund(isin) if isin else None
    if fund is None or fund.fund_ref is None or fund.issuer not in ADAPTERS:
        return None
    return fund


def _create(ctx: CommandContext, values: dict) -> UUID:
    asset_id = ctx.conn.execute(
        insert(asset_table)
        .values(workspace_id=ctx.workspace_id, created_at=ctx.now, updated_at=ctx.now, **values)
        .returning(asset_table.c.id)
    ).scalar_one()
    append_event(
        ctx,
        EventType.ASSET_CREATED,
        asset_id,
        AssetRecorded(
            kind=values["kind"], symbol=values.get("symbol"), isin=None, name=values["name"]
        ),
    )
    return asset_id


def _by_symbol(conn: Connection, symbol: str) -> UUID | None:
    return conn.execute(
        select(asset_table.c.id).where(asset_table.c.symbol == symbol)
    ).scalar_one_or_none()


def cash_asset(ctx: CommandContext, code: str) -> UUID:
    """The cash asset of a currency (kind cash, symbol = the code), created on first use."""
    found = _by_symbol(ctx.conn, code)
    if found is not None:
        return found
    return _create(
        ctx,
        {
            "kind": AssetKind.CASH.value,
            "symbol": code,
            "name": CASH_NAMES.get(code, code),
            "currency": code,
        },
    )


def asset_for_symbol(ctx: CommandContext, symbol: str) -> UUID:
    """The asset of a symbol as an exchange export names it, created on first sight: a
    currency code is cash; a registry coin carries its name and price ids; any other symbol
    is a crypto asset without a price source (the to-do „Kursquelle wählen“)."""
    symbol = symbol.strip().upper()
    if symbol in CASH_CODES:
        return cash_asset(ctx, symbol)
    found = _by_symbol(ctx.conn, symbol)
    if found is not None:
        return found
    coin = registry().coin(symbol)
    return _create(
        ctx,
        {
            "kind": AssetKind.CRYPTO.value,
            "symbol": symbol,
            "name": coin.name if coin else symbol,
            "coingecko_id": coin.coingecko_id if coin else None,
            "kraken_pair": coin.kraken_pair if coin else None,
        },
    )
