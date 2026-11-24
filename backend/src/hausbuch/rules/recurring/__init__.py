"""Recurring payments and single payments that may be one-off (docs/phases/phase-10.md §7.9
(b), (c); D296, D297). Pure functions over the confirmed private lines; outside rules/health/
and rules/plan/, so services.health.rule_version() does not change with them."""

from .one_off import Candidate, candidates, code_medians, newest_allocation_marks, one_off_ids
from .series import SeriesMark, SpendingLine, find, recurring

__all__ = [
    "Candidate",
    "SeriesMark",
    "SpendingLine",
    "candidates",
    "code_medians",
    "find",
    "newest_allocation_marks",
    "one_off_ids",
    "recurring",
]
