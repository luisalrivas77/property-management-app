"""The depot's pure rules (docs/phases/phase-6.md D165, §7; fixtures
tests/fixtures/phase6/depot_lines.yaml and holdings_reconcile.yaml). Built in phase 6 step 4.

- `categorise(booking_text)`: a cash line of the depot's statement to its category by its
  booking text, without a model (D165 (1)). BOOKING_PATTERNS is the rule data, first match
  wins; anything else is uncategorized and goes to review.
- `reconcile_holdings(declared, movements)`: a report's declared units per asset against
  Σ confirmed movements up to its date, to 6 decimals with a tolerance of 0.000001 (§7).
"""

import re
from collections.abc import Sequence
from decimal import Decimal

from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.household import AssetMovement, HoldingStatement

STEP = "phase 6 step 4 (the depot)"

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
    raise NotImplementedError(STEP)


def reconcile_holdings(
    declared: Sequence[HoldingStatement], movements: Sequence[AssetMovement]
) -> list[HoldingStatement]:
    raise NotImplementedError(STEP)
