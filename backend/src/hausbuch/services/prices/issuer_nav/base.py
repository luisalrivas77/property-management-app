"""What every issuer adapter shares (docs/phases/phase-6.md D174 (2), amended 16 Nov 2026).

An adapter reads one NAV file per fund reference. An adapter whose issuer publishes a
catalogue of its funds (`catalogue_url`) also finds a fund's reference by its ISIN: the
fetch job asks the catalogue once a run for a held fund the registry does not list, so the
household's funds never have to be written into the repository. A reference found there is
only a candidate: `read_points` still refuses a file stating another ISIN.

D174 amended 19 Nov 2026: an adapter whose issuer answers by the ISIN itself (Amundi,
Xtrackers, Invesco, BNP Paribas, L&G) needs no catalogue: `candidates` names the references
to try for an ISIN (the ISIN, or BNP's one per currency), and HANetf's names the fund pages
its sitemap suggests for the fund's name. A read may make more than one request (`load`), a
service with a date range is asked from `since` (the fund's first movement), and an
adapter may keep one answer for every fund of the run (L&G's one document), since the job
builds one adapter per issuer and run. `not_listed` is the issuer's answer that it lists no
such fund.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar

from hausbuch.schemas.prices import NavPoint

from ..http import MAX_BYTES, Fetch, SourceError, get

PLACES = Decimal("0.00000001")
ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
CATALOGUE_MAX_BYTES = 16 * 1024 * 1024  # iShares' is about 4 MB (16 Nov 2026)


@dataclass(frozen=True)
class NavFile:
    """One issuer file: the ISIN and NAV currency it states, and its NAV per day (newest
    first is not assumed; callers sort)."""

    isin: str
    currency: str
    points: list[NavPoint]


def nav(on: date, value: Decimal, currency: str) -> NavPoint | None:
    """A NavPoint, or None for a value that is not a positive price."""
    rounded = value.quantize(PLACES, rounding=ROUND_HALF_UP)
    if rounded <= 0:
        return None
    return NavPoint(on=on, nav=rounded, currency=currency)


@dataclass
class IssuerAdapter:
    """An adapter reads one file per fund reference. `parse` is pure (the tests feed it the
    recorded samples); `read` fetches and parses; `fetch` is the IssuerNavAdapter protocol
    of schemas/household.py."""

    issuer: ClassVar[str]
    max_bytes: ClassVar[int] = MAX_BYTES
    catalogue_url: ClassVar[str | None] = None  # the issuer's list of its funds, if any
    # How a fund's name names the issuer (lower case, a word starting so: the banks' short
    # names such as „iShsIII-Example Fund“ too): the catalogue is asked only for a fund so
    # named, so a fund of another issuer costs no catalogue download. A false match costs one
    # download at most; the catalogue must still list the ISIN.
    brands: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def names_brand(cls, name: str) -> bool:
        words = re.sub(r"[^a-z0-9&]+", " ", name.lower())
        return any(re.search(rf"(^| ){re.escape(b)}", words) for b in cls.brands)

    fetcher: Fetch = field(repr=False)
    # Answers kept for the run (an issuer whose one document lists every fund).
    memo: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # A fund reference this adapter can derive from the ISIN alone (no catalogue needed).
    isin_keyed: ClassVar[bool] = False

    def candidates(self, isin: str, name: str) -> list[str]:
        """The references to try, in order, for a fund the registry and the catalogues do
        not name; each is read until one states the ISIN. The ISIN itself for an adapter
        keyed by it, nothing otherwise. May ask the issuer (HANetf's sitemap)."""
        return [isin] if self.isin_keyed else []

    def url(self, fund_ref: str) -> str:
        raise NotImplementedError

    @staticmethod
    def parse(body: bytes) -> NavFile:
        raise NotImplementedError

    @staticmethod
    def parse_catalogue(body: bytes) -> dict[str, str]:
        """ISIN -> fund reference, each reference one `url` accepts; an ISIN listed with two
        different references is left out (never guessed)."""
        raise NotImplementedError

    def catalogue(self) -> dict[str, str]:
        if self.catalogue_url is None:
            return {}
        body = get(self.fetcher, self.catalogue_url, max_bytes=CATALOGUE_MAX_BYTES)
        try:
            found = self.parse_catalogue(body)
        except SourceError:
            raise
        except Exception:  # noqa: BLE001 - any reader failure is "the file changed"
            raise SourceError("parse") from None
        if not found:
            raise SourceError("parse")
        return found

    def load(self, fund_ref: str, since: date | None) -> NavFile:
        """The request(s) and the reading; one GET of `url` by default."""
        return self.parse(get(self.fetcher, self.url(fund_ref), max_bytes=self.max_bytes))

    def read(self, fund_ref: str, since: date | None = None) -> NavFile:
        try:
            return self.load(fund_ref, since)
        except SourceError:
            raise
        except Exception:  # noqa: BLE001 - any reader failure is "the file changed"
            raise SourceError("parse") from None

    def fetch(self, fund_ref: str) -> list[NavPoint]:
        return self.read(fund_ref).points


def unique_refs(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """The catalogue from (ISIN, reference) pairs: a valid ISIN with exactly one reference."""
    refs: dict[str, set[str]] = {}
    for isin, ref in pairs:
        if ISIN.fullmatch(isin) and ref:
            refs.setdefault(isin, set()).add(ref)
    return {isin: next(iter(found)) for isin, found in refs.items() if len(found) == 1}


def parse_json(body: bytes) -> Any:
    """JSON with every number a Decimal (never a float: a NAV is money)."""
    return json.loads(body, parse_float=Decimal, parse_int=Decimal)


def read_points(
    adapter: IssuerAdapter, fund_ref: str, isin: str, since: date | None = None
) -> NavFile:
    """The file for a registry entry, refused (SourceError "isin_mismatch") when it states
    another ISIN than the registry's."""
    found = adapter.read(fund_ref, since)
    if found.isin != isin:
        raise SourceError("isin_mismatch")
    return found
