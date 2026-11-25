"""GET /household-plan?scenario= (docs/phases/phase-9.md §6, §7, §13; D244–D248; phase 9
step 2): the household plan assembled on read from stored data by the pure rules in
rules/plan/. Nothing here writes, and no figure is computed outside those rules.

- **Levers**: the household scenario named, else the default in force, else the defaults
  (D248); a scenario naming an account or a plan that no longer exists is still used, with
  the to-do „Szenario prüfen“ (§7.3).
- **Window**: step 1's closed months (services.health). Below three closed months the plan
  holds the levers and the to-dos only (D231): no mean, no plan, no path, no projection.
- **Surplus** (D244): rules.plan.surplus over the window's household months.
- **Savings plans** (D245): the confirmed outgoing payments of the private accounts in the
  last three closed months, into an own depot or exchange (the out-leg of a current transfer
  link, `saving_links`, by the out-leg's booking month) or booked hh_investment_securities /
  hh_investment_crypto to a payee; then the scenario's overrides.
- **Reserve and paths** (D237, D246): the reserve at the end of the newest closed month, from
  that month-end's stored run (D241); without a run from today's balances (the accounts'
  `as_of` then says „Stand heute“). The paths start from it.
- **Months** (§7.1, D247): flat(k) from each property's default phase 4 scenario, its
  projected years' (net_cash + tax_effect.effect) ÷ 12, through services.performance as it
  is; the measured mean for the newest closed month's calendar year, for a year phase 4 does
  not project, and whenever a property has no default scenario (then for every property:
  the mean is the household's, not split by property).
- **Net worth in some years** (§7.5): rules.plan.projection from the stored run at the
  newest closed month-end, or the to-dos that stand in its place. Without a saved household
  scenario no projection is shown (D248). The run is used only while the month-end read
  again is complete by step 1's guard (services.health._incomplete): phase 6 stores a run
  that leaves an unvalued position out, which would start that class at 0.
- **Advice mode** (D251): workspace.advice_mode; under `generic` the paths keep only their
  first and last row.

- **Proposed one-off** (Q-O168, answered by mockup 12): phase 4b's whole-return estimate of
  the most recent past tax year that has one and no confirmed Einkommensteuerbescheid (no
  current household `bescheid_zve`) and that no one-off of the levers already names, its
  result with the sign of a one-off (a refund positive), without a month. An accepted
  one-off names its year in its source („return_estimate:2026“), kept in the scenario even
  after its month has closed, so a refund already received is never proposed again; a bare
  `return_estimate` (saved before the year was written) names none and ends the proposal.
  Nothing for a result of 0, when the estimate refuses (`RuleError`, e.g. no rule set for the
  year), or while it still lacks an owner's Lohnsteuerbescheinigung (its `missing` holds
  `wage_certificate`): the withheld tax is then a projection, and so is the refund (PR #434
  review G).
- **The paths' screen lines** (step 3): the plans without a pause row (`paths_others`), the
  flat's positive mean that lands on the rental account first, and `flat_change`, the month
  the flat's scenario replaces the mean with each path's amount from then on, left out when
  that one month's figure would not hold from then on (an income change or a one-off after it,
  `holds_from`). A path's „im Monat“ comes from the months without one-offs, so a refund in
  the first plan month is not shown as a monthly rate (PR #434 review B).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.categories import CATEGORY_LABEL
from hausbuch.rules.errors import RuleError
from hausbuch.rules.goal.walk import Projection as GoalProjection
from hausbuch.rules.health.window import window_todos
from hausbuch.rules.plan import levers as L
from hausbuch.rules.plan import projection as N
from hausbuch.rules.plan import savings_plans as S
from hausbuch.rules.plan import spending as SP
from hausbuch.rules.plan.paths import flat_change, holds_from, paths
from hausbuch.rules.plan.surplus import surplus
from hausbuch.schemas.advice import AdviceMode
from hausbuch.schemas.allocation import AllocationCategory, TransferLegSource
from hausbuch.schemas.enums import (
    AccountKind,
    AccountPurpose,
    AllocationStatus,
    EntityType,
    IncomeInputKind,
    ReturnInputKind,
)
from hausbuch.schemas.health import HealthTodo, HealthTodoCode, HouseholdMonth, Reserve
from hausbuch.schemas.household import NetWorthClass
from hausbuch.schemas.net_worth import NetWorth
from hausbuch.schemas.performance import YearKind
from hausbuch.schemas.plan import (
    AssumptionKind,
    HouseholdLevers,
    HouseholdPlan,
    HouseholdScenario,
    NetWorthProjection,
    ProjectionAssumption,
    ProposedOneOff,
    SavingsPlan,
    SpendingComparison,
    SurplusKey,
)

from .errors import NotFoundError
from .health import (
    MonthRead,
    _incomplete,
    _reserve_balances,
    _run_figure,
    _window,
    key_figures,
    read_months,
)
from .household import month_bounds
from .household_scenarios import (
    RESERVE_KINDS,
    default_scenario,
    get_scenario,
    list_scenarios,
)
from .income_inputs import list_income_inputs
from .net_worth import net_worth
from .performance import performance_projection
from .plan_inputs import PlanInputs
from .scenarios import default_scenario as property_default_scenario
from .transfers import saving_links

ZERO = Decimal("0.00")
PLAN_HREF = N.PLAN_HREF
# A transfer's two legs may be booked a few banking days apart (phase 6 D162): the links are
# read this much wider than the three months and kept by the out-leg's own month.
LINK_MARGIN = timedelta(days=7)


def advice_mode(conn: Connection) -> AdviceMode:
    value = conn.execute(select(workspace_table.c.advice_mode)).scalar_one_or_none()
    return AdviceMode(value or AdviceMode.OWN_USE.value)


def _scenario(
    conn: Connection, scenario_id: UUID | None, inputs: PlanInputs | None = None
) -> HouseholdScenario | None:
    """The row named (any row, a withdrawn one too, as health.household_levers reads it), else
    the default in force; None: the defaults. `inputs`: the call's kept reads (phase 10
    §7.10 (b)), when the caller has them."""
    if inputs is None:
        row = default_scenario(conn) if scenario_id is None else get_scenario(conn, scenario_id)
    elif scenario_id is None:
        row = inputs.part(("default_scenario",), default_scenario)
    else:
        row = inputs.part(("scenario", scenario_id), lambda c: get_scenario(c, scenario_id))
    if row is None and scenario_id is not None:
        raise NotFoundError(EntityType.HOUSEHOLD_SCENARIO, scenario_id)
    return row


# --- The plan's reads, kept for a data version (phase 10 §7.10 (b)) ---------------------------


def account_kinds(conn: Connection) -> dict[UUID, AccountKind]:
    """Every account's kind, by id."""
    acc = account_table.c
    return {r.id: AccountKind(r.kind) for r in conn.execute(select(acc.id, acc.kind))}


def rental_account_ids(conn: Connection) -> frozenset[UUID]:
    acc = account_table.c
    return frozenset(
        conn.execute(select(acc.id).where(acc.purpose == AccountPurpose.RENTAL.value)).scalars()
    )


def _kinds(inputs: PlanInputs) -> dict[UUID, AccountKind]:
    return inputs.part(("account_kinds",), account_kinds)


def _net_worth(inputs: PlanInputs, on: date) -> NetWorth:
    return inputs.part(("net_worth", on), lambda c: net_worth(c, on))


def _balances(inputs: PlanInputs, month_end: date) -> tuple[NetWorth | None, NetWorth, list]:
    """The stored run at `month_end` (D241), the balances the reserve starts from (that run,
    else today's net worth) and their accounts' rows as services.health reads them."""
    run = inputs.part(("run", month_end), lambda c: _run_figure(c, month_end))
    source = ("run", month_end) if run is not None else ("net_worth", inputs.today)
    balances = run if run is not None else _net_worth(inputs, inputs.today)
    rows = inputs.part(("reserve_balances", source), lambda c: _reserve_balances(c, balances))
    return run, balances, rows


# --- Savings plans (D245) -------------------------------------------------------------------


def plan_lines(conn: Connection, months: Sequence[str]) -> list[S.PlanLine]:
    """The private accounts' confirmed outgoing payments that can make a plan, booked in the
    first to the last of `months`: the out-legs of current links into an own depot or exchange
    (D162: a matcher's link counts only while both legs are booked as transfers, a link by
    hand stays current whatever its legs are booked as), and the lines booked
    hh_investment_securities or hh_investment_crypto to a payee. A line taken as the out-leg
    of a link is never read again as a payment to a payee, so one payment makes one plan."""
    start, end = month_bounds(months[0])[0], month_bounds(months[-1])[1]
    acc, a, t = account_table.c, allocation_table.c, transaction_table.c
    private = {
        r.id
        for r in conn.execute(select(acc.id).where(acc.purpose == AccountPurpose.PRIVATE.value))
    }
    lines: list[S.PlanLine] = []
    linked: set[UUID] = set()
    for view in saving_links(conn, start=start - LINK_MARGIN, end=end + LINK_MARGIN):
        out, into = view.out_leg, view.in_leg
        if (
            out.source is not TransferLegSource.TRANSACTION
            or out.account_id not in private
            or out.amount is None
            or not start <= out.booked_on <= end
        ):
            continue
        kind = into.account_kind
        if kind is None:
            kind = AccountKind(
                conn.execute(select(acc.kind).where(acc.id == into.account_id)).scalar_one()
            )
        linked.add(out.id)
        lines.append(S.PlanLine(out.booked_on, out.amount, into.account_id, kind))
    rows = conn.execute(
        select(t.booking_date, a.amount, a.category, t.counterparty_name)
        .join(transaction_table, t.id == a.transaction_id)
        .join(account_table, acc.id == t.account_id)
        .where(
            acc.purpose == AccountPurpose.PRIVATE.value,
            t.excluded_as_duplicate_of.is_(None),
            a.status == AllocationStatus.CONFIRMED.value,
            a.category.in_([c.value for c in S.PLAN_CODES]),
            a.amount < 0,
            t.booking_date >= start,
            t.booking_date <= end,
            t.id.not_in(linked),
        )
        .order_by(t.booking_date, a.transaction_id, a.id)
    )
    for r in rows:
        category = next(c for c in S.PLAN_CODES if c.value == r.category)
        lines.append(
            S.PlanLine(r.booking_date, r.amount, payee=r.counterparty_name, category=category)
        )
    return lines


# --- To-dos ----------------------------------------------------------------------------------


def _scenario_check(text: str) -> HealthTodo:
    return HealthTodo(
        code=HealthTodoCode.SCENARIO_CHECK,
        text=text,
        action="Szenario prüfen",
        href=PLAN_HREF,
    )


def scenario_todos(
    conn: Connection,
    levers: HouseholdLevers,
    detected: Sequence[SavingsPlan],
    newest: str,
    means: dict[AllocationCategory, Decimal],
    kinds: Mapping[UUID, AccountKind] | None = None,
) -> list[HealthTodo]:
    """§7.3: a reserve account that no longer exists or is no reserve account, an override
    of a plan that is not detected now, and a one-off after the horizon's end (the newest
    closed month `newest` moved back, a month that was closed is open again;
    rules.plan.levers.months leaves its amount out), each named once. A one-off at or before
    `newest` raises nothing: its month is measured and the plan ignores it (§7.3 amended,
    PR #434 review D). Last, a percent change of a code with no mean in `means`
    (`spending_todos`). `kinds`: every account's kind, when the caller has them."""
    todos = []
    named = [*levers.reserve_accounts.added, *levers.reserve_accounts.removed]
    if named:
        acc = account_table.c
        if kinds is None:
            kinds = {
                r.id: AccountKind(r.kind)
                for r in conn.execute(select(acc.id, acc.kind).where(acc.id.in_(named)))
            }
        gone = [i for i in named if i not in kinds]
        wrong = [i for i in levers.reserve_accounts.added if kinds.get(i) not in RESERVE_KINDS]
        wrong = [i for i in wrong if i not in gone]
        if gone:
            todos.append(
                _scenario_check(
                    "Das Szenario nennt ein Konto für die Reserve, das es nicht mehr gibt; "
                    "es zählt nicht mit."
                )
            )
        if wrong:
            todos.append(
                _scenario_check(
                    "Das Szenario zählt ein Konto zur Reserve, das kein Giro-, Spar- oder "
                    "Kreditkartenkonto ist; es zählt nicht mit."
                )
            )
    keys = {p.key for p in detected}
    if any(o.plan_key not in keys for o in levers.plan_overrides):
        todos.append(
            _scenario_check(
                "Das Szenario ändert einen Sparplan, den Hausbuch in den letzten drei "
                "abgeschlossenen Monaten nicht mehr findet; die Änderung zählt nicht mit."
            )
        )
    outside = L.one_offs_outside(levers.one_offs, newest, levers.horizon_months)
    if outside:
        named = [f"{m[5:]}/{m[:4]}" for m in sorted({o.month for o in outside})]
        text = (
            f"Das Szenario enthält eine Einmalzahlung im Monat {named[0]}, der nach dem Ende "
            "des Zeitraums der Rechnung liegt; sie zählt nicht mit."
            if len(outside) == 1
            else f"Das Szenario enthält Einmalzahlungen in den Monaten {', '.join(named)}, die "
            "nach dem Ende des Zeitraums der Rechnung liegen; sie zählen nicht mit."
        )
        todos.append(_scenario_check(text))
    return todos + spending_todos(levers, means)


SPENDING_PLAY_HREF = f"{PLAN_HREF}#ausgaben-durchspielen"


def spending_todos(
    levers: HouseholdLevers, means: dict[AllocationCategory, Decimal]
) -> list[HealthTodo]:
    """§7.6 (1) amended (PR #436 review): a stored percent change of a code with no measured
    spending in today's window (re-booked away, a month opened again, fewer than three closed
    months) is kept and saved again unaltered, but counts for nothing while the code has none
    (rules.plan.spending.planned); one to-do per code names it, with „Ausgaben durchspielen“
    as the place to change it."""
    return [
        HealthTodo(
            code=HealthTodoCode.SCENARIO_CHECK,
            text=(
                f"Das Szenario ändert die Ausgaben für „{CATEGORY_LABEL[code]}“ um einen "
                "Prozentsatz, aber in den abgeschlossenen Monaten gibt es dafür keine "
                "gemessenen Ausgaben; die Änderung zählt gerade nicht mit."
            ),
            action="Szenario prüfen",
            href=SPENDING_PLAY_HREF,
        )
        for code in SP.percents_on_nothing(levers.spending_changes, means)
    ]


def _income_change_todo() -> HealthTodo:
    """D247, Q-O141: an income change of the plan beside the expected gross pay, so phases 4
    and 4b see the same change; the plan converts no gross pay into net pay."""
    return HealthTodo(
        code=HealthTodoCode.INCOME_CHANGE_LINK,
        text=(
            "Das Szenario ändert euer Einkommen. Damit die Steuerwirkung der Wohnung und die "
            "Steuerschätzung dieselbe Änderung kennen, passt dort das erwartete Bruttogehalt an."
        ),
        action="Erwartetes Bruttogehalt anpassen",
        href="/owners",
    )


def _incomplete_run_todo(end: date, figure: NetWorth) -> HealthTodo:
    """§7.5 (1), (2): the stored run at the newest closed month-end left a gap out (phase 6
    stores a run whose position has no value without it), so it is no start: the month-end
    read again is tested with step 1's guard (services.health._incomplete, the daily step's),
    and its first gap's text, button and link stand in the projection's place."""
    gaps = _incomplete(figure)
    more = len(gaps) - 1
    tail = "" if not more else f" ({more} weitere Lücke{'n' if more > 1 else ''} im Nettovermögen)"
    return HealthTodo(
        code=HealthTodoCode.MONTH_END_MISSING,
        text=(
            f"Das Nettovermögen zum {end.strftime('%d.%m.%Y')} ist noch unvollständig, deshalb "
            f"rechnet Hausbuch das Vermögen in einigen Jahren noch nicht: {gaps[0].text}{tail}"
        ),
        action=gaps[0].action,
        href=gaps[0].href,
    )


def _no_scenario_todo() -> HealthTodo:
    """D248: without a saved scenario the plan runs on the defaults and shows no projection."""
    return HealthTodo(
        code=HealthTodoCode.RATE_MISSING,
        text=(
            "Das Vermögen in einigen Jahren rechnet Hausbuch nur mit euren eigenen Annahmen; "
            "ohne gespeichertes Szenario zeigt es diese Rechnung nicht."
        ),
        action="Annahme eintragen",
        href=PLAN_HREF,
    )


def _flat_unprojectable() -> HealthTodo:
    """Phase 4 refuses the property's default scenario (a sale year already past, no rule set
    for a year): the months keep the measured mean and the projection is not shown."""
    return HealthTodo(
        code=HealthTodoCode.SCENARIO_CHECK,
        text=(
            "Das Szenario der Wohnung lässt sich gerade nicht rechnen, deshalb rechnet der Plan "
            "mit dem gemessenen Durchschnitt der Wohnung und zeigt kein Vermögen in einigen "
            "Jahren."
        ),
        action="Szenario der Wohnung öffnen",
        href=N.PROPERTY_HREF,
    )


# --- The flat's scenarios (D247) -------------------------------------------------------------


def flat_scenarios(
    conn: Connection, today: date
) -> tuple[dict[UUID, N.FlatScenario | None], list[HealthTodo], list[ProjectionAssumption]]:
    """Per property its default phase 4 scenario's projected years (None without a default),
    the to-do „Szenario für die Wohnung anlegen“ for a property without a default (D247),
    the to-do when phase 4 refuses to project it, and the flat's growth as an assumption."""
    flats: dict[UUID, N.FlatScenario | None] = {}
    todos: list[HealthTodo] = []
    assumptions: list[ProjectionAssumption] = []
    ids = conn.execute(select(property_table.c.id).order_by(property_table.c.id)).scalars()
    for property_id in ids:
        scenario = property_default_scenario(conn, property_id)
        if scenario is None:  # D247: the measured mean, with „Szenario für die Wohnung anlegen“
            flats[property_id] = None
            if not any(t.code is HealthTodoCode.NO_DEFAULT_SCENARIO for t in todos):
                todos.append(N.scenario_todo())
            continue
        try:
            projection = performance_projection(
                conn, property_id, today=today, scenario_id=scenario.id
            )
        except RuleError:  # RuleViolationError included
            todos.append(_flat_unprojectable())
            flats[property_id] = None
            continue
        flats[property_id] = N.FlatScenario(
            sale_year=projection.scenario.sale_year,
            years={
                y.year: L.FlatYear(
                    net_cash=y.net_cash,
                    tax_effect=None if y.tax_effect is None else y.tax_effect.effect,
                    value=y.value,
                    loan_balance=y.loan_balance,
                )
                for y in projection.years
                if y.kind is YearKind.PROJECTED
            },
        )
        assumptions.append(
            ProjectionAssumption(
                kind=AssumptionKind.FLAT_GROWTH,
                value=projection.scenario.value_growth,
                scenario_id=scenario.id,
                property_id=property_id,
                created_at=scenario.recorded_at,
                created_by=scenario.recorded_by,
            )
        )
    return flats, todos, assumptions


def projection_start(month_end: date, figure: NetWorth | None) -> N.ProjectionStart | None:
    """§7.5 (2): the classes and liabilities of the stored run. The class `liabilities`
    (negative) holds the loans and a card's debt; `loan_balances` is the loans' part."""
    if figure is None:
        return None
    c = figure.by_class
    liabilities = -c.get(NetWorthClass.LIABILITIES, ZERO)
    return N.ProjectionStart(
        month_end=month_end,
        bank=c.get(NetWorthClass.BANK, ZERO),
        depot=c.get(NetWorthClass.DEPOT, ZERO),
        crypto=c.get(NetWorthClass.CRYPTO, ZERO),
        property=c.get(NetWorthClass.PROPERTY, ZERO),
        loans=figure.loan_balances,
        other_liabilities=liabilities - figure.loan_balances,
    )


def _rate_assumptions(row: HouseholdScenario) -> list[ProjectionAssumption]:
    out = []
    for kind, rate in (
        (AssumptionKind.DEPOT_RATE, row.levers.value_change.depot),
        (AssumptionKind.CRYPTO_RATE, row.levers.value_change.crypto),
    ):
        if rate is not None:
            out.append(
                ProjectionAssumption(
                    kind=kind,
                    value=rate,
                    scenario_id=row.id,
                    created_at=row.created_at,
                    created_by=row.created_by,
                )
            )
    return out


# --- GET /household-plan ---------------------------------------------------------------------


def _assessed_years(conn: Connection) -> frozenset[int]:
    """The tax years with a confirmed household Einkommensteuerbescheid (`bescheid_zve`)."""
    return frozenset(
        row.tax_year
        for row in list_income_inputs(conn)
        if row.kind is IncomeInputKind.BESCHEID_ZVE and row.owner_id is None
    )


def _return_estimate(conn: Connection, year: int, today: date):
    """Phase 4b's whole-return estimate of `year`, None when it refuses (RuleError)."""
    from .return_inputs import household_return_estimate

    try:
        return household_return_estimate(conn, tax_year=year, today=today)
    except RuleError:
        return None


def proposed_refund(
    conn: Connection,
    today: date,
    levers: HouseholdLevers,
    inputs: PlanInputs | None = None,
) -> list[ProposedOneOff]:
    """Q-O168: the expected refund or back-payment to propose (see the module doc).
    `inputs`: the call's kept reads (phase 10 §7.10 (b)), when the caller has them."""
    from hausbuch.rules.tax import registry

    accepted = [o for o in levers.one_offs if o.from_return_estimate]
    if any(o.tax_year is None for o in accepted):
        return []  # a bare return_estimate names no year
    named = {o.tax_year for o in accepted}
    assessed = (
        _assessed_years(conn) if inputs is None else inputs.part(("assessed",), _assessed_years)
    )
    for year in sorted((y for y in registry.anlage_n_years() if y < today.year), reverse=True):
        if year in assessed or year in named:
            continue
        estimate = (
            _return_estimate(conn, year, today)
            if inputs is None
            else inputs.part(
                ("return_estimate", year), lambda c, y=year: _return_estimate(c, y, today)
            )
        )
        if estimate is None:
            continue
        if estimate.result == 0 or any(
            m.key == ReturnInputKind.WAGE_CERTIFICATE for m in estimate.missing
        ):
            return []
        return [ProposedOneOff(tax_year=year, amount=-estimate.result)]
    return []


def household_plan(conn: Connection, today: date, scenario_id: UUID | None = None) -> HouseholdPlan:
    """GET /household-plan?scenario= on `today` (Berlin). NotFoundError for an unknown
    scenario. See the module doc."""
    inputs = PlanInputs(conn, today)
    row = _scenario(conn, scenario_id, inputs)
    return _plan(conn, today, row, HouseholdLevers() if row is None else row.levers, None, inputs)


def preview_plan(
    conn: Connection, today: date, levers: HouseholdLevers, scenario_id: UUID | None = None
) -> HouseholdPlan:
    """POST /household-plan/preview (D263 (c)): the plan for typed levers, checked as a
    scenario row would be (services.household_scenarios.check_levers), on the row previewed
    (`scenario_id`, any row as GET reads it, else the default scenario in force) with its
    levers replaced, so the projection keeps its saved row; without a saved row no projection,
    as GET shows. A spending change or a budget class override carried unaltered from that row
    is not refused, as the save superseding it would not refuse it (§7.6 (1) amended, phase 10
    D272 (2)). NotFoundError for an unknown `scenario_id`. Reads only: no row, no event."""
    from .household_scenarios import carried_overrides, check_levers

    inputs = PlanInputs(conn, today)
    reads = inputs.ended_reads()
    row = _scenario(conn, scenario_id, inputs)
    previous = [] if row is None else row.levers.spending_changes
    check_levers(
        conn,
        levers,
        today,
        reads,
        previous=previous,
        previous_overrides=carried_overrides(levers, row),
        previous_goal=None if row is None else row.levers.goal,
        account_kinds=_kinds(inputs),
    )
    if row is not None:
        row = row.model_copy(update={"levers": levers})
    return _plan(conn, today, row, levers, reads, inputs)


def window_spending(
    reads: dict[str, MonthRead], months: Sequence[str]
) -> list[dict[AllocationCategory, Decimal]]:
    """§7.6 (2): each window month's regular spending per code, from the private accounts'
    cashflow (D234, the one-off codes and the payments marked one-off left out)."""
    return [
        SP.month_spending(reads[m].private.cashflow, reads[m].private.lines, reads[m].one_off_ids)
        for m in months
    ]


@dataclass(frozen=True)
class PlanParts:
    """What the household goal reads beside the plan (phase 10 §7.9 (d), services.goal): the
    window's household months, the codes' means, the reserve and the projection's inputs, so
    the goal walks phase 9's projection month by month without computing any of it again.
    `projection` is None while the plan's projection has to-dos."""

    months: tuple[HouseholdMonth, ...]
    means: Mapping[AllocationCategory, Decimal]
    reserve: Reserve
    projection: GoalProjection | None


def _plan(
    conn: Connection,
    today: date,
    row: HouseholdScenario | None,
    levers: HouseholdLevers,
    reads: dict[str, MonthRead] | None = None,
    inputs: PlanInputs | None = None,
) -> HouseholdPlan:
    return plan_parts(conn, today, row, levers, reads, inputs)[0]


def plan_parts(
    conn: Connection,
    today: date,
    row: HouseholdScenario | None,
    levers: HouseholdLevers,
    reads: dict[str, MonthRead] | None = None,
    inputs: PlanInputs | None = None,
) -> tuple[HouseholdPlan, PlanParts | None]:
    """The plan and, from three closed months on, the parts the goal reads (PlanParts).
    `inputs`: the call's reads, kept for a data version (services.plan_inputs); a new
    PlanInputs on `today` when the caller has none. `reads`: today's months, read through
    `inputs` when not given."""
    if inputs is None:
        inputs = PlanInputs(conn, today)
    if reads is None:
        reads = inputs.ended_reads()
    win = _window(reads)
    newest = win.months[-1] if win.months else None
    todos = list(window_todos(win))
    plan = HouseholdPlan(
        as_of=today,
        scenario_id=None if row is None else row.id,
        levers=levers,
        newest_closed=newest,
    )
    if not win.enough:
        todos += spending_todos(levers, {})  # no mean below three closed months (§7.6 (2))
        return plan.model_copy(
            update={"todos": todos, "projection": NetWorthProjection(todos=list(todos))}
        ), None
    assert newest is not None
    months = [reads[m].household for m in win.months]
    sur = surplus(months, levers.buffer_percent)
    spending = window_spending(reads, win.months)
    means = SP.code_means(spending)
    last_three = tuple(win.months[-3:])
    lines = inputs.part(("plan_lines", last_three), lambda c: plan_lines(c, last_three))
    detected = S.detect(lines, list(last_three))
    plans = S.apply_overrides(detected, levers.plan_overrides)
    invested = S.already_invested(plans)
    todos += scenario_todos(conn, levers, detected, newest, means, _kinds(inputs))
    if levers.income_changes:
        todos.append(_income_change_todo())

    month_end = month_bounds(newest)[1]
    run, balances, balance_rows = _balances(inputs, month_end)
    reserve = key_figures(
        conn,
        reads,
        win,
        levers,
        balances=balances,
        debt_figure=None,
        today=balances.on,
        balance_rows=balance_rows,
    ).reserve
    assert reserve is not None  # the window has three months

    flats, flat_todos, flat_assumptions = inputs.part(
        ("flat_scenarios",), lambda c: flat_scenarios(c, today)
    )
    todos += flat_todos
    with_scenario = {k: f for k, f in flats.items() if f is not None}
    flat_years = (
        L.flat_by_year({k: f.years for k, f in with_scenario.items()})
        if flats and len(with_scenario) == len(flats)
        else None
    )
    basis = L.MonthBasis(
        newest_closed=newest,
        mean_income=sur.amount(SurplusKey.MEAN_INCOME),
        mean_regular_spending=sur.amount(SurplusKey.MEAN_REGULAR_SPENDING),
        mean_rental_cash=sur.amount(SurplusKey.MEAN_RENTAL_CASH),
        buffer_percent=levers.buffer_percent,
        spending_changes=tuple(levers.spending_changes),
        code_means=means,
    )
    plan_months = L.months(
        basis, levers.horizon_months, flat_years, levers.income_changes, levers.one_offs
    )
    mode = inputs.part(("advice_mode",), advice_mode)
    steady = L.months(basis, levers.horizon_months, flat_years, levers.income_changes, ())
    rows = paths(reserve.amount, reserve.target, plans, plan_months, newest, mode, steady)
    rental = sur.amount(SurplusKey.MEAN_RENTAL_CASH)
    later = flat_change(plans, steady, rental, mode)
    if later is not None and not holds_from(later, steady, levers.one_offs):
        later = None  # one month's figure that does not hold from then on is not shown
    # „… auf dem Mietkonto, das nicht zur Reserve zählt“ is said only while no rental account
    # is counted: the lever `reserve_accounts.added` may count it (PR #434 review E).
    rental_ids = inputs.part(("rental_ids",), rental_account_ids)
    rental_counted = any(a.counted and a.account_id in rental_ids for a in reserve.accounts)

    walked: GoalProjection | None = None
    if row is None:
        projection = NetWorthProjection(todos=[_no_scenario_todo()])
    elif refused := [t for t in flat_todos if t.code is HealthTodoCode.SCENARIO_CHECK]:
        projection = NetWorthProjection(todos=refused)
    elif run is not None and _incomplete(again := _net_worth(inputs, month_end)):
        projection = NetWorthProjection(todos=[_incomplete_run_todo(month_end, again)])
    else:
        start = projection_start(month_end, run)
        projection = N.project_net_worth(
            basis,
            start,
            month_end,
            reserve.amount,
            plans,
            flats,
            levers,
        ).projection
        if not projection.todos and start is not None:
            walked = GoalProjection(
                basis=basis,
                start=start,
                reserve_start=reserve.amount,
                plans=tuple(plans),
                flats=with_scenario,
                levers=levers,
            )
        if projection.points:
            projection = projection.model_copy(
                update={"assumptions": _rate_assumptions(row) + flat_assumptions}
            )
    parts = PlanParts(months=tuple(months), means=means, reserve=reserve, projection=walked)
    return plan.model_copy(
        update={
            "surplus": sur,
            "plans": plans,
            "already_invested": invested,
            "investable_left": sur.amount(SurplusKey.INVESTABLE) - invested,
            "reserve": reserve,
            "paths": rows.rows,
            "paths_others": rows.others,
            "paths_on_rental_account": rental if rental > 0 and not rental_counted else None,
            "flat_change": later,
            "months": plan_months,
            "spending_play": SP.play(
                months,
                spending,
                levers.spending_changes,
                levers.income_changes,
                levers.horizon_months,
                lambda ym: (
                    L.month_at(
                        basis, L.months_after(newest, ym), None, levers.income_changes
                    ).investable
                ),
            ),
            "proposed_one_offs": proposed_refund(conn, today, levers, inputs),
            "projection": projection,
            "todos": todos,
        }
    ), parts


# --- GET /household-plan/comparison (D263 (d), §7.7) ------------------------------------------


def spending_comparison(conn: Connection, today: date, month: str) -> SpendingComparison:
    """The month's real spending beside the default household scenario in force on its last
    day (today for the running month), for each code that scenario names with a change in
    force in the month; no rows without such a scenario. Final only for a closed month that
    has ended; otherwise „bisher“ with the month's lines still waiting in Zu prüfen. Reads
    the month as the Haushalt screen does (services.health.read_months)."""
    read = read_months(conn, [month], today)[month]
    when = SP.as_of(month, today)
    scenario = SP.in_force_on(list_scenarios(conn, include_history=True), when.day)
    return SP.comparison(
        month,
        today,
        closed=read.closed.closed,
        open_lines=read.private.cashflow.open_lines,
        scenario=scenario,
        changes=[] if scenario is None else scenario.levers.spending_changes,
        lines=read.private.lines,
        one_off_ids=read.one_off_ids,
    )


__all__ = [
    "advice_mode",
    "household_plan",
    "plan_lines",
    "preview_plan",
    "projection_start",
    "spending_comparison",
    "proposed_refund",
    "window_spending",
]
