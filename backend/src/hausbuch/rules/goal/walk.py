"""The month walk moved to rules/plan/projection.py (docs/phases/phase-10.md §7.11 (a), D301):
one walk feeds Anlagen's 31 December points, the goal and Durchspielen. Kept as a re-export."""

from hausbuch.rules.plan.projection import (
    WALK_YEARS,
    Projection,
    Walk,
    WalkMonth,
    last_month,
    walk,
)

__all__ = ["WALK_YEARS", "Projection", "Walk", "WalkMonth", "last_month", "walk"]
