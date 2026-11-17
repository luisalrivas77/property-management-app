"""The month function under the scenario's levers (docs/phases/phase-9.md §7.1, §7.3, D247,
D248). Fixture: tests/fixtures/phase9/plan_levers.yaml."""

from hausbuch.schemas.plan import PlanMonth


def months(*args: object, **kwargs: object) -> list[PlanMonth]:
    """flat(k), free(k) and investable(k) for each month of the horizon."""
    raise NotImplementedError("phase 9 step 2")
