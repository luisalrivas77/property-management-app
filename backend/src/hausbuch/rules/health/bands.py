"""The marks on the key figures (docs/phases/phase-9.md §5.6, D250). Data: bands.yaml beside
this module; fixture: tests/fixtures/phase9/lights.yaml.

`proposals()` loads the rule data; `light` decides one figure, `lights` every figure of a
`Health`, and `bands_todos` states the to-do while no limit is confirmed. Pure: the limits are
the household's confirmed `levers.bands`, passed in; nothing is coloured by a proposal.
"""

from collections.abc import Sequence
from decimal import Decimal
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


def _confirmed(bands: Sequence[BandLimits] | None) -> bool:
    """`levers.bands` defaults to []: an empty list is as unconfirmed as None (D250 (3))."""
    return bool(bands)


def lights(health: Health, bands: Sequence[BandLimits] | None) -> dict[MarkedFigure, Light]:
    """Every figure's mark from a computed `Health` and the confirmed limits; every figure
    unmarked while none is confirmed. With several properties the Beleihungsauslauf marked
    is the highest one (the worst)."""
    limits = {b.figure: b for b in bands or []} if _confirmed(bands) else {}
    debt = health.debt
    ltvs = [] if debt is None else [p.ltv for p in debt.properties if p.ltv is not None]
    rate = health.savings_rate
    reserve = health.reserve
    values: dict[MarkedFigure, Decimal | None] = {
        MarkedFigure.RESERVE: None if reserve is None else reserve.amount,
        MarkedFigure.SAVINGS_RATE: None if rate is None else rate.rate,
        MarkedFigure.LTV: max(ltvs, default=None),
        MarkedFigure.DEBT_SERVICE_RATIO: None if debt is None else debt.debt_service_ratio,
        MarkedFigure.DEBT_RATIO: None if debt is None else debt.debt_ratio,
        MarkedFigure.DEBT_TO_INCOME: None if debt is None else debt.debt_to_income,
    }
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
