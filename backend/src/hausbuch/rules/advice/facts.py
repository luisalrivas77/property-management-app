"""The request's facts (docs/phases/phase-9.md §8.1, D252–D254). Fixture:
tests/fixtures/phase9/explain_facts.yaml.

Two layers. `assemble` takes plain inputs (the figures by key, the household's accounts, plans
and assets with the texts a person typed, the confirmed limits and their colours, the
projection) and returns the `ExplainFacts`: figures with their types, states derived by code,
kinds and classes under opaque keys k1, p1, a1 …, and the labels in a map the dump leaves out
(D253). The fixture replays this layer. `build` maps a `Health` and a `HouseholdPlan` onto it;
the accounts and the assets are not in either, so the caller passes them (step 4b's service
reads them).

Readings (docs/phases/phase-9.md §15.3, step 4a):
- keys are numbered in the order the caller lists accounts, plans and assets; a pause path is
  keyed by its plan's opaque key (`path_pause_p1_reached`), never by the plan's own key, which
  can be a slug of a payee a person typed;
- an asset is held when it has units at the as-of day and is not spam (D254);
- `basis_state` is `full` from 12 window months, `trend_state` the sign of `net_worth_change`;
  `projection_state` is always sent; a `light_<figure>` only for a figure whose limits were
  confirmed, `none` when its value could not be coloured;
- in `generic` the accounts, plans and assets are empty and no pause path is sent;
- the figures are sorted by key, so the dump and `facts_sha256` are stable.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from hausbuch.schemas.advice import (
    FIGURE_KEYS,
    PROJECTION_KEYS,
    AdviceMode,
    ExplainFacts,
    ExplainLabels,
    Fact,
    FigureType,
    StateValue,
)
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import BandLimits, Health, LightColour, MarkedFigure
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.plan import AssumptionKind, HouseholdPlan, PlanClass, SurplusKey

T = FigureType

# §8.1's closed list with its types. `debt_to_income` and its limits are a multiple, the one
# type step 4a added (schemas/advice.FigureType.MULTIPLE).
FIGURE_TYPES: dict[str, FigureType] = {
    **dict.fromkeys(
        [
            "net_worth",
            "net_worth_change",
            "saved_total",
            "principal_total",
            "reserve",
            "monthly_need",
            "reserve_target",
            "reserve_gap",
            "free_cash",
            "free_cash_median",
            "free_cash_lowest",
            "buffer",
            "investable",
            "already_invested",
            "investable_left",
            "projection_total",
            "projection_change",
            "projection_assumption_part",
        ],
        T.MONEY,
    ),
    **dict.fromkeys(
        [
            "savings_rate",
            "savings_rate_regular",
            "ltv",
            "debt_ratio",
            "debt_service_ratio",
            "share_bank",
            "share_depot",
            "share_crypto",
            "share_property",
            "assumed_change_depot",
            "assumed_change_crypto",
            "flat_value_growth",
        ],
        T.PERCENT,
    ),
    "debt_to_income": T.MULTIPLE,
    "reserve_months": T.MONTHS,
    "reserve_target_months": T.MONTHS,
    "basis_months": T.COUNT,
    "projection_until": T.MONTH,
}
assert set(FIGURE_TYPES) == FIGURE_KEYS | PROJECTION_KEYS, "every key of §8.1 has a type"

# The band keys of each marked figure (`band_<slot>_<green|red>`), as bands.py names them.
BAND_SLOT = {
    MarkedFigure.RESERVE: "band_reserve",
    MarkedFigure.SAVINGS_RATE: "band_savings_rate",
    MarkedFigure.LTV: "band_ltv",
    MarkedFigure.DEBT_SERVICE_RATIO: "band_debt_service",
    MarkedFigure.DEBT_RATIO: "band_debt_ratio",
    MarkedFigure.DEBT_TO_INCOME: "band_debt_to_income",
}
_FULL_BASIS = 12


def figure_type(key: str) -> FigureType:
    """The type of a figure key of §8.1, paths and limits included."""
    if key in FIGURE_TYPES:
        return FIGURE_TYPES[key]
    if key.startswith("path_"):
        return T.MONEY if key.endswith("_monthly") else T.MONTH
    if key == "band_reserve_red":
        return T.MONEY
    if key.startswith("band_debt_to_income_"):
        return T.MULTIPLE
    if key.startswith("band_"):
        return T.PERCENT
    raise KeyError(key)


def _value(kind: FigureType, value: Decimal | int | str) -> str:
    """A figure as the decimal string its type requires (§8.1)."""
    if kind is T.MONTH:
        return str(value)[:7]
    number = Decimal(str(value))
    if kind is T.MONEY:
        return f"{number.quantize(Decimal('0.01')):f}"
    if kind is T.MONTHS:
        return f"{number.quantize(Decimal('0.1')):f}"
    if kind is T.COUNT:
        return str(int(number))
    if kind is T.MULTIPLE:
        return f"{number.quantize(Decimal('0.01')):f}"
    places = -number.as_tuple().exponent if number.as_tuple().exponent < 0 else 0
    return f"{number.quantize(Decimal('0.0001')) if places > 4 else number:f}"


def fact(key: str, value: Decimal | int | str) -> Fact:
    kind = figure_type(key)
    return Fact(key=key, type=kind, value=_value(kind, value))


# --- The plain inputs -------------------------------------------------------------------


@dataclass(frozen=True)
class AccountIn:
    label: str
    kind: AccountKind


@dataclass(frozen=True)
class PlanIn:
    label: str  # the destination's label, or the payee for a plan to no own account
    asset_class: PlanClass
    plan_key: str | None = None  # the plan's own key, to name its pause path


@dataclass(frozen=True)
class AssetIn:
    name: str
    asset_class: NetWorthClass
    units: Decimal  # at the as-of day, on the own accounts
    spam: bool = False


@dataclass(frozen=True)
class ProjectionIn:
    until: str  # YYYY-MM
    total: Decimal
    change: Decimal
    assumption_part: Decimal
    depot: Decimal | None = None  # the typed yearly rates (D247 amended)
    crypto: Decimal | None = None
    flat_value_growth: Decimal | None = None  # one property only


def _sign_state(value: Decimal, up: StateValue, down: StateValue, flat: StateValue):
    return up if value > 0 else down if value < 0 else flat


def assemble(
    mode: AdviceMode,
    as_of: date,
    *,
    figures: Mapping[str, Decimal | int | str | None],
    accounts: Sequence[AccountIn] = (),
    plans: Sequence[PlanIn] = (),
    assets: Sequence[AssetIn] = (),
    reserve: tuple[Decimal, Decimal] | None = None,  # amount, target
    window_months: int | None = None,
    bands: Sequence[BandLimits] | None = None,
    lights: Mapping[MarkedFigure, LightColour | None] | None = None,
    projection: ProjectionIn | None = None,
    paths: Mapping[str, tuple[Decimal | None, str | None]] | None = None,
) -> ExplainFacts:
    """The facts of one request. `figures` maps keys of §8.1 to values (None: not computed,
    so absent); `paths` maps a path key (`reserve_first`, `as_is`, `pause:<plan key>`) to its
    first month's amount to the reserve and the month it is reached."""
    generic = mode is AdviceMode.GENERIC
    out: dict[str, Fact] = {}
    for key, value in figures.items():
        if value is not None:
            out[key] = fact(key, value)

    account_keys = {} if generic else {f"k{i}": a for i, a in enumerate(accounts, 1)}
    plan_keys = {} if generic else {f"p{i}": p for i, p in enumerate(plans, 1)}
    held = [a for a in assets if a.units > 0 and not a.spam]
    asset_keys = {} if generic else {f"a{i}": a for i, a in enumerate(held, 1)}

    by_plan_key = {p.plan_key: slot for slot, p in plan_keys.items() if p.plan_key}
    for path_key, (monthly, reached) in (paths or {}).items():
        if path_key.startswith("pause:"):
            slot = by_plan_key.get(path_key.removeprefix("pause:"))
            if slot is None:  # generic, or a plan not listed: no opaque key to name it by
                continue
            name = f"pause_{slot}"
        else:
            name = path_key
        if monthly is not None:
            out[f"path_{name}_monthly"] = fact(f"path_{name}_monthly", monthly)
        if reached is not None:
            out[f"path_{name}_reached"] = fact(f"path_{name}_reached", reached)

    def number(key: str) -> Decimal | None:
        f = out.get(key)
        return None if f is None else Decimal(f.value)

    states: dict[str, StateValue] = {}
    amount, target = reserve if reserve else (number("reserve"), number("reserve_target"))
    if amount is not None and target is not None:
        states["reserve_state"] = StateValue.BELOW if amount < target else StateValue.REACHED
    if (investable := number("investable")) is not None:
        states["investable_state"] = StateValue.POSITIVE if investable > 0 else StateValue.ZERO
    if (left := number("investable_left")) is not None:
        states["left_state"] = StateValue.NEGATIVE if left < 0 else StateValue.POSITIVE
    basis = window_months if window_months is not None else number("basis_months")
    if basis is not None:
        states["basis_state"] = StateValue.FULL if basis >= _FULL_BASIS else StateValue.SHORT
    if (change := number("net_worth_change")) is not None:
        states["trend_state"] = _sign_state(change, StateValue.UP, StateValue.DOWN, StateValue.FLAT)

    for band in bands or []:
        slot = BAND_SLOT[band.figure]
        if band.figure is MarkedFigure.RESERVE:
            if band.red is not None and target is not None:
                out["band_reserve_red"] = fact("band_reserve_red", band.red * target)
        else:
            for side, limit in (("green", band.green), ("red", band.red)):
                if limit is not None:
                    out[f"{slot}_{side}"] = fact(f"{slot}_{side}", limit)
        colour = (lights or {}).get(band.figure)
        states[f"light_{band.figure.value}"] = (
            StateValue(colour.value) if colour is not None else StateValue.NONE
        )

    if projection is not None:
        states["projection_state"] = StateValue.SHOWN
        for key, value in (
            ("assumed_change_depot", projection.depot),
            ("assumed_change_crypto", projection.crypto),
            ("flat_value_growth", projection.flat_value_growth),
            ("projection_until", projection.until),
            ("projection_total", projection.total),
            ("projection_change", projection.change),
            ("projection_assumption_part", projection.assumption_part),
        ):
            if value is not None:
                out[key] = fact(key, value)
    else:
        states["projection_state"] = StateValue.ABSENT
        for key in PROJECTION_KEYS:
            out.pop(key, None)

    return ExplainFacts(
        mode=mode,
        as_of=as_of,
        figures=[out[k] for k in sorted(out)],
        states=states,
        accounts={k: a.kind for k, a in account_keys.items()},
        plans={k: p.asset_class for k, p in plan_keys.items()},
        assets={k: a.asset_class for k, a in asset_keys.items()},
        labels=ExplainLabels(
            accounts={k: a.label for k, a in account_keys.items()},
            plans={k: p.label for k, p in plan_keys.items()},
            assets={k: a.name for k, a in asset_keys.items()},
        ),
    )


def facts_sha256(facts: ExplainFacts) -> str:
    """The hash of the serialised facts (§8.1); the labels are not in the dump."""
    return hashlib.sha256(facts.model_dump_json().encode()).hexdigest()


# --- From the household's figures and plan ----------------------------------------------


def build(
    health: Health,
    plan: HouseholdPlan,
    mode: AdviceMode,
    *,
    accounts: Sequence[AccountIn] = (),
    assets: Sequence[AssetIn] = (),
    plan_labels: Mapping[str, str] | None = None,
) -> ExplainFacts:
    """Figures, states, kinds and classes under opaque keys; the labels apart, never sent.

    `accounts` and `assets` are the household's (not in `Health` or `HouseholdPlan`);
    `plan_labels` names each plan's destination by the plan's own key, for the labels only."""
    figures: dict[str, Decimal | int | str | None] = {}
    point = next((p for p in reversed(health.series) if p.total is not None), None)
    if point is not None:
        figures["net_worth"] = point.total
        figures["net_worth_change"] = point.change
    if (rate := health.savings_rate) is not None:
        figures |= {
            "savings_rate": rate.rate,
            "savings_rate_regular": rate.rate_regular,
            "saved_total": rate.saved,
            "principal_total": rate.principal,
        }
    reserve = plan.reserve or health.reserve
    if reserve is not None:
        figures |= {
            "reserve": reserve.amount,
            "reserve_months": reserve.months,
            "monthly_need": reserve.need,
            "reserve_target": reserve.target,
            "reserve_target_months": reserve.target_months,
            "reserve_gap": reserve.gap,
        }
    if plan.surplus is not None:
        rows = {r.key: r.amount for r in plan.surplus.rows}
        for key, row in (
            ("free_cash", SurplusKey.FREE_CASH),
            ("free_cash_median", SurplusKey.MEDIAN_FREE_CASH),
            ("free_cash_lowest", SurplusKey.LOWEST_FREE_CASH),
            ("buffer", SurplusKey.BUFFER),
            ("investable", SurplusKey.INVESTABLE),
        ):
            figures[key] = rows.get(row)
    figures["already_invested"] = plan.already_invested
    figures["investable_left"] = plan.investable_left
    if (debt := health.debt) is not None:
        ltvs = [p.ltv for p in debt.properties if p.ltv is not None]
        figures |= {
            "ltv": max(ltvs) if ltvs else None,  # the highest, as bands.py marks it
            "debt_ratio": debt.debt_ratio,
            "debt_service_ratio": debt.debt_service_ratio,
            "debt_to_income": debt.debt_to_income,
        }
    for cls in (
        NetWorthClass.BANK,
        NetWorthClass.DEPOT,
        NetWorthClass.CRYPTO,
        NetWorthClass.PROPERTY,
    ):
        figures[f"share_{cls.value}"] = health.split.get(cls)
    figures["basis_months"] = len(health.window.months)

    plans = [
        PlanIn(
            label=(plan_labels or {}).get(p.key, p.destination_payee or ""),
            asset_class=p.asset_class,
            plan_key=p.key,
        )
        for p in plan.plans
    ]
    paths = {p.key: (p.to_reserve_first_month, p.reached) for p in plan.paths}
    marks = {f: m.colour for f, m in health.marks.items()}
    projection = None
    shown = plan.projection
    if shown is not None and shown.points and shown.points[-1].change is not None:
        last = shown.points[-1]
        assumed = {a.kind: a.value for a in shown.assumptions}
        flats = [a for a in shown.assumptions if a.kind is AssumptionKind.FLAT_GROWTH]
        projection = ProjectionIn(
            until=last.on.strftime("%Y-%m"),
            total=last.total,
            change=last.change.total,
            assumption_part=last.change.from_assumptions,
            depot=assumed.get(AssumptionKind.DEPOT_RATE),
            crypto=assumed.get(AssumptionKind.CRYPTO_RATE),
            flat_value_growth=flats[0].value if len(flats) == 1 else None,
        )
    return assemble(
        mode,
        health.as_of,
        figures=figures,
        accounts=accounts,
        plans=plans,
        assets=assets,
        reserve=(reserve.amount, reserve.target) if reserve is not None else None,
        window_months=len(health.window.months),
        bands=plan.levers.bands,
        lights=marks,
        projection=projection,
        paths=paths,
    )
