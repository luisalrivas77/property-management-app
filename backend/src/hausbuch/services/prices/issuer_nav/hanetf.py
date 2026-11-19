"""HANetf NAV history (docs/phases/phase-6.md D174 amended 19 Nov 2026 (b)).

Two requests per fund (read live 19 Nov 2026 with the app's own client and user agent):
    GET https://hanetf.com/fund/<slug>/
        the fund page: its ISIN (key information and the page's ld+json), „Base Currency“,
        and the „NAV History“ link to hanetf.com/wp-content/assets/upload/
        NAV_history-<TICKER>-<ISIN>-all-all.xlsx
    GET that XLSX: `Fund Name:`, `Ticker:` and `ISIN:` above the table `Date | NAV | ...`,
        the day an Excel day number, oldest first.
The reference is the page's slug. The link is followed only on hanetf.com and only when it
names the page's ISIN, and the file's ISIN must equal the page's.

No catalogue lists ISINs, so a fund is found by its name (Q-O172): the fund sitemap
(https://hanetf.com/fund-sitemap.xml) lists the English fund pages, and the pages whose slug
words the fund's name starts (at least two words, the brand and the legal words left out)
are read, the best first and at most three a run, until one states the ISIN.
"""

import html
import json
import re
from datetime import date
from typing import ClassVar

from lxml import etree

from hausbuch.schemas.prices import NavPoint

from ..http import SourceError, get
from . import xlsx
from .base import ISIN, IssuerAdapter, NavFile, nav

PAGE_URL = "https://hanetf.com/fund/{slug}/"
SITEMAP_URL = "https://hanetf.com/fund-sitemap.xml"
MAX_PAGES = 3  # pages read a run to find one fund by its name
MIN_SCORE = 2  # name words that must start a slug word
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_PAGE_LOC = re.compile(r"^https://hanetf\.com/fund/([a-z0-9]+(?:-[a-z0-9]+)*)/$")
_FILE = re.compile(
    r'href="(https://hanetf\.com/wp-content/assets/upload/'
    r'NAV_history-[A-Za-z0-9]+-([A-Z]{2}[A-Z0-9]{9}[0-9])-all-all\.xlsx)"'
)
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)
_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_TAG = re.compile(r"<[^>]+>")
_ISIN_FACT = re.compile(r"\bISIN:?\s+([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_CURRENCY_FACT = re.compile(r"\bBase Currency:?\s+([A-Z]{3})\b")
_NAME_WORD = re.compile(r"[a-z0-9]+")
_IGNORED = {"hanetf", "icav", "plc", "uetf", "etf", "ucits", "acc", "dist", "the", "and", "of"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def score(name: str, slug: str) -> int:
    """How many of the name's words start a word of the slug (the brand and the legal words
    such as „UCITS“ or „Acc“ not counted)."""
    words = [w for w in _NAME_WORD.findall(name.lower()) if w not in _IGNORED]
    slug_words = slug.split("-")
    return sum(1 for w in set(words) if any(s.startswith(w) for s in slug_words))


def parse_sitemap(body: bytes) -> list[str]:
    """The English fund pages' slugs, in the sitemap's order."""
    root = etree.fromstring(body, _PARSER)
    slugs = []
    for loc in root.iter(f"{_SITEMAP_NS}loc"):
        match = _PAGE_LOC.fullmatch((loc.text or "").strip())
        if match and match.group(1) not in slugs:
            slugs.append(match.group(1))
    if not slugs:
        raise SourceError("parse")
    return slugs


def parse_page(body: bytes) -> tuple[str, str, str]:
    """(ISIN, base currency, the NAV history file's address) the fund page states."""
    page = body.decode("utf-8", errors="replace")
    isins = set(
        _ISIN_FACT.findall(" ".join(html.unescape(_TAG.sub(" ", _SCRIPT.sub(" ", page))).split()))
    )
    for block in _LD_JSON.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for ident in data.get("identifier") or [] if isinstance(data, dict) else []:
            if isinstance(ident, dict) and ident.get("propertyID") == "ISIN":
                isins.add(str(ident.get("value")))
    text = " ".join(html.unescape(_TAG.sub(" ", _SCRIPT.sub(" ", page))).split())
    currencies = set(_CURRENCY_FACT.findall(text))
    if len(isins) != 1 or len(currencies) != 1:
        raise SourceError("parse")
    isin, currency = next(iter(isins)), next(iter(currencies))
    if not ISIN.fullmatch(isin):
        raise SourceError("parse")
    links = {url for url, named in _FILE.findall(page) if named == isin}
    if len(links) != 1:
        raise SourceError("parse")
    return isin, currency, next(iter(links))


class HanetfAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "hanetf"
    brands: ClassVar[tuple[str, ...]] = ("hanetf",)
    checked_candidates: ClassVar[bool] = True

    def url(self, fund_ref: str) -> str:
        if not _SLUG.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        return PAGE_URL.format(slug=fund_ref)

    def _page(self, slug: str) -> bytes:
        key = f"page:{slug}"
        if key not in self.memo:
            try:
                self.memo[key] = get(self.fetcher, self.url(slug), max_bytes=self.max_bytes)
            except SourceError as exc:
                self.memo[key] = exc
        found = self.memo[key]
        if isinstance(found, SourceError):
            raise found
        return found

    def candidates(self, isin: str, name: str) -> list[str]:
        """The page among the sitemap's best matches for the name that states the ISIN."""
        if "sitemap" not in self.memo:
            self.memo["sitemap"] = parse_sitemap(
                get(self.fetcher, SITEMAP_URL, max_bytes=self.max_bytes)
            )
        scored = sorted(
            ((score(name, slug), i, slug) for i, slug in enumerate(self.memo["sitemap"])),
            key=lambda s: (-s[0], s[1]),
        )
        for points, _, slug in scored[:MAX_PAGES]:
            if points < MIN_SCORE:
                break
            try:
                stated, _, _ = parse_page(self._page(slug))
            except SourceError as exc:
                if exc.kind == "HTTPError" and exc.status in (404, 410):
                    continue
                if exc.kind == "parse":
                    continue
                raise
            if stated == isin:
                return [slug]
        return []

    def load(self, fund_ref: str, since: date | None, isin: str | None = None) -> NavFile:
        stated, currency, link = parse_page(self._page(fund_ref))
        if isin is not None and stated != isin:
            raise SourceError("isin_mismatch")  # its file is never asked
        rows = xlsx.rows(get(self.fetcher, link, max_bytes=self.max_bytes))
        header = next(
            (i for i, r in enumerate(rows) if r.get("A") == "Date" and r.get("B") == "NAV"),
            None,
        )
        if header is None:
            raise SourceError("parse")
        facts = {r["A"]: r.get("B", "") for r in rows[:header] if "A" in r}
        if facts.get("ISIN:") != stated:
            raise SourceError("isin_mismatch")
        points: dict[date, NavPoint] = {}
        for row in rows[header + 1 :]:
            on, value = xlsx.day(row.get("A", "")), xlsx.number(row.get("B", ""))
            if on is None or value is None:
                continue
            point = nav(on, value, currency)
            if point is None:
                continue
            if on in points and points[on] != point:
                raise SourceError("parse")
            points[on] = point
        if not points:
            raise SourceError("parse")
        return NavFile(isin=stated, currency=currency, points=list(points.values()))
