"""The key figures (docs/phases/phase-9.md §5.1–§5.4). Fixtures: savings_rate.yaml,
reserve.yaml, debt.yaml, net_worth_series.yaml under tests/fixtures/phase9/.

Pure functions over the window's `HouseholdMonth`s (rules/health/month), the newest balances,
a `NetWorth` (phase 6) and the stored runs. Money exact to the cent, ratios four places
half-up, months one place, means to the cent (half-up); nothing reads a clock: the day that
decides staleness is passed in.
"""

import calendar
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from uuid import UUID

from hausbuch.rules.net_worth import split as split_by_shares
from hausbuch.rules.valuation import STALE_AFTER_DAYS
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.health import (
    Debt,
    DebtFigure,
    DebtGap,
    EstimateSource,
    HealthTodo,
    HealthTodoCode,
    HouseholdMonth,
    MonthRate,
    NetWorthPoint,
    PropertyEstimate,
    PropertyLtv,
    Reserve,
    ReserveAccount,
    ReserveBalance,
    ReserveOrigin,
    ReserveState,
    RunTotals,
    SavingsRate,
    ScenarioValue,
    ratio,
)
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.net_worth import NetWorth, NetWorthLineKind
from hausbuch.schemas.plan import HouseholdLevers, ReserveAccounts

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

# D237: the reserve accounts by default (a private current or savings account, or a card).
RESERVE_KINDS = frozenset(
    {AccountKind.BANK_CURRENT, AccountKind.BANK_SAVINGS, AccountKind.CREDIT_CARD}
)


def _cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _mean(values: Sequence[Decimal]) -> Decimal:
    return _cent(sum(values, ZERO) / len(values))


def _german(day: date) -> str:
    return day.strftime("%d.%m.%Y")


# --- 5.1 Savings rate (D235) -----------------------------------------------------------


def savings_rate(window_months: Sequence[HouseholdMonth]) -> SavingsRate:
    """Σ saved ÷ Σ income over the window, with and without one-offs and principal (D235);
    every ratio None when Σ income ≤ 0, and a month's own rate None when its income ≤ 0."""
    saved = sum((m.saved for m in window_months), ZERO)
    income = sum((m.income for m in window_months), ZERO)
    one_off = sum((m.one_off_spending for m in window_months), ZERO)
    principal = sum((m.principal for m in window_months), ZERO)
    return SavingsRate(
        saved=saved,
        income=income,
        one_off_spending=one_off,
        principal=principal,
        rate=ratio(saved, income),
        rate_regular=ratio(saved + one_off, income),
        rate_without_principal=ratio(saved - principal, income),
        months=[MonthRate(month=m.month, rate=m.savings_rate) for m in window_months],
    )


# --- 5.2 Reserve (D237, D238) ----------------------------------------------------------


def need(window_months: Sequence[HouseholdMonth]) -> Decimal:
    """The monthly need: mean regular spending + max(0, −mean C), both means to the cent."""
    if not window_months:
        raise ValueError("the need is taken over at least one closed month")
    regular = _mean([m.regular_spending for m in window_months])
    cash = _mean([m.rental_cash for m in window_months])
    return regular + max(ZERO, -cash)


def _origin(balance: ReserveBalance, accounts: ReserveAccounts) -> ReserveOrigin | None:
    if balance.account_id in accounts.removed:
        return ReserveOrigin.REMOVED
    if balance.account_id in accounts.added:
        return ReserveOrigin.ADDED
    if balance.kind in RESERVE_KINDS and not balance.rental:
        return ReserveOrigin.DEFAULT
    return None


def _stale(balance: ReserveBalance, today: date) -> HealthTodo:
    """The net worth's own to-do for an outdated balance (services/net_worth._outdated)."""
    card = balance.kind is AccountKind.CREDIT_CARD
    return HealthTodo(
        code=HealthTodoCode.BALANCE_STALE,
        text=(
            f"Der Kontostand von „{balance.label}“ ist vom {_german(balance.as_of)}; bis "
            f"zum {_german(today)} fehlt der nächste Kontoauszug, und die Reserve zählt "
            "den alten Stand."
        ),
        action="Kreditkartenabrechnung hochladen" if card else "Kontoauszug hochladen",
        href=f"/accounts/{balance.account_id}?tab=kontoauszuege",
    )


def reserve(
    balances: Sequence[ReserveBalance],
    window_months: Sequence[HouseholdMonth],
    levers: HouseholdLevers,
    *,
    today: date,
) -> Reserve:
    """The reserve in euros and in months of the need, against the target (D237, D238).

    `balances`: every account's newest balance (the service may pass them all; the kinds
    and `levers.reserve_accounts` choose); `levers.reserve_target_months` sets the target.
    An account removed by the lever is
    listed, not counted. `by_owner` splits each counted account by its shares with phase 6's
    cent rule; it is empty when a counted account has no shares recorded. Each counted
    balance older than STALE_AFTER_DAYS before `today` adds one to-do."""
    lever = levers.reserve_accounts
    target_months = levers.reserve_target_months
    listed: list[ReserveAccount] = []
    counted: list[ReserveBalance] = []
    for balance in balances:
        origin = _origin(balance, lever)
        if origin is None:
            continue
        listed.append(
            ReserveAccount(
                account_id=balance.account_id,
                kind=balance.kind,
                balance=balance.balance,
                as_of=balance.as_of,
                origin=origin,
            )
        )
        if origin is not ReserveOrigin.REMOVED:
            counted.append(balance)
    amount = sum((b.balance for b in counted), ZERO)
    by_owner: dict[UUID, Decimal] = {}
    if all(b.owners for b in counted):
        for b in counted:
            shares = {o: Fraction(s.numerator, s.denominator) for o, s in b.owners.items()}
            for owner, part in split_by_shares(b.balance, shares).items():
                by_owner[owner] = by_owner.get(owner, ZERO) + part
    monthly = need(window_months)
    target = _cent(target_months * monthly)
    months = None if monthly == 0 else (amount / monthly).quantize(Decimal("0.1"), ROUND_HALF_UP)
    return Reserve(
        accounts=listed,
        amount=amount,
        need=monthly,
        months=months,
        target_months=target_months,
        target=target,
        gap=max(ZERO, target - amount),
        state=ReserveState.REACHED if amount >= target else ReserveState.BELOW,
        by_owner=dict(sorted(by_owner.items(), key=lambda item: str(item[0]))),
        todos=[
            _stale(b, today)
            for b in counted
            if (today - b.as_of) > timedelta(days=STALE_AFTER_DAYS)
        ],
    )


# --- 5.3 Debt (D239) and the split (D240) ---------------------------------------------


def _gross(net_worth: NetWorth) -> Decimal:
    return sum(
        (v for c, v in net_worth.by_class.items() if c is not NetWorthClass.LIABILITIES), ZERO
    )


def debt(
    net_worth: NetWorth,
    window_months: Sequence[HouseholdMonth],
    *,
    interest: Decimal,
    estimate_sources: Mapping[UUID, EstimateSource],
) -> Debt:
    """The four debt figures, annualised from the window (D239).

    `net_worth`: the newest closed month-end's run (or NetWorth on a day); its class
    `liabilities` (negative) holds the loans and a card's debt, its property lines the
    estimates and its loan lines each loan's balance (negative) by property.
    `window_months`: the closed months (n = their count). `interest`: Σ of the window's
    rental interest (the rental blocks' `interest`; HouseholdMonth carries no interest field).
    `estimate_sources`: per property with an estimate line, whether it is the default
    scenario's „Wert heute“ or the purchase price (the run's lines do not say)."""
    n = len(window_months)
    if not 1 <= n <= 12:
        raise ValueError("the debt figures are taken over 1 to 12 closed months")
    gross = _gross(net_worth)
    liabilities = -net_worth.by_class.get(NetWorthClass.LIABILITIES, ZERO)
    estimates: dict[UUID, Decimal] = {}
    owed: dict[UUID, Decimal] = {}
    for line in net_worth.lines:
        if line.property_id is None or line.value_eur is None:
            continue
        if line.kind is NetWorthLineKind.PROPERTY:
            estimates[line.property_id] = estimates.get(line.property_id, ZERO) + line.value_eur
        elif line.kind is NetWorthLineKind.LOAN:
            owed[line.property_id] = owed.get(line.property_id, ZERO) - line.value_eur
    gaps: list[DebtGap] = []
    properties: list[PropertyLtv] = []
    for property_id, estimate in estimates.items():
        if property_id not in estimate_sources:
            raise ValueError(f"no estimate source for property {property_id}")
        loan = owed.get(property_id, ZERO)
        ltv = ratio(loan, estimate) if property_id in owed else None
        if property_id in owed and ltv is None:
            gaps.append(
                DebtGap(
                    figure=DebtFigure.LTV,
                    reason="Der geschätzte Wert der Immobilie ist 0 €; der Anteil der Kredite "
                    "lässt sich nicht berechnen.",
                )
            )
        properties.append(
            PropertyLtv(
                property_id=property_id,
                loan_balance=loan,
                estimate=estimate,
                estimate_source=estimate_sources[property_id],
                ltv=ltv,
            )
        )
    principal = sum((m.principal for m in window_months), ZERO)
    income = sum((m.income for m in window_months), ZERO)
    service = _cent((interest + principal) * 12 / n)
    income_year = _cent((income + interest) * 12 / n)
    debt_ratio = ratio(liabilities, gross)
    if debt_ratio is None:
        gaps.append(
            DebtGap(
                figure=DebtFigure.DEBT_RATIO,
                reason="Das Vermögen ohne Schulden ist 0 € oder weniger; der Verschuldungsgrad "
                "lässt sich nicht berechnen.",
            )
        )
    dti = None
    if income_year > 0:
        dti = (liabilities / income_year).quantize(CENT, rounding=ROUND_HALF_UP)
    else:
        reason = (
            "Das Jahreseinkommen aus dem Zeitraum ist 0 € oder weniger; die Kennzahl lässt "
            "sich nicht berechnen."
        )
        gaps.append(DebtGap(figure=DebtFigure.DEBT_SERVICE_RATIO, reason=reason))
        gaps.append(DebtGap(figure=DebtFigure.DEBT_TO_INCOME, reason=reason))
    return Debt(
        properties=properties,
        liabilities=liabilities,
        gross_assets=gross,
        debt_ratio=debt_ratio,
        interest=interest,
        principal=principal,
        debt_service_year=service,
        income_year=income_year,
        debt_service_ratio=ratio(service, income_year),
        debt_to_income=dti,
        months=n,
        gaps=gaps,
    )


def split(net_worth: NetWorth) -> dict[NetWorthClass, Decimal]:
    """Each asset class's share of the gross assets, each rounded on its own (they need not
    sum to 1), never marked (D240); empty when the gross assets are 0 or below."""
    gross = _gross(net_worth)
    if gross <= 0:
        return {}
    return {
        c: (v / gross).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        for c, v in net_worth.by_class.items()
        if c is not NetWorthClass.LIABILITIES
    }


# --- 5.4 Net worth over time (D241) ----------------------------------------------------


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _previous_end(end: date) -> date:
    return end.replace(day=1) - timedelta(days=1)


def _next_end(end: date) -> date:
    following = end + timedelta(days=1)
    return _month_end(following.year, following.month)


def _gap_todo(end: date) -> HealthTodo:
    return HealthTodo(
        code=HealthTodoCode.MONTH_END_MISSING,
        text=(
            f"Für den {_german(end)} ist kein Monatsabschluss gespeichert; der Verlauf hat "
            "dort eine Lücke."
        ),
        action="Monatsabschluss festhalten",
        # The Übersicht's Nettovermögen card holds the button; the month-end it stores is
        # named (POST /net-worth/snapshots {as_of} takes a past month-end since D241).
        href=f"/?month_end={end.isoformat()}",
    )


def series(runs: Sequence[RunTotals], months: Sequence[HouseholdMonth]) -> list[NetWorthPoint]:
    """The net worth per month-end from the stored runs, the newest run per month-end by its
    creation time (D241). From the second point on, against the month-end directly before
    only: `change`, `saved` (that month's S when it is among `months`, the closed months)
    and `other = change − saved`. A month-end between the first and the last run without a
    run is a gap point with the to-do „Monatsabschluss festhalten“; before the first run
    the series has not started."""
    newest: dict[date, RunTotals] = {}
    for run in runs:
        held = newest.get(run.month_end)
        if held is None or run.created_at > held.created_at:
            newest[run.month_end] = run
    saved = {m.month: m.saved for m in months}
    points: list[NetWorthPoint] = []
    if not newest:
        return points
    end, last = min(newest), max(newest)
    while end <= last:
        run = newest.get(end)
        if run is None:
            points.append(NetWorthPoint(month_end=end, todo=_gap_todo(end)))
        else:
            before = newest.get(_previous_end(end))
            change = month_saved = other = None
            if before is not None:
                change = run.total - before.total
                month_saved = saved.get(end.strftime("%Y-%m"))
                other = None if month_saved is None else change - month_saved
            points.append(
                NetWorthPoint(
                    month_end=end,
                    net_worth_run_id=run.net_worth_run_id,
                    total=run.total,
                    by_class=run.by_class,
                    by_owner=run.by_owner,
                    change=change,
                    saved=month_saved,
                    other=other,
                )
            )
        end = _next_end(end)
    return points


def estimate_on(
    scenarios: Sequence[ScenarioValue], month_end: date, purchase_price: Decimal
) -> PropertyEstimate:
    """D241 (amends phase 6 D167 (5)): a property's estimate on a past month-end is the
    default scenario as it stood that day (the newest default, non-withdrawn row written on
    or before it), else its purchase price. "Newest" is `services/scenarios.default_scenario`'s
    order: the latest `recorded_at`, and between rows recorded at the same instant the
    smallest id."""
    standing = [s for s in scenarios if s.default and not s.withdrawn and s.created_on <= month_end]
    if not standing:
        return PropertyEstimate(value=purchase_price, source=EstimateSource.PURCHASE_PRICE)
    newest = max(s.recorded_at for s in standing)
    latest = min((s for s in standing if s.recorded_at == newest), key=lambda s: s.scenario_id)
    return PropertyEstimate(
        value=latest.value_today, source=EstimateSource.SCENARIO, scenario_id=latest.scenario_id
    )
