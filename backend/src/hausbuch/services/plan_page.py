"""„Euer Plan“ (docs/phases/phase-10.md §7.11 (h), D306; mockup 19i): `GET /household-goal/plan`.

The goal's own inputs (services.goal.goal_inputs, read through the same kept reads, so the page
and Haushalt › Ziel can never disagree) plus what the page shows beside them: today's reserve
with its accounts, the properties' living areas and value changes, the running month's booked
investments and whether the route's wants budget is the default scenario's. The cards are
rules/goal/risks.plan_cards; the shared view is rules/goal/share.share with the five switches.
Reads only: no row, no event.
"""

from collections.abc import Hashable, Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.rules import investment_split as X
from hausbuch.rules.goal import current_month, household_goal
from hausbuch.rules.goal.risks import (
    LIVING_AREA_MISSING,
    PlanCards,
    PlanProperty,
    PlanTodo,
    plan_cards,
    plan_rules,
)
from hausbuch.rules.goal.risks import PlanInputs as CardInputs
from hausbuch.rules.goal.share import plain, share
from hausbuch.schemas.goal import GoalWord, HouseholdGoal
from hausbuch.schemas.health import HealthTodo, HealthTodoCode, Reserve
from hausbuch.schemas.plan import AssumptionKind, HouseholdLevers
from hausbuch.schemas.plan_page import PlanPage, PlanSwitches, PlanView

from .goal import _reserve_depots, goal_inputs, reserve_today
from .health import levers_one_off_codes
from .plan import _scenario, flat_scenarios, plan_lines
from .plan_inputs import PlanInputs


def living_area_todo(todo: PlanTodo) -> HealthTodo:
    """rules/goal/risks' `plan.living_area_missing`: a property without its living area gets
    no repair in „Wenn es schlechter läuft“."""
    assert todo.code == LIVING_AREA_MISSING
    return HealthTodo(
        code=HealthTodoCode.LIVING_AREA_MISSING,
        text=(
            "Für die Wohnung ist keine Wohnfläche eingetragen; ohne sie rechnet „Wenn es "
            "schlechter läuft“ keine große Reparatur."
        ),
        action=todo.action,
        href=todo.href,
    )


def plan_page(
    conn: Connection,
    today: date,
    view: PlanView = PlanView.OWN,
    switches: PlanSwitches | None = None,
) -> PlanPage:
    switches = PlanSwitches() if switches is None else switches
    inputs = PlanInputs(conn, today)  # the goal's reads, kept per data version
    row = _scenario(conn, None, inputs)
    if row is None or row.levers.goal is None:
        return _page(view, switches, word=GoalWord.NONE)
    levers = row.levers
    reads = inputs.ended_reads(levers_one_off_codes(levers))
    built = goal_inputs(conn, today, row, levers, reads, inputs)
    if isinstance(built, HouseholdGoal):  # below three closed months
        return _page(view, switches, word=built.word, issue=built.issue, todos=built.todos)
    answer = household_goal(built.goal)
    reserve = reserve_today(conn, today, levers, reads, inputs, built.now)
    flats, _, flat_assumptions = inputs.part(
        ("flat_scenarios",), lambda c: flat_scenarios(c, today)
    )
    growth = {
        a.property_id: a.value
        for a in flat_assumptions
        if a.kind is AssumptionKind.FLAT_GROWTH and a.property_id is not None
    }
    props = _properties(conn, list(flats))
    month = current_month(today)
    lines = plan_lines(conn, [month])
    invested = sum(X.bought(lines, [month], _reserve_depots(built.parts.reserve)).values())
    cards = plan_cards(
        CardInputs(
            goal=built.goal,
            net_worth_today=built.now.total,
            reserve_today=reserve.amount,
            reserve_accounts=tuple(_account_labels(conn, reserve)),
            properties=tuple(
                PlanProperty(
                    key=key,
                    living_area=None if key not in props else props[key]["living_area"],
                    value_change=growth.get(key),
                )
                for key in flats
            ),
            invested_this_month=invested,
            budget_adopted=_budget_adopted(levers, answer),
        ),
        answer,
    )
    return page(
        cards,
        answer,
        view,
        switches,
        labels={k: v["label"] for k, v in props.items()},
        kinds={k: v["kind"] for k, v in props.items()},
        window=tuple(m.month for m in built.parts.months),
    )


def page(
    cards: PlanCards,
    answer: HouseholdGoal,
    view: PlanView,
    switches: PlanSwitches,
    *,
    labels: Mapping[Hashable, str] | None = None,
    kinds: Mapping[Hashable, str] | None = None,
    window: Sequence[str] = (),
) -> PlanPage:
    """The answer from the rule's cards (pure; tests/rules run the fixture through it). „Zum
    Teilen“ is built from share()'s dict alone, so a label it dropped is never added back;
    „Für euch“ adds the properties' labels and the to-dos. Both get what the page shows beside
    the cards: the window the mean is taken over (`window`), the repair's sources, route 3's
    windows and each property's kind (`kinds`)."""
    labels, kinds = labels or {}, kinds or {}
    if view is PlanView.SHARE:
        out = share(
            cards,
            round_amounts=switches.round_amounts,
            accounts=switches.accounts,
            wants=switches.wants,
            risks=switches.risks,
            assumptions=switches.assumptions,
        )
    else:
        out = plain(cards)
        if cards.risks is not None:
            out["risks"]["todos"] = [living_area_todo(t) for t in cards.risks.todos]
        for p in (out.get("assumptions") or {}).get("properties", []):
            p["label"] = labels.get(p["key"])
    _keys_as_text(out)
    heute = out.get("heute")
    if heute is not None and window:
        heute["window_from"], heute["window_to"] = window[0], window[-1]
    risks = out.get("risks")
    if risks is not None:
        risks["sources"] = [
            {"name": s.name, "url": s.url, "read_on": s.read_on} for s in plan_rules().sources
        ]
    other = out.get("other_routes")
    more = answer.more_return
    if other is not None and more is not None:
        other["return_windows"] = more.windows
        other["return_reached_windows"] = more.reached_windows
        other["return_verdict"] = more.verdict
    for p in (out.get("assumptions") or {}).get("properties", []):
        p["kind"] = next((k for key, k in kinds.items() if str(key) == p["key"]), None)
    return _page(
        view,
        switches,
        word=answer.word,
        issue=answer.issue,
        todos=list(answer.todos) if view is PlanView.OWN else None,
        cards=out,
    )


def _keys_as_text(out: dict[str, Any]) -> None:
    """A property's key as its id's text (the rule keys by the id itself)."""
    for p in (out.get("assumptions") or {}).get("properties", []):
        p["key"] = str(p["key"])
    repair = (out.get("risks") or {}).get("repair")
    if repair is not None:
        repair["by_property"] = {str(k): v for k, v in repair["by_property"].items()}


def _page(
    view: PlanView,
    switches: PlanSwitches,
    *,
    word: GoalWord | None,
    issue: Any = None,
    todos: list[HealthTodo] | None = None,
    cards: dict[str, Any] | None = None,
) -> PlanPage:
    data: dict[str, Any] = {"view": view, "word": word, "issue": issue, **(cards or {})}
    if view is PlanView.SHARE:
        data["switches"] = switches
    elif todos is not None:
        data["todos"] = todos
    return PlanPage.model_validate(data)


def _properties(conn: Connection, keys: list[UUID]) -> dict[UUID, dict[str, Any]]:
    if not keys:
        return {}
    p = property_table.c
    rows = conn.execute(select(p.id, p.label, p.kind, p.living_area_m2).where(p.id.in_(keys)))
    return {r.id: {"label": r.label, "kind": r.kind, "living_area": r.living_area_m2} for r in rows}


def _account_labels(conn: Connection, reserve: Reserve) -> list[str]:
    """The reserve's counted accounts by label, with their provider („Tagesgeld (Bank A)“),
    in the reserve's order. „Für euch“, or shared with the switch `accounts` only."""
    ids = [a.account_id for a in reserve.accounts if a.counted]
    if not ids:
        return []
    acc = account_table.c
    rows = {
        r.id: r
        for r in conn.execute(
            select(acc.id, acc.label, acc.bank, acc.provider).where(acc.id.in_(ids))
        )
    }
    out = []
    for key in ids:
        r = rows.get(key)
        if r is None:
            continue
        by = (r.provider or r.bank or "").strip()
        out.append(f"{r.label} ({by})" if by and by not in r.label else r.label)
    return out


def _budget_adopted(levers: HouseholdLevers, answer: HouseholdGoal) -> bool:
    """The route's wants budget is the default scenario's: each change of the balanced route
    is in the levers with its amount („Als Budget übernehmen“; the mean stored with it and the
    months are not compared, since the route's first month moves on each month)."""
    b = answer.balanced
    if b is None or not b.spending_changes:
        return False
    have = {(c.code, c.monthly, c.percent) for c in levers.spending_changes}
    return all((c.code, c.monthly, c.percent) in have for c in b.spending_changes)


__all__ = ["living_area_todo", "page", "plan_page"]
