"""Route 3's series in euros from the committed rule data and the funds download (phase 10
§7.11 (c) 3, „Route 3 as decided for the build“ points 7, 8; D310 amended).

`rules/goal/history.yaml` (written by scripts/goal_history.py, never typed) holds the ECB's
year-end US dollars per euro from 1999, the Bundesbank's year-end DM per US dollar 1990–1998
and each coin's 31 December PriceUSD from Coin Metrics' community archive. The funds series
(Kenneth French's Developed market) is never committed: its rows come from the table
`reference_return` and are passed in. Every conversion is done here, by code, so each series
names the rates it was converted with and „eigene Berechnung“ (the ESCB terms ask a
calculation to be stated as such).
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from fractions import Fraction
from functools import cache
from importlib.resources import files
from typing import Any

import yaml

from .history import (
    EUR_PEGGED,
    FUNDS,
    USD_PEGGED,
    History,
    Series,
    coin_returns,
    funds_returns,
    pegged_returns,
    q4_returns,
    usd_per_eur,
)

FUNDS_SOURCE = "Kenneth R. French Data Library, Developed Market (Mkt−RF + RF), jährlich"
FUNDS_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Developed_3_Factors_CSV.zip"
)
FUNDS_LICENCE = "Eigene Nutzung; heruntergeladen, nie im Repository (D310)"
FX_NOTE = (
    "in Euro umgerechnet mit den Jahresendkursen der EZB (ab 1999) und der Deutschen "
    "Bundesbank (DM, bis 1998), eigene Berechnung"
)
FX_SOURCE = "Europäische Zentralbank; Deutsche Bundesbank"
FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
FX_ATTRIBUTION = "Source: ECB statistics; Source: Deutsche Bundesbank"


@cache
def committed() -> dict[str, Any]:
    text = (files("hausbuch.rules.goal") / "history.yaml").read_text("utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not {"fx", "coins", "house_prices"} <= data.keys():
        raise ValueError("rules/goal/history.yaml: fx, coins and house_prices expected")
    return data


def _values(block: Mapping[str, Any]) -> dict[int, Decimal]:
    return {int(y): Decimal(str(v)) for y, v in block["values"].items()}


def _read_on(block: Mapping[str, Any]) -> date | None:
    value = block.get("read_on")
    return None if value is None else date.fromisoformat(str(value))


@cache
def fx() -> dict[int, Fraction]:
    """U_Y, US dollars per euro at the end of each year of the committed rates."""
    rates = committed()["fx"]
    return usd_per_eur(_values(rates["ecb_usd_per_eur"]), _values(rates["bbk_dem_per_usd"]))


@cache
def coin_series() -> dict[str, Series]:
    """One series per coin symbol: the archive's coins, the US dollar stablecoins by the rate
    and the euro ones at 0. A coin with no archive price (SOL, AVAX, POL) has none."""
    coins = committed()["coins"]
    rates = committed()["fx"]["ecb_usd_per_eur"]
    u = fx()
    read_on = _read_on(coins)
    out = {
        str(sym): Series(
            key=str(sym),
            returns=coin_returns({int(y): Decimal(str(v)) for y, v in prices.items()}, u),
            source=str(coins["source"]),
            url=str(coins["url"]),
            licence=str(coins["licence"]),
            attribution=f"{coins['attribution']}; {FX_NOTE}",
            read_on=read_on,
        )
        for sym, prices in sorted(coins["prices"].items())
    }
    dollar = pegged_returns("USD", u)
    euro = pegged_returns("EUR", u)
    for sym in sorted(USD_PEGGED | EUR_PEGGED):
        out[sym] = Series(
            key=sym,
            returns=dollar if sym in USD_PEGGED else euro,
            source=FX_SOURCE if sym in USD_PEGGED else "Euro",
            url=FX_URL,
            licence=str(rates["licence"]),
            attribution=(
                f"{FX_ATTRIBUTION}; dem US-Dollar gleichgesetzt, eigene Berechnung"
                if sym in USD_PEGGED
                else "dem Euro gleichgesetzt: 0 % im Jahr"
            ),
            read_on=_read_on(rates),
        )
    return out


def funds_series(rows: Mapping[int, tuple[Decimal, Decimal]], read_on: date | None) -> Series:
    """French's annual (Mkt−RF, RF) in percent, in USD, as the funds series in euros."""
    return Series(
        key=FUNDS,
        returns=funds_returns(rows, fx()),
        source=FUNDS_SOURCE,
        url=FUNDS_URL,
        licence=FUNDS_LICENCE,
        attribution=f"Kenneth R. French Data Library; {FX_NOTE}",
        read_on=read_on,
    )


def history(
    funds: Mapping[int, tuple[Decimal, Decimal]] | None, read_on: date | None = None
) -> History:
    """Route 3's history: the funds series when a download is stored (None: not yet), and
    every coin's."""
    return History(
        funds=None if not funds else funds_series(funds, read_on),
        coins=coin_series(),
    )


HOUSE_PRICES = "house_prices"  # the house price series' key (§7.9 (e) items 1, 2)
HOUSE_PRICES_NOTE = "nur Preise, ohne Miete und Kosten; Jahresrenditen Q4 zu Q4, eigene Berechnung"


@cache
def house_price_q4() -> dict[int, Decimal]:
    """Each year's fourth-quarter value of Eurostat's German house price index (2015 = 100)
    as committed; a year whose Q4 is not published yet is absent."""
    block = committed()[HOUSE_PRICES]
    out = {}
    for quarter, value in block["values"].items():
        year, q = str(quarter).split("-Q")
        if q == "4":
            out[int(year)] = Decimal(str(value))
    return dict(sorted(out.items()))


def house_price_series(q4: Mapping[int, Decimal] | None = None) -> Series:
    """The house price series: Q4 of Y ÷ Q4 of Y−1 − 1 for each year with both (§7.9 (e) 2).
    `q4` replaces the committed values (the fixtures' invented index); the source stays."""
    block = committed()[HOUSE_PRICES]
    return Series(
        key=HOUSE_PRICES,
        returns=q4_returns(house_price_q4() if q4 is None else q4),
        source=str(block["source"]),
        url=str(block["url"]),
        licence=str(block["licence"]),
        attribution=f"{block['attribution']}; {HOUSE_PRICES_NOTE}",
        read_on=_read_on(block),
    )


__all__ = [
    "FUNDS_URL",
    "HOUSE_PRICES",
    "coin_series",
    "committed",
    "funds_series",
    "fx",
    "history",
    "house_price_q4",
    "house_price_series",
]
