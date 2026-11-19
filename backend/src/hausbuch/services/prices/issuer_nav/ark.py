"""ARK Invest Europe (the former Rize funds) NAV history (docs/phases/phase-6.md D174
amended 19 Nov 2026 (b)).

Page (read live 19 Nov 2026): GET https://europe.ark-funds.com/funds/<section>/<slug>/, the
fund's own page (about 2 MB), whose inline script holds the chart's series
    var rawChartData = [ { "date": new Date("YYYY-MM-DDT00:00:00"), "etf": <NAV>, ... }, ... ];
and whose key facts state „ISIN“, „Base Currency“ and „NAV per share <CUR> <value>“. The
reference is `<section>/<slug>`. The series' last value must equal the page's NAV per share
(else `parse`: the series is not the share class the facts describe). An ISIN in the
holdings table is a constituent's, not the fund's: only a labelled ISIN counts.

Catalogue: GET https://europe.ark-funds.com/funds/, one table row per fund with its page
link and its ISIN.
"""

import html
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError
from .base import IssuerAdapter, NavFile, nav, unique_refs

URL = "https://europe.ark-funds.com/funds/{ref}/"
CATALOGUE_URL = "https://europe.ark-funds.com/funds/"
_REF = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*/[a-z0-9]+(-[a-z0-9]+)*$")
_SERIES = re.compile(r"var\s+rawChartData\s*=\s*\[(.*?)\]\s*;", re.S)
_POINT = re.compile(
    r'"date"\s*:\s*new\s+Date\(\s*"(\d{4}-\d{2}-\d{2})T00:00:00"\s*\)\s*,\s*'
    r'"etf"\s*:\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)'
)
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_ISIN_FACT = re.compile(r"\bISIN:?\s+([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_CURRENCY_FACT = re.compile(r"\bBase Currency:?\s+([A-Z]{3})\b")
_NAV_FACT = re.compile(r"\bNAV per share:?\s+([A-Z]{3})\s+(\d+(?:[.,]\d+)?)\b")
_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
_LINK = re.compile(r'href="(?:https://europe\.ark-funds\.com)?/funds/([a-z0-9-]+/[a-z0-9-]+)/"')
_CELL_ISIN = re.compile(r"<td[^>]*>\s*([A-Z]{2}[A-Z0-9]{9}[0-9])\s*</td>")


def page_text(body: str) -> str:
    """The page's visible text, one space between words (scripts and styles left out)."""
    text = _TAG.sub(" ", _SCRIPT.sub(" ", body))
    return " ".join(html.unescape(text).split())


class ArkAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "ark"
    catalogue_url: ClassVar[str | None] = CATALOGUE_URL
    brands: ClassVar[tuple[str, ...]] = ("ark inv", "ark i", "rize")

    def url(self, fund_ref: str) -> str:
        if not _REF.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        return URL.format(ref=fund_ref)

    @staticmethod
    def parse_catalogue(body: bytes) -> dict[str, str]:
        pairs = []
        for row in _ROW.findall(body.decode("utf-8", errors="replace")):
            links, isins = set(_LINK.findall(row)), set(_CELL_ISIN.findall(row))
            if len(links) == 1 and len(isins) == 1:
                pairs.append((next(iter(isins)), next(iter(links))))
        return unique_refs(pairs)

    @staticmethod
    def parse(body: bytes) -> NavFile:
        page = body.decode("utf-8", errors="replace")
        text = page_text(page)
        isins = set(_ISIN_FACT.findall(text))
        currencies = set(_CURRENCY_FACT.findall(text))
        stated = set(_NAV_FACT.findall(text))
        if len(isins) != 1 or len(currencies) != 1 or len(stated) != 1:
            raise SourceError("parse")
        isin, currency = next(iter(isins)), next(iter(currencies))
        nav_currency, nav_text = next(iter(stated))
        series = _SERIES.search(page)
        if nav_currency != currency or series is None:
            raise SourceError("parse")
        points: dict[date, NavPoint] = {}
        for day, value in _POINT.findall(series.group(1)):
            try:
                on, number = date.fromisoformat(day), Decimal(value)
            except (ValueError, InvalidOperation):
                raise SourceError("parse") from None
            point = nav(on, number, currency)
            if point is None:
                continue
            if on in points and points[on] != point:
                raise SourceError("parse")
            points[on] = point
        if not points:
            raise SourceError("parse")
        last = points[max(points)]
        if last.nav != nav(last.on, Decimal(nav_text.replace(",", ".")), currency).nav:
            raise SourceError("parse")
        return NavFile(isin=isin, currency=currency, points=list(points.values()))
