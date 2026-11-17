"""Savings plans detected from the transfers (docs/phases/phase-9.md §6.2, D245). Fixture:
tests/fixtures/phase9/savings_plans.yaml."""

from hausbuch.schemas.plan import SavingsPlan


def detect(*args: object, **kwargs: object) -> list[SavingsPlan]:
    """A destination paid in each of the last three closed months; monthly = the median."""
    raise NotImplementedError("phase 9 step 2")


def apply_overrides(*args: object, **kwargs: object) -> list[SavingsPlan]:
    """The household's typed amounts and dropped plans over the detected ones."""
    raise NotImplementedError("phase 9 step 2")
