"""Closed months and the window (docs/phases/phase-9.md §4.4, D231). Fixture:
tests/fixtures/phase9/closed_month.yaml."""

from hausbuch.schemas.health import ClosedMonth, Window


def closed(*args: object, **kwargs: object) -> ClosedMonth:
    """Whether one month is closed: every private and rental account covered for it, no line
    of it waiting, no transfer leg of it open; the reasons and their to-dos otherwise."""
    raise NotImplementedError("phase 9 step 1")


def window(*args: object, **kwargs: object) -> Window:
    """The unbroken run of closed months ending at the newest closed one, cut to 12."""
    raise NotImplementedError("phase 9 step 1")
