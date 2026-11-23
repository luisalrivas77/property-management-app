"""Finanzielle Gesundheit in one word (phase 10 §7.6, Luis 23 Nov 2026). Data: verdict.yaml
beside this module, and bands.yaml's proposals (rules/health/bands, read through its loader).

Three questions, each answered good, ok or strained against the limits used:

- „Gebt ihr weniger aus, als ihr verdient?“: the savings rate (phase 9 D235);
- „Könnt ihr eine Überraschung auffangen?“: the reserve against its target, in euros (D237,
  D238);
- „Tragt ihr euren Kredit bequem?“: the Schuldendienstquote (D239 (3)).

A side of a figure's limits the household confirmed (`levers.bands`) wins; every other side is
the standard limit (bands.yaml's proposal, or verdict.yaml where bands.yaml proposes none, the
savings rate's good limit). Unlike phase 9's marks (D250 (3)), the verdict colours before any
limit is confirmed: Luis chose published guidance as the default, changeable.

The word: „Sehr gut“ when all three are good, „Angespannt“ when one is strained, else „Gut“;
„Noch zu wenig Daten“ when a figure cannot be computed (fewer than three closed months, or no
income in the window). Without a loan the third question is good, „Kein Kredit“. Pure:
computed on read from a `Health`, nothing stored.
"""

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from importlib.resources import files
from typing import NamedTuple

import yaml

from hausbuch.rules.health.bands import light, proposals
from hausbuch.schemas.health import (
    DIRECTION,
    BandLimits,
    Direction,
    Health,
    HealthTodo,
    HealthTodoCode,
    LightColour,
    MarkedFigure,
    Verdict,
    VerdictOrigin,
    VerdictPart,
    VerdictState,
    VerdictWord,
)

FIGURES = (MarkedFigure.SAVINGS_RATE, MarkedFigure.RESERVE, MarkedFigure.DEBT_SERVICE_RATIO)

# The to-dos that say why a figure cannot be computed yet: too few closed months, and what
# keeps the next month open (phase 9 D231, D242).
_WAIT_CODES = frozenset(
    {
        HealthTodoCode.TOO_FEW_MONTHS,
        HealthTodoCode.MONTH_END_MISSING,
        HealthTodoCode.ACCOUNT_NOT_COVERED,
        HealthTodoCode.LINES_WAITING,
        HealthTodoCode.TRANSFER_OPEN,
    }
)

_STATE = {
    LightColour.GREEN: VerdictState.GOOD,
    LightColour.AMBER: VerdictState.OK,
    LightColour.RED: VerdictState.STRAINED,
}

_TEXT_KINDS = ("question", "why", "reference", "good", "ok", "strained", "missing")
_EXTRA_TEXTS = (
    "savings_rate.negative",
    "debt_service_ratio.no_loan",
    "overall.very_good",
    "overall.too_few_months",
)


class Limit(NamedTuple):
    value: Decimal | None
    origin: VerdictOrigin | None


class Data(NamedTuple):
    version: str
    good: dict[MarkedFigure, Decimal]  # verdict.yaml's own standard limits (good side)
    budget_references: tuple[str, ...]  # rules/budget/references.yaml keys its points name
    texts: dict[str, str]


@cache
def data() -> Data:
    """verdict.yaml: its version, its standard limits, the references it names and its texts,
    every text the rule says present."""
    raw = yaml.safe_load((files("hausbuch.rules.verdict") / "verdict.yaml").read_text("utf-8"))
    order = [MarkedFigure(f) for f in raw["order"]]
    if order != list(FIGURES):
        raise ValueError(f"verdict.yaml: the order must be {[f.value for f in FIGURES]}")
    good: dict[MarkedFigure, Decimal] = {}
    keys: list[str] = []
    for row in raw["defaults"]:
        figure = MarkedFigure(row["figure"])
        if figure in good:
            raise ValueError(f"verdict.yaml: {figure} twice")
        if row["good"]["origin"] != "own_lever":
            raise ValueError("verdict.yaml: a standard limit is the household's own lever")
        good[figure] = Decimal(row["good"]["value"])
        keys += [p["budget_reference"] for p in row["reference_points"] if "budget_reference" in p]
    texts = {str(k): " ".join(str(v).split()) for k, v in raw["texts"].items()}
    wanted = [f"{f.value}.{kind}" for f in FIGURES for kind in _TEXT_KINDS] + list(_EXTRA_TEXTS)
    missing = [code for code in wanted if code not in texts]
    if missing:
        raise ValueError(f"verdict.yaml: texts missing {missing}")
    return Data(raw["version"], good, tuple(keys), texts)


def defaults() -> dict[MarkedFigure, tuple[Decimal | None, Decimal | None]]:
    """The standard (good, strained) limit per figure: bands.yaml's proposals, completed by
    verdict.yaml. The reserve's good limit is its target (None here), its strained one a share
    of the target."""
    proposed = proposals()[1]
    out: dict[MarkedFigure, tuple[Decimal | None, Decimal | None]] = {}
    for figure in FIGURES:
        proposal = proposed[figure]
        green = proposal.green.value if proposal.green else data().good.get(figure)
        red = proposal.red.value if proposal.red else None
        if figure is not MarkedFigure.RESERVE and (green is None or red is None):
            raise ValueError(f"no standard limit for {figure}")
        out[figure] = (green, red)
    return out


def limits_used(figure: MarkedFigure, household: BandLimits | None) -> tuple[Limit, Limit]:
    """(good, strained) for one figure: the household's side where it confirmed one, else the
    standard limit. When a household side and a standard side would cross (a household's red
    above the standard good), the standard side moves onto the household's, so the band between
    them is empty rather than reversed."""
    green, red = defaults()[figure]
    if figure is MarkedFigure.RESERVE:
        if household is not None and household.red is not None:
            return Limit(None, None), Limit(household.red, VerdictOrigin.HOUSEHOLD)
        return Limit(None, None), Limit(red, VerdictOrigin.DEFAULT)
    good = Limit(green, VerdictOrigin.DEFAULT)
    strained = Limit(red, VerdictOrigin.DEFAULT)
    if household is not None and household.green is not None:
        good = Limit(household.green, VerdictOrigin.HOUSEHOLD)
    if household is not None and household.red is not None:
        strained = Limit(household.red, VerdictOrigin.HOUSEHOLD)
    assert good.value is not None and strained.value is not None
    higher = DIRECTION[figure] is Direction.HIGHER
    crossed = good.value < strained.value if higher else good.value > strained.value
    if crossed:
        if good.origin is VerdictOrigin.DEFAULT:
            good = Limit(strained.value, VerdictOrigin.HOUSEHOLD)
        else:
            strained = Limit(good.value, VerdictOrigin.HOUSEHOLD)
    return good, strained


def _origin(*limits: Limit) -> VerdictOrigin | None:
    origins = [limit.origin for limit in limits if limit.origin is not None]
    if not origins:
        return None
    if VerdictOrigin.HOUSEHOLD in origins:
        return VerdictOrigin.HOUSEHOLD
    return VerdictOrigin.DEFAULT


def _texts(figure: MarkedFigure) -> dict[str, str]:
    texts = data().texts
    return {
        "question": texts[f"{figure.value}.question"],
        "why": texts[f"{figure.value}.why"],
        "reference": texts[f"{figure.value}.reference"],
    }


def _headline(figure: MarkedFigure, state: VerdictState) -> str:
    return data().texts[f"{figure.value}.{state.value}"]


def _state(value: Decimal, limits: BandLimits, *, target: Decimal | None = None) -> VerdictState:
    colour = light(value, limits, limits.figure, target=target).colour
    assert colour is not None  # both sides set (the reserve's good is its target)
    return _STATE[colour]


def _savings(health: Health, household: BandLimits | None) -> VerdictPart:
    figure = MarkedFigure.SAVINGS_RATE
    good, strained = limits_used(figure, household)
    rate = None if health.savings_rate is None else health.savings_rate.rate
    common = {
        "figure": figure,
        "value": rate,
        "good": good.value,
        "strained": strained.value,
        "good_origin": good.origin,
        "strained_origin": strained.origin,
        "origin": _origin(good, strained),
        **_texts(figure),
    }
    if rate is None:
        state = VerdictState.MISSING
        return VerdictPart(state=state, headline=_headline(figure, state), **common)
    limits = BandLimits(figure=figure, green=good.value, red=strained.value)
    state = _state(rate, limits)
    headline = _headline(figure, state)
    if state is VerdictState.STRAINED and rate < 0:
        headline = data().texts["savings_rate.negative"]
    return VerdictPart(state=state, headline=headline, **common)


def _cent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _reserve(health: Health, household: BandLimits | None) -> VerdictPart:
    figure = MarkedFigure.RESERVE
    good, strained = limits_used(figure, household)
    reserve = health.reserve
    common = {
        "figure": figure,
        "strained": strained.value,
        "strained_origin": strained.origin,
        "origin": _origin(good, strained),
        **_texts(figure),
    }
    if reserve is None:
        state = VerdictState.MISSING
        return VerdictPart(state=state, headline=_headline(figure, state), **common)
    assert strained.value is not None
    limits = BandLimits(figure=figure, red=strained.value)
    state = _state(reserve.amount, limits, target=reserve.target)
    return VerdictPart(
        state=state,
        headline=_headline(figure, state),
        amount=reserve.amount,
        months=reserve.months,
        target=reserve.target,
        target_months=reserve.target_months,
        strained_amount=_cent(strained.value * reserve.target),
        **common,
    )


def _has_loan(health: Health) -> bool:
    """A loan still owed on any property, or loan payments in the window (a loan repaid within
    it). Never read from a ratio of 0: a loan with no debit in the window has one too."""
    debt = health.debt
    assert debt is not None
    owed = any(p.loan_balance > 0 for p in debt.properties)
    return owed or debt.debt_service_year != 0


def _debt(health: Health, household: BandLimits | None) -> VerdictPart:
    figure = MarkedFigure.DEBT_SERVICE_RATIO
    good, strained = limits_used(figure, household)
    debt = health.debt
    ratio = None if debt is None else debt.debt_service_ratio
    common = {
        "figure": figure,
        "value": ratio,
        "good": good.value,
        "strained": strained.value,
        "good_origin": good.origin,
        "strained_origin": strained.origin,
        "origin": _origin(good, strained),
        **_texts(figure),
    }
    if debt is not None and not _has_loan(health):
        return VerdictPart(
            state=VerdictState.GOOD,
            headline=data().texts["debt_service_ratio.no_loan"],
            no_loan=True,
            **common,
        )
    if ratio is None:
        state = VerdictState.MISSING
        return VerdictPart(state=state, headline=_headline(figure, state), **common)
    state = _state(ratio, BandLimits(figure=figure, green=good.value, red=strained.value))
    return VerdictPart(state=state, headline=_headline(figure, state), **common)


def verdict(health: Health, bands: Sequence[BandLimits] | None) -> Verdict:
    """The verdict of a computed `Health` against the household's confirmed limits (`bands`,
    the default scenario's `levers.bands`; empty or None: the standard limits throughout)."""
    household: Mapping[MarkedFigure, BandLimits] = {b.figure: b for b in bands or []}
    parts = [
        _savings(health, household.get(MarkedFigure.SAVINGS_RATE)),
        _reserve(health, household.get(MarkedFigure.RESERVE)),
        _debt(health, household.get(MarkedFigure.DEBT_SERVICE_RATIO)),
    ]
    missing = [p for p in parts if p.state is VerdictState.MISSING]
    if missing:
        todos: list[HealthTodo] = [t for t in health.todos if t.code in _WAIT_CODES]
        line = (
            data().texts["overall.too_few_months"]
            if not health.window.enough
            else missing[0].headline
        )
        return Verdict(
            word=VerdictWord.TOO_LITTLE_DATA,
            weakest=missing[0].figure,
            line=line,
            parts=parts,
            todos=todos,
        )
    for state, word in (
        (VerdictState.STRAINED, VerdictWord.STRAINED),
        (VerdictState.OK, VerdictWord.GOOD),
    ):
        weakest = next((p for p in parts if p.state is state), None)
        if weakest is not None:
            return Verdict(word=word, weakest=weakest.figure, line=weakest.headline, parts=parts)
    return Verdict(word=VerdictWord.VERY_GOOD, line=data().texts["overall.very_good"], parts=parts)
