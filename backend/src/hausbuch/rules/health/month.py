"""One household month (docs/phases/phase-9.md §4, D230–D236). Fixture:
tests/fixtures/phase9/household_month.yaml."""

from hausbuch.schemas.health import HouseholdMonth


def household_month(*args: object, **kwargs: object) -> HouseholdMonth:
    """The private accounts' cashflow plus the rental account's cash, counted once, in the
    calendar month of each line's booking date (D230, D232, D233)."""
    raise NotImplementedError("phase 9 step 1")
