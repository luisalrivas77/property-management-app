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
from hausbuch.schemas.household import MovementStatus, NonNegativeQuantity, Quantity

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
