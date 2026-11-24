"""The household goal (docs/phases/phase-10.md §7.9 (d), D289, D298). Pure functions over what
services.plan computes for phase 9's projection; outside rules/health/ and rules/plan/, so
services.health.rule_version() does not change with them."""

from .goal import GoalInputs, current_month, household_goal, none_set, target_of
from .walk import WALK_YEARS, Projection, Walk, WalkMonth, last_month, walk

__all__ = [
    "WALK_YEARS",
    "GoalInputs",
    "Projection",
    "Walk",
    "WalkMonth",
    "current_month",
    "household_goal",
    "last_month",
    "none_set",
    "target_of",
    "walk",
]
