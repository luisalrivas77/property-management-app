"""What every issuer adapter shares (docs/phases/phase-6.md D174 (2), amended 16 Nov 2026).

An adapter reads one NAV file per fund reference. An adapter whose issuer publishes a
catalogue of its funds (`catalogue_url`) also finds a fund's reference by its ISIN: the
fetch job asks the catalogue once a run for a held fund the registry does not list, so the
household's funds never have to be written into the repository. A reference found there is
only a candidate: `read_points` still refuses a file stating another ISIN.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar

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
    # names such as „iShsIII-Core MSCI World“ too): the catalogue is asked only for a fund so
    # named, so a fund of another issuer costs no catalogue download. A false match costs one
    # download at most; the catalogue must still list the ISIN.
    brands: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def names_brand(cls, name: str) -> bool:
        words = re.sub(r"[^a-z0-9&]+", " ", name.lower())
        return any(re.search(rf"(^| ){re.escape(b)}", words) for b in cls.brands)

    fetcher: Fetch = field(repr=False)

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

    def read(self, fund_ref: str) -> NavFile:
        body = get(self.fetcher, self.url(fund_ref), max_bytes=self.max_bytes)
        try:
            return self.parse(body)
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


def read_points(adapter: IssuerAdapter, fund_ref: str, isin: str) -> NavFile:
    """The file for a registry entry, refused (SourceError "isin_mismatch") when it states
    another ISIN than the registry's."""
    found = adapter.read(fund_ref)
    if found.isin != isin:
        raise SourceError("isin_mismatch")
    return found
