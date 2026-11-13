"""What every issuer adapter shares (docs/phases/phase-6.md D174 (2))."""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar

from hausbuch.schemas.prices import NavPoint

from ..http import MAX_BYTES, Fetch, SourceError, get

PLACES = Decimal("0.00000001")


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

    fetcher: Fetch = field(repr=False)

    def url(self, fund_ref: str) -> str:
        raise NotImplementedError

    @staticmethod
    def parse(body: bytes) -> NavFile:
        raise NotImplementedError

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


def read_points(adapter: IssuerAdapter, fund_ref: str, isin: str) -> NavFile:
    """The file for a registry entry, refused (SourceError "isin_mismatch") when it states
    another ISIN than the registry's."""
    found = adapter.read(fund_ref)
    if found.isin != isin:
        raise SourceError("isin_mismatch")
    return found
