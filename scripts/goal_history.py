"""Write backend/src/hausbuch/rules/goal/history.yaml from the published files (phase 10 §7.11
(c) 3, „Route 3 as decided for the build“ point 8; D310 amended).

Run from the repo root on files downloaded into their own empty directories (never into the
repo), with Python in isolated mode:

    python3 -I scripts/goal_history.py --read-on 2026-11-25 \\
        --coins <dir of coinmetrics/data csv/*.csv> \\
        --ecb <eurofxref-hist.csv> --bbk <BBEX3.D.USD.DEM.AA.AC.000 csv> \\
        > backend/src/hausbuch/rules/goal/history.yaml

Sources:
- Coin Metrics' community data archive (https://github.com/coinmetrics/data, csv/<coin>.csv),
  CC BY-NC 4.0 (its LICENSE): each coin's PriceUSD on 31 December, as published.
- The ECB's euro reference rates (eurofxref-hist): US dollars per euro on the last day of
  each year with a rate, from 1999.
- The Deutsche Bundesbank's daily Frankfurt fixing BBEX3.D.USD.DEM.AA.AC.000: DM per US
  dollar on the last day of each year with a rate, 1990–1998.

Stdlib only; the output is deterministic for the same files. Kenneth French's file is not read
here: it is downloaded into the database (`reference_return`) and never committed.
"""

import argparse
import csv
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

# The coins route 3 can weigh: Coin Metrics' archive files that publish PriceUSD. Stablecoins
# are not priced from the archive (they follow the USD/EUR rate in code), and SOL, AVAX,
# MATIC/POL carry no PriceUSD in the community archive, so they are absent.
COINS = ("ada", "bch", "bnb", "btc", "doge", "dot", "eth")
COINS += ("link", "ltc", "trx", "uni", "xlm", "xrp")
# The first 31 December whose price starts a coin's history: the first year-end after its
# listing (its first full year is the next), except Bitcoin, whose history starts with 2014's
# return (the spec, point 2), so with the 31 Dec 2013 price.
FIRST_PRICE = {"btc": 2013}
LAST_YEAR = 2025  # the newest complete year when the files were read


def _dec(text: str) -> Decimal | None:
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d > 0 else None


def coin_prices(path: Path, first: int | None) -> dict[int, str]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = csv.DictReader(f)
        out = {}
        for row in rows:
            day = row.get("time", "")
            if not day.endswith("-12-31"):
                continue
            year = int(day[:4])
            text = (row.get("PriceUSD") or "").strip()
            if year > LAST_YEAR or _dec(text) is None:
                continue
            out[year] = text
    if first is not None:
        out = {y: v for y, v in out.items() if y >= first}
    return dict(sorted(out.items()))


def ecb_year_ends(path: Path) -> dict[int, str]:
    last: dict[int, tuple[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day, text = row["Date"], row["USD"].strip()
            year = int(day[:4])
            if year > LAST_YEAR or _dec(text) is None:
                continue
            if year not in last or day > last[year][0]:
                last[year] = (day, text)
    return {y: last[y][1] for y in sorted(last)}


def bbk_year_ends(path: Path) -> dict[int, str]:
    last: dict[int, tuple[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row.get("BBK_ID") != "BBEX3.D.USD.DEM.AA.AC.000":
                continue
            day, text = row["TIME_PERIOD"], row["OBS_VALUE"].strip()
            year = int(day[:4])
            if not 1990 <= year <= 1998 or _dec(text) is None:
                continue
            if year not in last or day > last[year][0]:
                last[year] = (day, text)
    return {y: last[y][1] for y in sorted(last)}


def _mapping(values: dict[int, str], indent: str) -> list[str]:
    return [f"{indent}{y}: '{v}'" for y, v in values.items()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--read-on", required=True)
    ap.add_argument("--coins", required=True, type=Path)
    ap.add_argument("--ecb", required=True, type=Path)
    ap.add_argument("--bbk", required=True, type=Path)
    a = ap.parse_args()
    ecb, bbk = ecb_year_ends(a.ecb), bbk_year_ends(a.bbk)
    out = [
        "# Route 3's committed reference data (phase 10 §7.11 (c) 3, D310 amended): written by",
        "# scripts/goal_history.py from the published files, never typed. Funds (Kenneth French's",
        "# Developed market) are not here: they are downloaded into the table reference_return.",
        "# The yearly returns in euros are computed by rules/goal/history (eigene Berechnung).",
        "fx:",
        "  ecb_usd_per_eur:",
        "    source: Europäische Zentralbank, Euro-Referenzkurse (US-Dollar je Euro)",
        "    url: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip",
        "    series: eurofxref-hist, USD, last rate of each year",
        f"    read_on: '{a.read_on}'",
        "    licence: ESCB statistics, free reuse, commercial or not, source quoted, unmodified",
        "    terms: https://www.ecb.europa.eu/home/disclaimer/html/index.en.html",
        "    attribution: 'Source: ECB statistics'",
        "    repo_allowed: true",
        "    commercial_use: true",
        "    values:",
        *_mapping(ecb, "      "),
        "  bbk_dem_per_usd:",
        "    source: Deutsche Bundesbank, Devisenkurse der Frankfurter Börse (DM je US-Dollar)",
        "    url: https://api.statistiken.bundesbank.de/rest/data/BBEX3/D.USD.DEM.AA.AC.000",
        "    series: BBEX3.D.USD.DEM.AA.AC.000, last fixing of each year, 1990-1998",
        f"    read_on: '{a.read_on}'",
        "    licence: ESCB statistics, free reuse, commercial or not, source quoted, unmodified",
        "    terms: https://www.bundesbank.de/en/homepage/user-information/terms-of-use-642972?index=6",
        "    attribution: 'Source: Deutsche Bundesbank'",
        "    repo_allowed: true",
        "    commercial_use: true",
        "    note: before 1999 US dollars per euro = 1.95583 ÷ DM per US dollar"
        " (eigene Berechnung)",
        "    values:",
        *_mapping(bbk, "      "),
        "coins:",
        "  source: Coin Metrics Community Data, PriceUSD on 31 December",
        "  url: https://github.com/coinmetrics/data",
        f"  read_on: '{a.read_on}'",
        "  licence: CC BY-NC 4.0, own non-commercial use",
        "  terms: https://raw.githubusercontent.com/coinmetrics/data/master/LICENSE",
        "  attribution: Coin Metrics, Inc.",
        "  repo_allowed: true",
        "  commercial_use: false",
        "  prices:",
    ]
    for coin in COINS:
        prices = coin_prices(a.coins / f"{coin}.csv", FIRST_PRICE.get(coin))
        if len(prices) < 2:
            continue
        out.append(f"    {coin.upper()}:")
        out += _mapping(prices, "      ")
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
