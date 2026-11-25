"""The Budget on read (docs/phases/phase-10.md D272–D277, §6; step 1).

GET /household-budget?scenario_id= is rules.budget.budget.budget over the window of closed
months phase 9's key figures use (services.health.read_months, `_window`), with the targets and
the class overrides of the scenario named (any row, a withdrawn one too), else of the default
household scenario in force, else none (services.plan._scenario, the row GET /household-plan
reads; NotFoundError for an unknown scenario). `scenario_id` in the view is the row read (the
default's id when none was named), None with the defaults. It serves the window only
(Q-O177): no single month. Reads only: nothing is stored, no event.

Notwendiges and Wünsche each carry their fixed payments and the variable rest (§7.10 (c),
D300; rules.budget.fixed.with_fixed): the series GET /household/recurring lists, computed by
services.recurring.recurring_from over the same inputs. Those inputs (the private lines from
1 Jan 2026, the series answers, each account's reach) are lever-free and kept per data version
and day with the months' reads (services.plan_inputs); rules.recurring runs on every call.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules.budget.budget import budget
from hausbuch.rules.budget.fixed import with_fixed
from hausbuch.rules.budget.references import references
from hausbuch.schemas.budget import BudgetView
from hausbuch.schemas.plan import HouseholdLevers

from .health import _window, levers_one_off_codes
from .plan import _scenario
from .plan_inputs import PlanInputs
from .recurring import recurring_from, recurring_inputs


def household_budget(conn: Connection, today: date, scenario_id: UUID | None = None) -> BudgetView:
    """The Budget on `today` (Berlin) for the scenario's levers; see the module doc."""
    inputs = PlanInputs(conn, today)  # the months' reads, kept (phase 10 §7.10 (b))
    row = _scenario(conn, scenario_id, inputs)
    levers = HouseholdLevers() if row is None else row.levers
    codes = levers_one_off_codes(levers)  # phase 10 D307
    reads = inputs.ended_reads(codes)
    win = _window(reads)
    window_reads = [reads[m] for m in win.months]
    one_off_ids = reads[win.months[-1]].one_off_ids if win.months else frozenset()
    view = budget(
        [r.household for r in window_reads],
        [r.private.lines for r in window_reads],
        levers.budget_class_overrides,
        levers.budget_targets,
        as_of=today,
        scenario_id=None if row is None else row.id,
        references=references()[1],
        one_off_ids=one_off_ids,
        one_off_codes=codes,
    )
    if not view.lines:  # below three closed months: no figure to split
        return view
    found = recurring_from(inputs.part(("recurring",), lambda c: recurring_inputs(c, today)), today)
    return with_fixed(
        view,
        found.series,
        levers.budget_class_overrides,
        one_off_codes=codes,
        one_off_ids=one_off_ids,
        window_ids=frozenset(line.allocation_id for r in window_reads for line in r.private.lines),
    )
