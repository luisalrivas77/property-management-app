"""Assets and their prices (docs/phases/phase-6.md D167, D174, §12, §13; phase 6 step 5b).

- GET /assets/overview?on=: the Anlagen Übersicht tab (mockup 09): per depot, exchange and
  wallet account its positions on `on` (today, Berlin time, by default) with units, the
  price, its source and day, the value, the stale marker and „Kurs aus dem letzten Bericht“.
- GET /assets: every asset with how it is priced (`has_source` false: „Kursquelle wählen“)
  and its latest price.
- PATCH /assets/{id}: the name, and a coin's CoinGecko id and Kraken pair. 422
  asset.not_crypto for ids on a fund or a cash asset.
- POST /assets/{id}/prices: a typed price (source user, „Kurs eintragen“). 422 asset.is_euro.

GET /assets/etf (the ETF tab) stays in routes/accounts.py, where step 4 put it.
"""

from datetime import date, datetime
from uuid import UUID

from hausbuch.schemas.household import AssetPatch
from hausbuch.schemas.prices import AssetPriceRow, AssetRow, AssetsOverview, TypedPriceBody
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.prices import overview

from ..deps import Cmd, domain_router

router = domain_router()


def _today() -> date:
    return datetime.now(LOCAL_TIME_ZONE).date()


@router.get("/assets/overview")
def assets_overview(cmd: Cmd, on: date | None = None) -> AssetsOverview:
    """Valued on read from the stored prices; every figure names its source and day."""
    return cmd.read(overview.assets_overview, on or _today())


@router.get("/assets")
def assets_list(cmd: Cmd) -> list[AssetRow]:
    return cmd.read(overview.list_assets, _today())


@router.patch("/assets/{asset_id}")
def asset_update(asset_id: UUID, body: AssetPatch, cmd: Cmd) -> AssetRow:
    """„Kursquelle wählen“: the next daily fetch (07:00 UTC) prices the coin by these ids."""
    return cmd.run(overview.update_asset, asset_id, body)


@router.post("/assets/{asset_id}/prices", status_code=201)
def asset_price_record(asset_id: UUID, body: TypedPriceBody, cmd: Cmd) -> AssetPriceRow:
    """„Kurs eintragen“: wins on its own day (D167 amended 13 Nov 2026)."""
    return cmd.run(overview.record_price, asset_id, body)
