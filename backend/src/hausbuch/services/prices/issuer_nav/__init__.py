"""The issuers' published NAV, one adapter per issuer (docs/phases/phase-6.md D174).

Each adapter reads one issuer's NAV history file for a fund reference from the registry
(rules/household/assets.yaml) and answers a NavFile: the ISIN and NAV currency the file
states, and its NAV per valuation day. The fetch job compares the file's ISIN with the
registry's before storing anything, so a moved or renamed file never prices the wrong fund.

Adapters written (each from the issuer's real file, read 13 Nov 2026; the URLs are in each
module and in assets.yaml): `spdr` (State Street's XLSX), `ishares` (BlackRock's
SpreadsheetML download). No other issuer of quirion's universe publishes a file the app can
read (Q-O74); their funds are priced from the depot's reports.

A held fund the registry does not list is found by its ISIN in its issuer's own catalogue
(D174 amended 16 Nov 2026): an adapter with a `catalogue_url` maps every ISIN it lists to its
fund reference, asked only for a fund whose name carries the adapter's brand; the reference
found is stored with the price (price.raw) and the file's own ISIN still decides.
"""

from .amundi import AmundiAdapter
from .base import IssuerAdapter, NavFile, read_points
from .ishares import ISharesAdapter
from .spdr import SpdrAdapter

ADAPTERS: dict[str, type[IssuerAdapter]] = {
    SpdrAdapter.issuer: SpdrAdapter,
    ISharesAdapter.issuer: ISharesAdapter,
    AmundiAdapter.issuer: AmundiAdapter,
}

# The name the screens show for an issuer's NAV („NAV <Fondsgesellschaft>“, §13).
ISSUER_NAMES: dict[str, str] = {
    "spdr": "State Street SPDR",
    "ishares": "iShares",
    "amundi": "Amundi",
}


def issuer_name(issuer: str) -> str:
    return ISSUER_NAMES.get(issuer, issuer)


__all__ = [
    "ADAPTERS",
    "ISSUER_NAMES",
    "ISharesAdapter",
    "IssuerAdapter",
    "NavFile",
    "SpdrAdapter",
    "issuer_name",
    "read_points",
]
