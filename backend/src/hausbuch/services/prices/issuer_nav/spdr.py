"""State Street (SPDR) NAV history (docs/phases/phase-6.md D174 (1)).

URL (read live 13 Nov 2026, the „NAV History“ download of the fund's ssga.com page):
    https://www.ssga.com/library-content/products/fund-data/etfs/emea/navhist-emea-en-<slug>.xlsx
where <slug> is the fund's ticker slug (`sppw-gy`), the registry's fund_ref.

Layout (one sheet `navhist`, every text a shared string):
    row 1  Fund Name:     | <name>
    row 2  ISIN:          | <ISIN>
    row 3  Report Date:   | 13-Nov-2026
    row 4  NAV Currency:  | USD
    row 6  Date | NAV | Shares Outstanding | Share Class Assets | Total Fund Assets (USD)
    row 7+ 12-Nov-2026 | 52.9838 (a number cell) | ...
    last   a disclaimer in column A only
Newest first. The NAV is in the currency the file names (one column; the step-0 reading of
three currency columns did not hold for the real file). Read with zipfile and lxml, no
external entities, and the unpacked size capped so a crafted ZIP cannot exhaust memory.

Catalogue (read live 16 Nov 2026: the data of ssga.com's German fund finder, „Fondsfinder“,
https://www.ssga.com/de/de/individual/etfs/fund-finder): one JSON document, about 600 kB,
`data.funds.etfs.datas` a list of 177 funds; a fund names its ISIN and ticker slug in its
documents' links (`/library-content/kids?isin=<ISIN>&...&ticker=<slug>`), the slug the NAV
file's address takes.
"""

import json
import re
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError
from . import xlsx
from .base import IssuerAdapter, NavFile, nav, unique_refs

URL = (
    "https://www.ssga.com/library-content/products/fund-data/etfs/emea/navhist-emea-en-{slug}.xlsx"
)
CATALOGUE_URL = (
    "https://www.ssga.com/bin/v1/ssmp/fund/fundfinder?country=de&language=de&role=individual"
    "&product=etfs&ui=fund-finder"
)
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _document_links(fund: dict) -> list[str]:
    links = []
    for group in fund.get("documentPdf") or []:
        docs = group.get("docs") if isinstance(group, dict) else None
        for doc in docs if isinstance(docs, list) else []:
            path = doc.get("path") if isinstance(doc, dict) else None
            if isinstance(path, str):
                links.append(path)
    return links


class SpdrAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "spdr"
    catalogue_url: ClassVar[str | None] = CATALOGUE_URL
    brands: ClassVar[tuple[str, ...]] = ("spdr", "ssga", "state street")

    def url(self, fund_ref: str) -> str:
        if not _SLUG.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        return URL.format(slug=fund_ref)

    @staticmethod
    def parse_catalogue(body: bytes) -> dict[str, str]:
        funds = json.loads(body)["data"]["funds"]["etfs"]["datas"]
        pairs = []
        for fund in funds if isinstance(funds, list) else []:
            if not isinstance(fund, dict):
                continue
            for link in _document_links(fund):
                query = parse_qs(urlsplit(link).query)
                isin, slug = query.get("isin", [""])[0], query.get("ticker", [""])[0].lower()
                if isin and _SLUG.fullmatch(slug):
                    pairs.append((isin, slug))
        return unique_refs(pairs)

    @staticmethod
    def parse(body: bytes) -> NavFile:
        rows = xlsx.rows(body)
        facts = {r["A"]: r.get("B", "") for r in rows if r.get("A", "").endswith(":")}
        isin, currency = facts.get("ISIN:", ""), facts.get("NAV Currency:", "")
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin) or not re.fullmatch(
            r"[A-Z]{3}", currency
        ):
            raise SourceError("parse")
        header = next(
            (i for i, r in enumerate(rows) if r.get("A") == "Date" and r.get("B") == "NAV"),
            None,
        )
        if header is None:
            raise SourceError("parse")
        points: list[NavPoint] = []
        for row in rows[header + 1 :]:
            on = xlsx.day(row.get("A", ""))
            if on is None:
                continue  # the blank row and the disclaimer
            value = xlsx.number(row.get("B", ""))
            if value is None:
                continue
            point = nav(on, value, currency)
            if point is not None:
                points.append(point)
        if not points:
            raise SourceError("parse")
        return NavFile(isin=isin, currency=currency, points=points)
