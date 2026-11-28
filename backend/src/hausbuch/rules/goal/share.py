"""„Euer Plan“ „Zum Teilen“ (docs/phases/phase-10.md §7.11 (h), D306): the page's cards as a
plain answer for showing to someone else, the five switches of „Was geteilt wird“ applied. A
switched-off label or card is absent from the answer, never null or empty, so it cannot reach a
screen by mistake; amounts rounded half-up (ties away from zero) to the steps of risks.yaml."""

import dataclasses
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from hausbuch.schemas.plan import GoalKind

from .risks import PlanCards, plan_rules

CENT = Decimal("0.01")


def plain(obj: Any) -> Any:
    """Dataclasses and models as dicts (a field's trailing underscore dropped: `from_` →
    `from`), enums as their values, tuples as lists; Decimals stay Decimals."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name.removesuffix("_"): plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)
        }
    if isinstance(obj, BaseModel):
        return {name: plain(getattr(obj, name)) for name in type(obj).model_fields}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Mapping):
        return {(k.value if isinstance(k, Enum) else k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(x) for x in obj]
    return obj


def round_to(value: Decimal | None, step: Decimal) -> Decimal | None:
    """Half-up to a multiple of `step`, ties away from zero, to the cent."""
    if value is None:
        return None
    return ((value / step).quantize(Decimal(1), ROUND_HALF_UP) * step).quantize(CENT)


def _apply(tree: Any, path: list[str], fn: Callable[[Any], Any]) -> None:
    """fn on the value at a dotted path; `*` is every item of a list or every value of a map."""
    if tree is None or not path:
        return
    head, rest = path[0], path[1:]
    if head == "*":
        items = list(enumerate(tree)) if isinstance(tree, list) else list(tree.items())
        for k, v in items:
            if rest:
                _apply(v, rest, fn)
            else:
                tree[k] = fn(v)
        return
    if not isinstance(tree, dict) or head not in tree:
        return
    if rest:
        _apply(tree[head], rest, fn)
    else:
        tree[head] = fn(tree[head])


def _drop(tree: dict, path: str) -> None:
    *parents, last = path.split(".")
    node: Any = tree
    for key in parents:
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(last, None)


# The amounts of each kind (reading 7): a goal's own figures by its kind
MONTHLY = (
    "heute.saved_mean",
    "plan.saving_extra",
    "plan.by_class.*",
    "plan.cut",
    "plan.wants.*.from",
    "plan.wants.*.to",
    "other_routes.more_saving",
    "other_routes.less_spending",
    "this_month.steps.*.amount",
)
ONCE = ("risks.repair.amount", "risks.repair.by_property.*", "risks.income_gap.drawn")
GOAL_FIGURES = ("goal.value_today", "plan.without_route", "plan.gap")


def share(
    cards: PlanCards,
    *,
    round_amounts: bool = True,
    accounts: bool = False,
    wants: bool = False,
    risks: bool = True,
    assumptions: bool = True,
) -> dict[str, Any]:
    """The shared view, with the switches' defaults as the spec sets them. The to-dos are the
    household's own and never shared."""
    out = plain(cards)
    _drop(out, "risks.todos")
    if not accounts:
        _drop(out, "heute.reserve_accounts")
    if not wants:
        _drop(out, "plan.wants")
    if not risks:
        out.pop("risks", None)
    if not assumptions:
        out.pop("assumptions", None)
    if round_amounts:
        steps = plan_rules().rounding
        goal_kind = "net_worth" if cards.goal.kind is GoalKind.NET_WORTH else "reserve"
        for path, kind in (
            *((p, goal_kind) for p in GOAL_FIGURES),
            ("heute.net_worth", "net_worth"),
            ("heute.reserve", "reserve"),
            ("heute.above_target", "reserve"),  # the reserve above its target: the reserve's step
            ("heute.reserve_target", "reserve"),
            *((p, "monthly") for p in MONTHLY),
            *((p, "once") for p in ONCE),
        ):
            step = steps[kind]
            _apply(out, path.split("."), lambda v, step=step: round_to(v, step))
        # „X € von Y €, noch Z €“ adds up in the shared view too: the gap from the two rounded
        # figures, never rounded on its own (§7.13 (d) 2)
        heute = out.get("heute")
        if isinstance(heute, dict) and heute.get("reserve_target") is not None:
            heute["reserve_gap"] = max(Decimal("0.00"), heute["reserve_target"] - heute["reserve"])
    return out


__all__ = ["plain", "round_to", "share"]
