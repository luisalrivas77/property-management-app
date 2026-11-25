"""The marks on the key figures (docs/phases/phase-9.md §5.6, D250). Data: bands.yaml beside
this module; fixture: tests/fixtures/phase9/lights.yaml.

`proposals()` loads the rule data; `light` decides one figure, `lights` every figure of a
`Health`, and `bands_todos` states the to-do while no limit is confirmed. Pure: the limits are
the household's confirmed `levers.bands`, passed in; nothing is coloured by a proposal.

`with_texts` (phase 9 step 3) renders each mark's sentences into German for the screen, so the
frontend never writes a figure into a sentence (principle 1): money in whole euros, half-up,
with the thousands point ("9.990 €"); a ratio as a percent with one decimal ("73,9 %"); the
reserve's months with one decimal ("3,8 Monate"); the debt-to-income multiple with one
decimal ("3,2"). A negative value keeps "-" before the number, as explain_render.yaml fixes
for step 4's renderer. An unknown slot raises rather than print it.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from importlib.resources import files

import yaml

from hausbuch.schemas.health import (
    DIRECTION,
    BandLimits,
    BandProposal,
    Direction,
    Health,
    HealthTodo,
    HealthTodoCode,
    Light,
    LightColour,
    LimitOrigin,
    MarkedFigure,
)


@cache
def proposals() -> tuple[str, dict[MarkedFigure, BandProposal], dict[str, str]]:
    """bands.yaml: its version, the proposal per figure and the sentences by code."""
    data = yaml.safe_load((files("hausbuch.rules.health") / "bands.yaml").read_text("utf-8"))
    by_figure = {}
    for row in data["figures"]:
        proposal = BandProposal.model_validate(row)
        if proposal.figure in by_figure:
            raise ValueError(f"bands.yaml: {proposal.figure} twice")
        by_figure[proposal.figure] = proposal
    sentences = dict(data["sentences"])
    for proposal in by_figure.values():
        for codes in proposal.sentences.values():
            missing = [c for c in codes if c not in sentences]
            if missing:
                raise ValueError(f"bands.yaml: unknown sentence codes {missing}")
    return data["version"], by_figure, sentences


def _colour(
    direction: Direction, value: Decimal, green: Decimal | None, red: Decimal | None
) -> LightColour | None:
    """On a limit the better colour; a side without a limit gives no colour."""
    if direction is Direction.HIGHER:
        if green is not None and value >= green:
            return LightColour.GREEN
        if red is not None and value < red:
            return LightColour.RED
    else:
        if green is not None and value <= green:
            return LightColour.GREEN
        if red is not None and value > red:
            return LightColour.RED
    if green is not None and red is not None:
        return LightColour.AMBER
    return None


def light(
    value: Decimal | None,
    limits: BandLimits | None,
    figure: MarkedFigure,
    *,
    target: Decimal | None = None,
    regular: Decimal | None = None,
) -> Light:
    """One figure's mark against its confirmed limits (§5.6). The direction is the figure's
    (DIRECTION). `value` at its stored precision (a ratio to four places; for the reserve the
    amount in euros). The reserve is compared with its `target` in euros (green at or above)
    and with `limits.red × target` (red below), unrounded. `regular`: the savings rate without
    one-offs; the sentence `savings_rate.red_regular` is said only when it is not below 0.
    A red savings rate that is not below 0 (a member's red limit above 0) says
    `savings_rate.red_limit` alone: „mehr ausgegeben als eingenommen“ would be false.
    No mark when the value, the limits or (for the reserve) the target is missing."""
    if value is None or limits is None:
        return Light(figure=figure)
    if figure is MarkedFigure.RESERVE:
        if target is None:
            return Light(figure=figure)
        red = None if limits.red is None else limits.red * target
        found = _colour(Direction.HIGHER, value, target, red)
    else:
        found = _colour(DIRECTION[figure], value, limits.green, limits.red)
    sentences: list[str] = []
    if found is not None:
        sentences = list(proposals()[1][figure].sentences.get(found, []))
    if figure is MarkedFigure.SAVINGS_RATE and found is LightColour.RED and value >= 0:
        sentences = ["savings_rate.red_limit"]
    if figure is MarkedFigure.SAVINGS_RATE and (regular is None or regular < 0):
        sentences = [code for code in sentences if code != "savings_rate.red_regular"]
    return Light(
        figure=figure,
        colour=found,
        green=limits.green,
        red=limits.red,
        origin=LimitOrigin.OWN_LEVER,  # a confirmed limit is the household's own (D250 (2))
        sentences=sentences,
    )


def _values(health: Health) -> dict[MarkedFigure, Decimal | None]:
    """Each marked figure's value in a `Health`; with several properties the highest LTV."""
    debt = health.debt
    ltvs = [] if debt is None else [p.ltv for p in debt.properties if p.ltv is not None]
    rate = health.savings_rate
    reserve = health.reserve
    return {
        MarkedFigure.RESERVE: None if reserve is None else reserve.amount,
        MarkedFigure.SAVINGS_RATE: None if rate is None else rate.rate,
        MarkedFigure.LTV: max(ltvs, default=None),
        MarkedFigure.DEBT_SERVICE_RATIO: None if debt is None else debt.debt_service_ratio,
        MarkedFigure.DEBT_RATIO: None if debt is None else debt.debt_ratio,
        MarkedFigure.DEBT_TO_INCOME: None if debt is None else debt.debt_to_income,
    }


def _confirmed(bands: Sequence[BandLimits] | None) -> bool:
    """`levers.bands` defaults to []: an empty list is as unconfirmed as None (D250 (3))."""
    return bool(bands)


def lights(health: Health, bands: Sequence[BandLimits] | None) -> dict[MarkedFigure, Light]:
    """Every figure's mark from a computed `Health` and the confirmed limits; every figure
    unmarked while none is confirmed. With several properties the Beleihungsauslauf marked
    is the highest one (the worst)."""
    limits = {b.figure: b for b in bands or []} if _confirmed(bands) else {}
    rate = health.savings_rate
    reserve = health.reserve
    values = _values(health)
    return {
        figure: light(
            value,
            limits.get(figure),
            figure,
            target=None if reserve is None else reserve.target,
            regular=None if rate is None else rate.rate_regular,
        )
        for figure, value in values.items()
    }


def bands_todos(bands: Sequence[BandLimits] | None) -> list[HealthTodo]:
    """D250 (3): while no limit is confirmed, the to-do that opens the card."""
    if _confirmed(bands):
        return []
    return [
        HealthTodo(
            code=HealthTodoCode.BANDS_UNCONFIRMED,
            text=(
                "Die Kennzahlen bekommen erst eine Ampel, wenn ihr eure Grenzen festgelegt "
                "habt; die App schlägt Werte vor, die ihr übernehmen oder ändern könnt."
            ),
            action="Ampel-Grenzen festlegen",
            href="/",  # Übersicht: Kennzahlen and the card „Ampel-Grenzen festlegen“
        )
    ]


# --- The sentences in German (phase 9 step 3) ---------------------------------------------

_SLOT = re.compile(r"\{f:([a-z_]+)\}")
_ONE = Decimal("1")
_TENTH = Decimal("0.1")


def _group(digits: str) -> str:
    return re.sub(r"\B(?=(\d{3})+(?!\d))", ".", digits)


def _number(value: Decimal, places: Decimal) -> str:
    rounded = value.quantize(places, rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    whole, _, fraction = f"{abs(rounded):f}".partition(".")
    return f"{sign}{_group(whole)}{',' + fraction if fraction else ''}"


def format_money(value: Decimal) -> str:
    """Whole euros, half-up, the thousands point: 9990.00 → "9.990 €"."""
    return f"{_number(value, _ONE)} €"


def format_percent(value: Decimal) -> str:
    """A ratio as a percent with one decimal: 0.7388 → "73,9 %", 0 → "0,0 %"."""
    return f"{_number(value * 100, _TENTH)} %"


def format_months(value: Decimal) -> str:
    """Months with one decimal, always "Monate": 3.8 → "3,8 Monate"."""
    return f"{_number(value, _TENTH)} Monate"


def format_multiple(value: Decimal) -> str:
    """A multiple with one decimal: 3.20 → "3,2"."""
    return _number(value, _TENTH)


_BAND_SLOT = {
    MarkedFigure.SAVINGS_RATE: "band_savings_rate",
    MarkedFigure.LTV: "band_ltv",
    MarkedFigure.DEBT_SERVICE_RATIO: "band_debt_service",
    MarkedFigure.DEBT_RATIO: "band_debt_ratio",
    MarkedFigure.DEBT_TO_INCOME: "band_debt_to_income",
}


def _slots(health: Health, mark: Light, value: Decimal) -> dict[str, str]:
    """The `{f:}` slots one figure's sentences may hold, formatted."""
    figure = mark.figure
    if figure is MarkedFigure.RESERVE:
        reserve = health.reserve
        assert reserve is not None
        out = {
            "reserve": format_money(reserve.amount),
            "reserve_target": format_money(reserve.target),
            "reserve_gap": format_money(reserve.gap),
        }
        # No months without a need (`months` is None when the need is 0). Then the target is
        # 0 too, so the mark is green at 0 € or more and red below: `reserve.amber`, the one
        # sentence with this slot, cannot be reached.
        if reserve.months is not None:
            out["reserve_months"] = format_months(reserve.months)
        if mark.red is not None:
            out["band_reserve_red"] = format_money(mark.red * reserve.target)
        return out
    fmt: Callable[[Decimal], str] = (
        format_multiple if figure is MarkedFigure.DEBT_TO_INCOME else format_percent
    )
    out = {figure.value: fmt(value)}
    rate = health.savings_rate
    # None since phase 10 D308 (the rate leaves the one-offs out); a stored row keeps it
    if figure is MarkedFigure.SAVINGS_RATE and rate is not None and rate.rate_regular is not None:
        out["savings_rate_regular"] = fmt(rate.rate_regular)
    band = _BAND_SLOT[figure]
    if mark.green is not None:
        out[f"{band}_green"] = fmt(mark.green)
    if mark.red is not None:
        out[f"{band}_red"] = fmt(mark.red)
    return out


def render(text: str, slots: Mapping[str, str]) -> str:
    """Replace every `{f:key}` by its formatted figure; an unknown slot raises."""

    def one(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in slots:
            raise ValueError(f"bands.yaml: no figure for the slot {{f:{key}}}")
        return slots[key]

    out = _SLOT.sub(one, text)
    if "{" in out:
        raise ValueError(f"bands.yaml: a slot that is not a figure in {text!r}")
    return out


def with_texts(health: Health, marks: Mapping[MarkedFigure, Light]) -> dict[MarkedFigure, Light]:
    """Each mark with its German `texts`: a marked figure's sentences in order; an unmarked
    figure with a value its intro sentence alone (the reserve and the savings rate have
    none, so they say nothing without a colour); a figure without a value nothing."""
    sentences = proposals()[2]
    values = _values(health)
    out = {}
    for figure, mark in marks.items():
        value = values[figure]
        codes = list(mark.sentences)
        if mark.colour is None:
            intro = f"{figure.value}.intro"
            codes = [intro] if value is not None and intro in sentences else []
        if value is None or not codes:
            out[figure] = mark.model_copy(update={"texts": []})
            continue
        slots = _slots(health, mark, value)
        out[figure] = mark.model_copy(
            update={"texts": [render(sentences[code], slots) for code in codes]}
        )
    return out
