"""The issuers' published NAV, one adapter per issuer (docs/phases/phase-6.md D174).

Each adapter reads one issuer's NAV history file for a fund reference from the registry
(rules/household/assets.yaml) and answers a NavFile: the ISIN and NAV currency the file
states, and its NAV per valuation day. The fetch job compares the file's ISIN with the
registry's before storing anything, so a moved or renamed file never prices the wrong fund.

Adapters written (each from the issuer's real file, read 13 Nov 2026; the URLs are in each
module and in assets.yaml): `spdr` (State Street's XLSX), `ishares` (BlackRock's
SpreadsheetML download). D174 amended 19 Nov 2026 adds, each read live that day (own use
only, Luis): `amundi` (a POST of the fund's ISIN, the history as JSON), `xtrackers` (DWS's
history download by ISIN), `invesco` (the share class's JSON and its history workbook),
`bnp` (BNP Paribas AM's NAV service by currency and ISIN), `landg` (L&G's fund centre, the
latest NAV only), `ark` (ARK Invest Europe's fund page and its chart series) and `hanetf`
(the fund page found by name in the sitemap, then its NAV history workbook). A fund of any
other issuer stays on the depot's reports.

A held fund the registry does not list is found by its ISIN in its issuer's own catalogue
(D174 amended 16 Nov 2026): an adapter with a `catalogue_url` maps every ISIN it lists to its
fund reference, asked only for a fund whose name carries the adapter's brand; an adapter
without one is asked by the fund itself (`candidates`, D174 amended 19 Nov 2026). The
reference found is stored with the price (price.raw) and the file's own ISIN still decides.
"""

from .amundi import AmundiAdapter
from .ark import ArkAdapter
from .base import IssuerAdapter, NavFile, read_points
from .bnp import BnpAdapter
from .hanetf import HanetfAdapter
from .invesco import InvescoAdapter
from .ishares import ISharesAdapter
from .landg import LandgAdapter
from .spdr import SpdrAdapter
from .xtrackers import XtrackersAdapter

ADAPTERS: dict[str, type[IssuerAdapter]] = {
    SpdrAdapter.issuer: SpdrAdapter,
    ISharesAdapter.issuer: ISharesAdapter,
    AmundiAdapter.issuer: AmundiAdapter,
    XtrackersAdapter.issuer: XtrackersAdapter,
    InvescoAdapter.issuer: InvescoAdapter,
    BnpAdapter.issuer: BnpAdapter,
    LandgAdapter.issuer: LandgAdapter,
    ArkAdapter.issuer: ArkAdapter,
    HanetfAdapter.issuer: HanetfAdapter,
}

# The name the screens show for an issuer's NAV („NAV <Fondsgesellschaft>“, §13).
ISSUER_NAMES: dict[str, str] = {
    "spdr": "State Street SPDR",
    "ishares": "iShares",
    "amundi": "Amundi",
    "xtrackers": "Xtrackers",
    "invesco": "Invesco",
    "bnp": "BNP Paribas",
    "landg": "L&G",
    "ark": "ARK Invest",
    "hanetf": "HANetf",
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
