"""Wage-replacement benefits booked on the accounts but not typed as Lohnersatzleistung (phase 6
D159 amended (8), 15 Nov 2026; phase 4b D102).

Moved into rules/tax/booked_inputs.py (phase 10 §7.8 (a), D293), which generalises it to every
household code that proposes a return input; the benefit rule, its to-do and its fixtures are
unchanged. These names stay importable from here.
"""

from hausbuch.rules.tax.booked_inputs import (  # noqa: F401
    _KINDS,
    benefit_kind,
    booked_benefit_warnings,
    is_short_time_allowance,
    kind_of_text,
)
