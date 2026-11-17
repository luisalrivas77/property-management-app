"""Free cash and „Anlegbar pro Monat“ (docs/phases/phase-9.md §6.1, D244). Fixture:
tests/fixtures/phase9/surplus.yaml."""

from hausbuch.schemas.plan import Surplus


def surplus(*args: object, **kwargs: object) -> Surplus:
    """The window's means, the buffer and what can be invested per month."""
    raise NotImplementedError("phase 9 step 2")
