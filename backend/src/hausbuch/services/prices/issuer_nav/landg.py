"""Legal & General (L&G ETFs) latest NAV (docs/phases/phase-6.md D174 amended 19 Nov 2026
(b)).

Request (read live 19 Nov 2026, the fund centre's own data):
    GET https://fundcentres.landg.com/srp/api/fund-centre/48?audience=150&language=3
one JSON document for every fund (about 190 kB), asked once a run for all held L&G funds.
Each share class's values are positional, named by `metadata.share_class_fields[].code_name`:
`shareclassISIN`, `shareclassCurrency`, `midPrice` (labelled „NAV“ by the document's own
metadata) and `midPrice__date`. Only the latest NAV: no history is published (Q-O174), so
days before the first run stay on the depot's reports. An ISIN listed under two share
classes with different prices is never guessed (`parse`), and one with no date is not read.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from ..http import SourceError, get
from .base import ISIN, IssuerAdapter, NavFile, nav, parse_json

URL = "https://fundcentres.landg.com/srp/api/fund-centre/48?audience=150&language=3"
_FIELDS = ("shareclassISIN", "shareclassCurrency", "midPrice", "midPrice__date")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, str):
        try:
            found = Decimal(value.strip())
        except InvalidOperation:
            return None
        return found if found.is_finite() else None
    return None


def share_classes(body: bytes) -> list[dict[str, Any]]:
    """Every share class of the document as code name -> value."""
    document = parse_json(body)
    fields = document["metadata"]["share_class_fields"]
    names = [f["code_name"] for f in fields]
    if any(field not in names for field in _FIELDS):
        raise SourceError("parse")
    found = []
    for fund in document["funds"]:
        for share_class in fund.get("share_classes") or []:
            values = share_class.get("data")
            if isinstance(values, list) and len(values) == len(names):
                found.append(dict(zip(names, values, strict=True)))
    if not found:
        raise SourceError("parse")
    return found


class LandgAdapter(IssuerAdapter):
    issuer: ClassVar[str] = "landg"
    isin_keyed: ClassVar[bool] = True
    brands: ClassVar[tuple[str, ...]] = ("l&g", "legal & general")

    def url(self, fund_ref: str) -> str:
        if not ISIN.fullmatch(fund_ref):
            raise SourceError("fund_ref")
        return URL

    def _document(self) -> list[dict[str, Any]]:
        """The document, asked once a run (a failure too, so every fund fails alike)."""
        if "document" not in self.memo:
            try:
                body = get(self.fetcher, URL, max_bytes=self.max_bytes)
                self.memo["document"] = share_classes(body)
            except SourceError as exc:
                self.memo["document"] = exc
            except Exception:  # noqa: BLE001 - any reader failure is "the file changed"
                self.memo["document"] = SourceError("parse")
        found = self.memo["document"]
        if isinstance(found, SourceError):
            raise found
        return found

    def load(self, fund_ref: str, since: date | None, isin: str | None = None) -> NavFile:
        self.url(fund_ref)
        rows = [r for r in self._document() if r.get("shareclassISIN") == fund_ref]
        if not rows:
            raise SourceError("not_listed")
        read = set()
        for row in rows:
            currency, value = row.get("shareclassCurrency"), _decimal(row.get("midPrice"))
            try:
                on = date.fromisoformat(str(row.get("midPrice__date")))
            except ValueError:
                raise SourceError("parse") from None
            if not isinstance(currency, str) or len(currency) != 3 or value is None:
                raise SourceError("parse")
            read.add((on, value, currency))
        if len(read) != 1:
            raise SourceError("parse")  # one ISIN, two share classes, two prices
        [(on, value, currency)] = read
        point = nav(on, value, currency)
        if point is None:
            raise SourceError("parse")
        return NavFile(isin=fund_ref, currency=currency, points=[point])
