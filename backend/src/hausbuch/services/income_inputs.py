"""Income inputs: the household's income facts per tax year (phase 4 D83, D84, §6, §8).

One append-only table `income_input` (migration 0016: SELECT and INSERT only for the app role,
a forbid_mutation trigger). Kinds: `expected_gross_pay` and `deductions_override` per owner,
`bescheid_zve` (the assessed joint zvE, its Bescheid as the document) and
`marginal_rate_override` per household (joint filing: one zvE, one rate). A row is never
changed: a correction is a new row whose `supersedes_id` names the row it replaces, and a
withdrawal is a new row with `withdrawn = true` that repeats the withdrawn row's value. Every
row is written together with its event in the caller's transaction (principle 4):
`owner.income_input_recorded` (entity = the owner) for the owner kinds,
`workspace.income_input_recorded` (entity = the workspace) for the household kinds. The event
carries ids, kind, year and value (amounts and codes only), never the note (free text).

"Current" (in force) = not withdrawn and not superseded by any other row. What a write must
satisfy, beyond IncomeInputCreate's own scope validator (`income_input.scope_invalid`, a 422
validation error on the body):

- the owner of an owner kind and the document exist in this workspace (404 otherwise);
- `supersedes_id` names a row of the same kind, tax year and owner
  (`income_input.scope_invalid`) that is current (`income_input.not_current`); a withdrawal
  repeats its value (`income_input.scope_invalid`);
- no other current row has the same kind, tax year and owner, so the rules never see two
  answers to one question (`income_input.not_current`: supersede it instead).

Writes of one workspace are serialised by locking the workspace row (FOR NO KEY UPDATE), so
the "no other current row" check cannot race; `supersedes_id` is UNIQUE in the table as well.

Reads for the rules: `income_facts(conn)` returns every current row of the workspace (all
years) as IncomeInputFact, which rules/performance/income.py takes (its "in force" filter is
then a no-op). `tax_rate(...)` is GET /api/tax-rate: the household base and the tax effect of
a property's result for one year, from those rows, the owners' church tax and the year's
tariff.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Row, and_, exists, insert, select
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import income_input as income_input_table
from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import workspace as workspace_table
from hausbuch.rules.errors import (
    INCOME_ASSESSED_RESULT_NEEDED,
    INCOME_INPUT_NOT_CURRENT,
    INCOME_INPUT_SCOPE_INVALID,
    TAX_YEAR_NOT_AVAILABLE,
    RuleError,
)
from hausbuch.rules.performance.income import owner_estimates, year_tax_effect
from hausbuch.rules.tax import registry
from hausbuch.schemas.common import Money, Schema, TaxYear
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import EventType, IncomeInputRecorded
from hausbuch.schemas.performance import (
    OWNER_SCOPED,
    IncomeInput,
    IncomeInputCreate,
    IncomeInputFact,
    OwnerTaxProfile,
    OwnerZve,
    TariffStamp,
    TaxEffect,
    ZveEstimate,
)

from .allocations import user_id
from .context import CommandContext
from .errors import ConflictError, NotFoundError, RuleViolationError, violated_constraint
from .events import append_event
from .lookup import require_document, require_row
from .owners import list_owners
from .tax import anlage_v_draft

INCOME_INPUT = "income_input"  # NotFoundError's entity name: the row carries no events itself

_SUPERSEDED_ONCE = "income_input_supersedes_id_key"
_SCOPE_COLUMNS = ("kind", "tax_year", "owner_id")


# --- Reads -------------------------------------------------------------------------------


def _is_current() -> Any:
    """SQL: the row is neither withdrawn nor superseded by another row."""
    later = income_input_table.alias("later")
    return and_(
        income_input_table.c.withdrawn.is_(False),
        ~exists().where(later.c.supersedes_id == income_input_table.c.id),
    )


def list_income_inputs(
    conn: Connection,
    *,
    tax_year: int | None = None,
    owner_id: UUID | None = None,
    include_history: bool = False,
) -> list[IncomeInput]:
    """GET /income-inputs: the workspace's current rows, oldest first; with include_history
    every row (superseded and withdrawn ones too). tax_year keeps that year's rows; owner_id
    keeps that owner's rows (the household kinds have none)."""
    query = select(income_input_table)
    if not include_history:
        query = query.where(_is_current())
    if tax_year is not None:
        query = query.where(income_input_table.c.tax_year == tax_year)
    if owner_id is not None:
        query = query.where(income_input_table.c.owner_id == owner_id)
    rows = conn.execute(query.order_by(income_input_table.c.recorded_at, income_input_table.c.id))
    return [_to_income_input(r) for r in rows]


def income_facts(conn: Connection) -> list[IncomeInputFact]:
    """Every current income_input row of the workspace, all years, oldest first, as the rules
    take them (rules/performance/income.zve_base and year_tax_effect). Phase 4 step 2's
    services/performance.py calls this; keep the name and shape."""
    return [
        IncomeInputFact.model_validate(
            r.model_dump(exclude={"workspace_id", "recorded_at", "recorded_by"})
        )
        for r in list_income_inputs(conn)
    ]


def owner_profiles(conn: Connection) -> list[OwnerTaxProfile]:
    """The workspace's owners as the tariff sees them (church tax and Bundesland), by name."""
    return [
        OwnerTaxProfile(owner_id=o.id, church_tax=o.church_tax, tax_state=o.tax_state)
        for o in list_owners(conn)
    ]


def get_income_input(conn: Connection, income_input_id: UUID) -> IncomeInput | None:
    row = conn.execute(
        select(income_input_table).where(income_input_table.c.id == income_input_id)
    ).one_or_none()
    return None if row is None else _to_income_input(row)


def _find_current(conn: Connection, data: IncomeInputCreate) -> list[IncomeInput]:
    """The current rows of the same kind, tax year and owner (oldest first)."""
    c = income_input_table.c
    owner = c.owner_id.is_(None) if data.owner_id is None else c.owner_id == data.owner_id
    query = select(income_input_table).where(
        c.kind == data.kind.value, c.tax_year == data.tax_year, owner, _is_current()
    )
    rows = conn.execute(query.order_by(c.recorded_at, c.id))
    return [_to_income_input(r) for r in rows]


# --- Writes ------------------------------------------------------------------------------


def create_income_input(ctx: CommandContext, data: IncomeInputCreate) -> IncomeInput:
    """POST /income-inputs: one new row and its event (see the module doc for the checks).
    Raises NotFoundError for an unknown owner, document or superseded row;
    RuleViolationError income_input.scope_invalid or income_input.not_current. Nothing is
    written then."""
    _lock_workspace(ctx.conn, ctx.workspace_id)
    if data.owner_id is not None:
        require_row(ctx.conn, owner_table, EntityType.OWNER, data.owner_id)
    require_document(ctx.conn, data.document_id)
    if data.supersedes_id is not None:
        _check_supersedes(ctx.conn, data)
    _check_no_other_current(ctx.conn, data)

    try:
        row = ctx.conn.execute(
            insert(income_input_table)
            .values(
                workspace_id=ctx.workspace_id,
                **data.model_dump(exclude={"value"}),
                value=data.value.model_dump(mode="json"),
                recorded_at=ctx.now,
                recorded_by=user_id(ctx),
            )
            .returning(*income_input_table.c)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _SUPERSEDED_ONCE:
            # Unreachable while the workspace lock holds; kept so a race is a 409, not a 500.
            raise ConflictError(
                f"income input {data.supersedes_id} was superseded concurrently",
                constraint=_SUPERSEDED_ONCE,
            ) from None
        raise
    created = _to_income_input(row)
    payload = IncomeInputRecorded(
        income_input_id=created.id,
        kind=created.kind,
        tax_year=created.tax_year,
        owner_id=created.owner_id,
        document_id=created.document_id,
        value=created.value,
        supersedes_id=created.supersedes_id,
        withdrawn=created.withdrawn,
    )
    if created.kind in OWNER_SCOPED:
        assert created.owner_id is not None
        append_event(ctx, EventType.OWNER_INCOME_INPUT_RECORDED, created.owner_id, payload)
    else:
        append_event(ctx, EventType.WORKSPACE_INCOME_INPUT_RECORDED, ctx.workspace_id, payload)
    return created


def _lock_workspace(conn: Connection, workspace_id: UUID) -> None:
    """The workspace row, locked FOR NO KEY UPDATE: income input writes of one household
    queue behind each other, while inserts that only reference the workspace don't."""
    conn.execute(
        select(workspace_table.c.id)
        .where(workspace_table.c.id == workspace_id)
        .with_for_update(key_share=True)
    ).one()


def _scope_invalid(message: str) -> RuleViolationError:
    return RuleViolationError([INCOME_INPUT_SCOPE_INVALID], message)


def _check_supersedes(conn: Connection, data: IncomeInputCreate) -> None:
    assert data.supersedes_id is not None
    old = get_income_input(conn, data.supersedes_id)
    if old is None:
        raise NotFoundError(INCOME_INPUT, data.supersedes_id)
    if any(getattr(old, name) != getattr(data, name) for name in _SCOPE_COLUMNS):
        raise _scope_invalid("a correction supersedes a row of the same kind, year and owner")
    if old.id not in {r.id for r in _find_current(conn, data)}:
        raise RuleViolationError(
            [INCOME_INPUT_NOT_CURRENT],
            f"income input {old.id} is not current (withdrawn or already superseded)",
        )
    if data.withdrawn and old.value != data.value:
        raise _scope_invalid("a withdrawal repeats the withdrawn row's value")


def _check_no_other_current(conn: Connection, data: IncomeInputCreate) -> None:
    if data.withdrawn:
        return  # ends an answer; adds none
    others = [r for r in _find_current(conn, data) if r.id != data.supersedes_id]
    if others:
        raise RuleViolationError(
            [INCOME_INPUT_NOT_CURRENT],
            f"income input {others[0].id} already answers this; supersede it (supersedes_id)",
        )


def _to_income_input(row: Row) -> IncomeInput:
    return IncomeInput.model_validate(row._asdict())


# --- The tax rate of a year (GET /api/tax-rate) ------------------------------------------


class TaxRateResult(Schema):
    """GET /api/tax-rate: the Steuern screen's "Steuerwirkung" line (§8, §9).

    zve and effect are None together when no income input applies to the year or an earlier
    one (`missing`, the flag income.missing: the screen shows "— · Einkommen eintragen").
    owner_estimates lists each owner's gross pay, deductions (the D84 default or the typed
    override) and estimate from the rows that apply, whatever the year's source, so the
    Eigentümer page can show the computed default deductions under a Bescheid or an override
    too."""

    tax_year: TaxYear
    result: Money
    tariff: TariffStamp
    missing: bool
    zve: ZveEstimate | None = None
    effect: TaxEffect | None = None
    owner_estimates: list[OwnerZve]


def tax_rate(
    conn: Connection,
    *,
    tax_year: int,
    result: Decimal,
    property_id: UUID | None = None,
    today: date,
) -> TaxRateResult:
    """The household base and the tax effect of `result` (the property's taxable result of
    `tax_year`) on the year's tariff (D82, D83). A Bescheid's zvE contains the property's
    result of the Bescheid's own year: `result` for `tax_year`; for a Bescheid carried forward
    from an earlier year, that year's draft result of `property_id`. Without property_id then:
    RuleError income.assessed_result_needed. RuleError tariff.no_file before every tariff.
    Phase 4b D98: the household's return_input rows give the Anlage N summaries and the
    household context (services/return_inputs.tax_contexts)."""
    from .return_inputs import tax_contexts  # it reads this module's income_facts

    t = registry.tariff(tax_year)
    inputs = income_facts(conn)
    owners = owner_profiles(conn)
    employment, household = tax_contexts(conn)(tax_year, result)
    estimate, effect = year_tax_effect(
        tax_year,
        result,
        inputs,
        owners,
        t,
        assessed_result=_assessed(conn, tax_year, result, property_id, today),
        employment=employment,
        household=household,
    )
    return TaxRateResult(
        tax_year=tax_year,
        result=result,
        tariff=TariffStamp(tax_year=t.tax_year, version=t.version, reused_from=t.reused_from),
        missing=estimate is None,
        zve=estimate,
        effect=effect,
        owner_estimates=owner_estimates(tax_year, inputs, owners, t, employment=employment),
    )


def _assessed(
    conn: Connection, tax_year: int, result: Decimal, property_id: UUID | None, today: date
) -> Callable[[int], Decimal]:
    def assessed(year: int) -> Decimal:
        if year == tax_year:
            return result
        if property_id is None:
            raise RuleError(
                INCOME_ASSESSED_RESULT_NEEDED,
                f"the Bescheid of {year} applies to {tax_year}: name the property, so its"
                f" {year} result can be taken out of the assessed zvE",
            )
        try:
            return anlage_v_draft(conn, property_id, tax_year=year, today=today).totals.result
        except RuleError as exc:
            # A Bescheid of a year the property was not owned in (typically the year before
            # the purchase) contains no result of it: exactly 0. tax.no_rule_set for an owned
            # year still fails, as that result is genuinely unknown.
            if exc.code == TAX_YEAR_NOT_AVAILABLE:
                return Decimal("0.00")
            raise

    return assessed
