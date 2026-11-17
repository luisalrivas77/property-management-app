"""The paths to the reserve target (docs/phases/phase-9.md §7.2, D246, D251). Fixture:
tests/fixtures/phase9/paths.yaml."""

from hausbuch.schemas.plan import Path


def paths(*args: object, **kwargs: object) -> list[Path]:
    """reserve_first, one pause per plan and as_is, month by month; none recommended."""
    raise NotImplementedError("phase 9 step 2")
