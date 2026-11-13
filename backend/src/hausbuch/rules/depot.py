"""The depot's pure rules (docs/phases/phase-6.md D165, §7; fixtures
tests/fixtures/phase6/depot_lines.yaml and holdings_reconcile.yaml). Built in phase 6 step 4.

- `categorise(booking_text)`: a cash line of the depot's statement to its category by its
  booking text, without a model (D165 (1)). BOOKING_PATTERNS is the rule data, first match
  wins; anything else is uncategorized and goes to review.
- `reconcile_holdings(as_of, declared, movements, report_references)`: a report's declared
  units per asset against Σ confirmed movements up to its date, to 6 decimals with a
  tolerance of 0.000001 (§7). Units moved in or out between own depots (an Einbuchung or
  Ausbuchung) are movements like a trade's; an asset the movements still hold but the report
  does not list is a position of 0 by omission (step 4, §14.1).
"""

import re
from collections.abc import Hashable, Mapping, Sequence
from datetime import date
from decimal import Decimal

from pydantic import Field

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import DocumentSubtype, DocumentType
from hausbuch.schemas.household import (
    MovementKind,
    MovementStatus,
    NonNegativeQuantity,
    Quantity,
)

# D165 (1), in order: Einlage/Auszahlung before the generic fee, Vorabpauschale and the taxes
# before the distribution words (a "Steuer auf Ausschüttung" line is the tax). Matched
# case-insensitively at a word start of the booking text, which is the line's headline only
# (step 4, §14.1): a sale's detail lines name its withheld tax („KEST: …“), and a sale must not
# become a tax line by them. The real statement's headlines are „Wertpapier Kauf, Ref.: …“,
# „Wertpapier Verkauf, Ref.: …“ and „Vermögensverwaltungshonorar <Konto>, <Zeitraum>“; the
# Verrechnungskonto is credited its interest quarterly (Zinsen, hh_capital_income, D159).
BOOKING_PATTERNS: tuple[tuple[re.Pattern[str], C], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), category)
    for pattern, category in (
        (r"\bEinlage\b", C.INTERNAL_TRANSFER_IN),
        (r"\bAuszahlung\b", C.INTERNAL_TRANSFER_OUT),
        (r"\bVorabpauschale\b", C.CAPITAL_INCOME_TAX),
        (
            r"\b(Steuer|KapSt|KESt|Kapitalertragsteuer|Soli|Solidaritätszuschlag)",
            C.CAPITAL_INCOME_TAX,
        ),
        (r"\bKauf\b", C.SECURITY_PURCHASE),
        (r"\bVerkauf\b", C.SECURITY_SALE),
        (r"\b(Dividende|Ausschüttung|Erträgnis|Ertrag)", C.DISTRIBUTION),
        (r"\b(Verwaltergebühr|Vermögensverwaltungshonorar|Honorar|Gebühr)", C.DEPOT_FEE),
        (r"\b(Zinsen|Habenzinsen|Zinsgutschrift)\b", C.HH_CAPITAL_INCOME),
    )
)
UNITS_TOLERANCE = Decimal("0.000001")
REASONING = "Buchung des Depotkontos, nach ihrem Buchungstext zugeordnet."


def categorise(booking_text: str) -> C:
    """The category of a depot cash line by its headline; uncategorized (to review) when no
    pattern matches."""
    for pattern, category in BOOKING_PATTERNS:
        if pattern.search(booking_text):
            return category
    return C.UNCATEGORIZED


class UnitsMovement(Schema):
    """What the reconciliation reads of one asset_movement row of the account."""

    asset: Hashable
    quantity: Quantity
    occurred_on: date
    status: MovementStatus
    external_ref: str | None = None


class PositionCheck(Schema):
    """One position of a report against the movements: what the holding_statement row
    stores (reconciled, difference_units) and what the card shows."""

    asset: Hashable
    listed: bool  # False: held by the movements, absent from the report (0 by omission)
    declared_units: NonNegativeQuantity
    held_units: Quantity
    difference_units: Quantity
    reconciled: bool
    # The report's Umsätze lines of this asset whose reference no movement carries: the
    # confirmations still to upload. Empty on a reconciled position.
    missing_references: tuple[str, ...] = Field(default=())

    @property
    def price_stored(self) -> bool:
        """A reconciled position with units stores its price on the report's date."""
        return self.reconciled and self.declared_units > 0


def reconcile_holdings(
    as_of: date,
    declared: Mapping[Hashable, Decimal],
    movements: Sequence[UnitsMovement],
    report_references: Mapping[Hashable, Sequence[str]] | None = None,
) -> list[PositionCheck]:
    """The report's positions in its order, then every asset the confirmed movements still
    hold on as_of that the report does not list. A proposed movement does not count towards
    the units; its reference counts as present (it waits for its own „Übernehmen“)."""
    present = {m.external_ref for m in movements if m.external_ref}
    held: dict[Hashable, Decimal] = {}
    for m in movements:
        held.setdefault(m.asset, Decimal(0))
        if m.status is MovementStatus.CONFIRMED and m.occurred_on <= as_of:
            held[m.asset] += m.quantity
    rows = [(asset, units, True) for asset, units in declared.items()]
    rows += [
        (asset, Decimal(0), False)
        for asset, units in held.items()
        if asset not in declared and units != 0
    ]
    checks = []
    for asset, units, listed in rows:
        units_held = held.get(asset, Decimal(0))
        difference = units - units_held
        reconciled = abs(difference) <= UNITS_TOLERANCE
        missing = (
            ()
            if reconciled
            else tuple(
                ref for ref in (report_references or {}).get(asset, ()) if ref not in present
            )
        )
        checks.append(
            PositionCheck(
                asset=asset,
                listed=listed,
                declared_units=units,
                held_units=units_held,
                difference_units=difference,
                reconciled=reconciled,
                missing_references=missing,
            )
        )
    return checks


def reconciled_through(as_of: date, checks: Sequence[PositionCheck]) -> date | None:
    """The report's date when every position reconciles, else None: nothing after the last
    reconciled report is valued for the account (D166's rule for the exchange)."""
    return as_of if all(c.reconciled for c in checks) else None


# --- The depot's documents, typed by code (step 4, §14.1) -------------------------------

# quirion's documents are digital PDFs whose text layer names their kind. Code types them
# before any model call, so the ai_private_accounts switch (D168) is checked before the model
# sees one; the type each subtype is filed under is the catalogue's (D131). Checked in order:
# a transfer's Hinweistext and a report's trade list can name „Kauf“ or „Wertpapier“ too.
DOCUMENT_MARKERS: tuple[tuple[re.Pattern[str], DocumentSubtype], ...] = (
    (re.compile(r"(?:Ein|Aus)buchung von Wertpapieren"), DocumentSubtype.SECURITIES_TRANSFER),
    (re.compile(r"Wertpapierumsätze|Detailreport"), DocumentSubtype.DEPOT_REPORT),
    (re.compile(r"Erträgnisabrechnung"), DocumentSubtype.DISTRIBUTION_NOTE),
    (re.compile(r"Wertpapierabrechnung"), DocumentSubtype.TRADE_CONFIRMATION),
)
DOCUMENT_TYPES = {
    DocumentSubtype.SECURITIES_TRANSFER: DocumentType.LETTER,
    DocumentSubtype.DEPOT_REPORT: DocumentType.OTHER,
    DocumentSubtype.DISTRIBUTION_NOTE: DocumentType.LETTER,
    DocumentSubtype.TRADE_CONFIRMATION: DocumentType.LETTER,
}


def document_subtype(text: str) -> DocumentSubtype | None:
    """The depot subtype a document's text layer names, or None (then the classifier types
    it). A cash statement is not one of these: it is read by code on its account
    (services.bank.depot_pdf), never extracted."""
    return next((subtype for marker, subtype in DOCUMENT_MARKERS if marker.search(text)), None)


# Any other document of a depot names the depot (a Depotauszug, a tax certificate, a cost
# statement): the printed depot number's label, value before label as quirion prints it, or
# the depot bank's own name, which every quirion document prints in its letterhead or footer
# (a quarterly report, a cost statement and a tax certificate name no depot number).
_DEPOT_HINT = re.compile(
    r"Depot-?Nr|Depotnummer|Depotauszug|Depotinhaber|Wertpapierdepot|[0-9]{6,}[ \t]*Depot\b"
    r"|\b[Qq]uirion\b|Quirin\s+Privatbank"
)


def is_depot_document(text: str) -> bool:
    """A document of the household's depot by its text layer (D168): one of the four the
    depot readers read (document_subtype), or any other that names the depot. Such a
    document is never sent to the model while ai_private_accounts is off."""
    return document_subtype(text) is not None or _DEPOT_HINT.search(text) is not None


# --- What code proposes from a depot reading (D165 (2), (3), step 4) --------------------

# The fixture quirion_documents.yaml's header is the contract: one movement per trade
# confirmation, one per distribution note, one per position of a securities transfer, and the
# report's positions and its Umsätze list.
_CENT = Decimal("0.01")
# A distribution's euro taxes are converted line by line by the bank: the euro net may differ
# from the euro gross − taxes by a cent per tax line (Kapitalertragsteuer, Soli, Kirchensteuer).
_EURO_LINES_TOLERANCE = _CENT * 3

# Warning codes a proposal card shows when a reading's own figures do not add up; the movement
# is still proposed (the user checks it against the document), never booked by itself.
NET_MISMATCH = "depot.net_mismatch"
GROSS_MISMATCH = "depot.gross_mismatch"
POSITION_VALUE_MISMATCH = "depot.position_value_mismatch"
ROW_UNREADABLE = "depot.row_unreadable"


class ProposedMovement(Schema):
    kind: MovementKind
    isin: str
    name: str | None = None
    quantity: Quantity
    eur_amount: Decimal | None = None
    fee_eur: Decimal | None = None
    tax_withheld_eur: Decimal | None = None
    occurred_on: date
    external_ref: str | None = None


class ProposedHolding(Schema):
    isin: str
    name: str | None = None
    as_of: date
    declared_units: NonNegativeQuantity
    declared_value_eur: Decimal


class ProposedTrade(Schema):
    isin: str
    trade_date: date
    units: Quantity
    amount: Decimal
    reference: str


class DepotProposal(Schema):
    movements: list[ProposedMovement] = Field(default_factory=list)
    holdings: list[ProposedHolding] = Field(default_factory=list)
    trades: list[ProposedTrade] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _german(text: str) -> Decimal:
    return Decimal(text.strip().replace(".", "").replace(",", "."))


def _german_date(text: str) -> date:
    day, month, year = text.strip().split(".")
    return date(int(year), int(month), int(day))


def _dec(values: Mapping[str, object], name: str) -> Decimal | None:
    value = values.get(name)
    return None if value is None else Decimal(str(value))


def _day(values: Mapping[str, object], name: str) -> date | None:
    value = values.get(name)
    return None if value is None else date.fromisoformat(str(value))


def _text(values: Mapping[str, object], name: str) -> str | None:
    value = values.get(name)
    return None if value is None else str(value)


def _rows(values: Mapping[str, object], name: str, width: int) -> tuple[list[list[str]], bool]:
    """A text_list's rows split at „ | “; False when a row has another width (unreadable)."""
    rows, ok = [], True
    items = values.get(name) or []
    for item in items if isinstance(items, list) else []:
        parts = [part.strip() for part in str(item).split(" | ")]
        if len(parts) == width:
            rows.append(parts)
        else:
            ok = False
    return rows, ok


def propose(kind: str, values: Mapping[str, object]) -> DepotProposal | None:
    """The movements, holdings and trades one reading proposes (`values` by field name, as
    the parse gives them: ISO dates, decimals as strings, lists of „ | “ rows). None when the
    reading lacks what a proposal needs (the ISIN, the date, the units, the side); the card
    then asks for them like any missing field."""
    readers = {
        "trade_confirmation": _trade,
        "distribution_note": _distribution,
        "depot_report": _report,
        "securities_transfer": _transfer,
    }
    reader = readers.get(str(kind))
    return None if reader is None else reader(values)


def _trade(values: Mapping[str, object]) -> DepotProposal | None:
    isin, side = _text(values, "isin"), _text(values, "side")
    on, units = _day(values, "trade_date"), _dec(values, "units")
    if not isin or side not in ("Kauf", "Verkauf") or on is None or units is None:
        return None
    gross, fees, taxes, net = (
        _dec(values, f) for f in ("gross_amount", "fees", "taxes", "net_amount")
    )
    buy = side == "Kauf"
    warnings = []
    stated = gross is not None and fees is not None and taxes is not None and net is not None
    if stated and net != (gross + fees + taxes if buy else gross - fees - taxes):
        warnings.append(NET_MISMATCH)
    movement = ProposedMovement(
        kind=MovementKind.ACQUISITION if buy else MovementKind.DISPOSAL,
        isin=isin,
        name=_text(values, "security_name"),
        quantity=units if buy else -units,
        eur_amount=None if net is None else (-net if buy else net),
        fee_eur=fees,
        tax_withheld_eur=taxes,
        occurred_on=on,
        external_ref=_text(values, "order_reference"),
    )
    return DepotProposal(movements=[movement], warnings=warnings)


def _distribution(values: Mapping[str, object]) -> DepotProposal | None:
    isin, on = _text(values, "isin"), _day(values, "payment_date")
    gross_eur, net_eur = _dec(values, "gross_amount_eur"), _dec(values, "net_amount_eur")
    if not isin or on is None or gross_eur is None:
        return None
    warnings = set()
    gross, held, german, net = (
        _dec(values, f) for f in ("gross_amount", "withholding_tax", "german_taxes", "net_amount")
    )
    stated = gross is not None and held is not None and german is not None and net is not None
    if stated and net != gross - held - german:
        warnings.add(NET_MISMATCH)
    units, per_unit = _dec(values, "units"), _dec(values, "gross_per_unit")
    if (
        units is not None
        and per_unit is not None
        and gross is not None
        and (units * per_unit).quantize(_CENT) != gross
    ):
        warnings.add(GROSS_MISMATCH)
    taxes_eur = _dec(values, "german_taxes_eur")
    if (
        taxes_eur is not None
        and net_eur is not None
        and abs(gross_eur - taxes_eur - net_eur) > _EURO_LINES_TOLERANCE
    ):
        warnings.add(NET_MISMATCH)
    movement = ProposedMovement(
        kind=MovementKind.DISTRIBUTION,
        isin=isin,
        quantity=Decimal(0),
        eur_amount=gross_eur,
        # The movement's net (gross − tax) is the cash line's amount to the cent.
        tax_withheld_eur=None if net_eur is None else gross_eur - net_eur,
        occurred_on=on,
    )
    return DepotProposal(movements=[movement], warnings=sorted(warnings))


def _report(values: Mapping[str, object]) -> DepotProposal | None:
    as_of = _day(values, "report_date")
    if as_of is None:
        return None
    positions, positions_ok = _rows(values, "positions", 6)
    trade_rows, trades_ok = _rows(values, "trades", 5)
    warnings = set() if positions_ok and trades_ok else {ROW_UNREADABLE}
    holdings = []
    for isin, name, units, price, value, _currency in positions:
        try:
            units_d, price_d, value_d = _german(units), _german(price), _german(value)
        except ArithmeticError:
            warnings.add(ROW_UNREADABLE)
            continue
        if (units_d * price_d).quantize(_CENT) != value_d:
            warnings.add(POSITION_VALUE_MISMATCH)
        holdings.append(
            ProposedHolding(
                isin=isin,
                name=name,
                as_of=as_of,
                declared_units=units_d,
                declared_value_eur=value_d,
            )
        )
    trades = []
    for isin, day, units, amount, reference in trade_rows:
        try:
            trades.append(
                ProposedTrade(
                    isin=isin,
                    trade_date=_german_date(day),
                    units=_german(units),
                    amount=_german(amount),
                    reference=reference,
                )
            )
        except (ArithmeticError, ValueError):
            warnings.add(ROW_UNREADABLE)
    return DepotProposal(holdings=holdings, trades=trades, warnings=sorted(warnings))


def _transfer(values: Mapping[str, object]) -> DepotProposal | None:
    direction, on = _text(values, "direction"), _day(values, "transfer_date")
    if direction not in ("Einbuchung", "Ausbuchung") or on is None:
        return None
    inward = direction == "Einbuchung"
    positions, ok = _rows(values, "positions", 3)
    warnings = set() if ok else {ROW_UNREADABLE}
    movements = []
    for isin, name, units in positions:
        try:
            quantity = _german(units)
        except ArithmeticError:
            warnings.add(ROW_UNREADABLE)
            continue
        movements.append(
            ProposedMovement(
                kind=MovementKind.INTERNAL_TRANSFER_IN
                if inward
                else MovementKind.INTERNAL_TRANSFER_OUT,
                isin=isin,
                name=name,
                quantity=quantity if inward else -quantity,
                occurred_on=on,
                external_ref=_text(values, "reference"),
            )
        )
    return DepotProposal(movements=movements, warnings=sorted(warnings))
