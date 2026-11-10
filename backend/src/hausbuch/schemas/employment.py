"""Phase 4b: employment income, the whole-return estimate and the yearly questionnaire
(docs/phases/phase-4b.md §4, §5, §7).

Four groups:
- The Anlage N rule set as data (AnlageNRuleSet, AnlageNFormMapping): loaded from
  rules/tax/anlage_n/<year>.yaml by registry.anlage_n. Form lines reuse phase 3's FormLine and
  are keyed by stable line codes; Kennzahlen repeat across the commute blocks, so the key is
  the identity (D99).
- Return inputs (D97): one append-only table `return_input` (migration 0018) with a typed
  `value` per kind, like tax_input (D58) and income_input (D83). Household kinds carry no owner.
- The questionnaire (D110): the year's questions as data (rules/tax/questionnaire/<year>.yaml)
  and the answers, one append-only table `questionnaire_answer` with no free-text column.
- Results of the pure rules (§5): the Anlage N draft, Vorsorge, the assessed tax and the
  whole-return estimate.

Privacy (D96, D103): no identifier anywhere here (no Steuer-ID, no social insurance number, no
birth date of an owner, no IBAN, no address). Free-text labels (an employer, an item, a
recipient) stay in the row's value and never enter an event payload.

Sign conventions: amounts are magnitudes as the form shows them; `result` of an Anlage N draft
and `ReturnEstimate.result` are signed (the estimate's result is positive for a back-payment,
negative for a refund).
"""

from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .common import (
    Area,
    ChildAllowanceCount,
    Fraction,
    Money,
    NonNegativeMoney,
    PositiveMoney,
    Rate,
    Schema,
    TaxYear,
)
from .enums import (
    HOUSEHOLD_KINDS,
    NOT_CARRIED_KINDS,
    QuestionnaireAnswerValue,
    ReturnInputKind,
)

# ChildFact and HouseholdTaxContext live in performance (ZveEstimate carries the context, D98)
# and are re-exported here for the 4b rules.
from .performance import (  # noqa: F401
    ChildFact,
    HouseholdTaxContext,
    IncomeInputFact,
    TariffStamp,
)
from .tax import (
    FORM_MAPPING_INCOMPLETE,
    DraftLine,
    DraftWarning,
    FormLine,
    FormStatus,
    RuleSetStamp,
    SourceRef,
    TaxInputCreate,
    TaxInputKind,
)

# Error codes the schemas raise inside a validation message; rules/errors.py holds the same
# strings with the other phase 4b codes (schemas do not import rules).
RETURN_INPUT_SCOPE_INVALID = "return_input.scope_invalid"
RETURN_INPUT_CARRIED_FROM_INVALID = "return_input.carried_from_invalid"
QUESTIONNAIRE_SCOPE_INVALID = "questionnaire.scope_invalid"
ANLAGE_N_RULE_SET_INVALID = "anlage_n.rule_set_invalid"

ItemClass = Literal["computer", "software", "phone", "furniture", "tools", "subscription", "other"]
ITEM_CLASSES: tuple[str, ...] = ItemClass.__args__  # type: ignore[attr-defined]

# --- The Anlage N rule set as data (§4) ------------------------------------------------


class CommuteRateTier(Schema):
    """§9 Abs. 1 S. 3 Nr. 4 EStG: the rate per full kilometre and day from from_km on."""

    from_km: int = Field(ge=1)  # 2026: one tier from km 1
    rate: PositiveMoney


class UsefulLifeDefault(Schema):
    item_class: ItemClass
    months: int | None = Field(default=None, ge=1)  # None = must be typed (other)
    full_in_year: bool = False  # computer, software (BMF 22 Feb 2022), subscription


# Every line key the Anlage N draft writes (§4.1). A mapping without one of them fails to load.
COMMUTE_ROUTE_FIELDS = (
    "kind",
    "address",
    "period",
    "days_per_week",
    "absent_days",
    "days",
    "km",
    "km_car",
    "km_collective",
    "km_other",
    "public_transport_costs",
)
COMMUTE_ROUTES = 3  # the form's three blocks; more → anlage_n.too_many_routes
ANLAGE_N_LINE_KEYS: tuple[str, ...] = (
    "header.tax_class",
    "pay.gross",
    "pay.income_tax",
    "pay.soli",
    "pay.church_tax",
    "pay.church_tax_spouse",
    # the form's second column: a Steuerklasse-6 certificate (a second job), D99
    "pay.gross_class_6",
    "pay.income_tax_class_6",
    "pay.soli_class_6",
    "pay.church_tax_class_6",
    "pay.church_tax_spouse_class_6",
    "pay.replacement_benefits",
    *(
        f"commute.route_{r}.{field}"
        for r in range(1, COMMUTE_ROUTES + 1)
        for field in COMMUTE_ROUTE_FIELDS
    ),
    "commute.employer_tax_free",
    "commute.employer_flat_taxed",
    "commute.agency_subsidy",
    "dues.associations",
    "equipment.items",
    "equipment.total",
    "work_room.amount",
    "home_office.days_other_workplace",
    "home_office.days_no_other_workplace",
    "training.total",
    "other.ferry_flight",
    "other.items",
    "other.total",
    "wk.itemised",
    "wk.allowance",
    "wk.applied",
    "result",
)


class AnlageNFormMapping(Schema):
    """Line key → Zeile/Kennzahl for one form year of the Anlage N. No Kennzahl uniqueness
    (D99): the same Kz may stand on two lines of one block."""

    form_year: TaxYear
    status: FormStatus
    version_stamp: str = Field(min_length=1)  # "2025AnlN031NET – Oktober 2025"
    source_url: str = Field(min_length=1)
    lines: dict[str, FormLine]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        problems = []
        absent = [k for k in ANLAGE_N_LINE_KEYS if k not in self.lines]
        if absent:
            problems.append(f"lines the draft writes are missing: {', '.join(absent)}")
        misnamed = [k for k, line in self.lines.items() if line.key != k]
        if misnamed:
            problems.append(f"line keys differ from their entry: {', '.join(misnamed)}")
        if problems:
            raise ValueError(f"{FORM_MAPPING_INCOMPLETE}: {'; '.join(problems)}")
        return self


class AnlageNRuleSet(Schema):
    tax_year: TaxYear
    version: str = Field(min_length=1)
    commute_rates: list[CommuteRateTier] = Field(min_length=1)  # §9 Abs. 1 S. 3 Nr. 4
    commute_cap_non_car: PositiveMoney = Decimal("4500.00")
    home_office_day_rate: PositiveMoney = Decimal("6.00")  # §4 Abs. 5 S. 1 Nr. 6c
    home_office_cap: PositiveMoney = Decimal("1260.00")
    work_room_flat: PositiveMoney = Decimal("1260.00")  # §4 Abs. 5 S. 1 Nr. 6b S. 3, D106
    gwg_limit_net: PositiveMoney = Decimal("800.00")  # §6 Abs. 2 S. 1
    default_vat_rate: Rate = Decimal("0.19")
    useful_lives: list[UsefulLifeDefault]  # one per item class (Q-O21: phone 60, not full)
    telecom_share: Rate = Decimal("0.20")  # R 9.1 Abs. 5 S. 4 LStR, D112
    telecom_cap_monthly: PositiveMoney = Decimal("20.00")
    account_fee_flat: PositiveMoney = Decimal("16.00")  # practice, H 9.1 LStH
    union_dues_beside_allowance: bool  # §9a S. 3 EStG, true from 2026
    other_vorsorge_cap_employee: PositiveMoney = Decimal("1900.00")  # §10 Abs. 4 S. 2
    other_vorsorge_cap_self_paying: PositiveMoney = Decimal("2800.00")  # §10 Abs. 4 S. 1
    pension_max_contribution: PositiveMoney  # §10 Abs. 3 S. 1
    weekdays_in_year: int = Field(ge=250, le=262)  # D100 (2) plausibility
    form: AnlageNFormMapping

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems = []
        if self.form.status is FormStatus.PUBLISHED and self.form.form_year != self.tax_year:
            problems.append("a published form mapping is the tax year's own form")
        if self.form.status is FormStatus.PROVISIONAL and self.form.form_year >= self.tax_year:
            problems.append("a provisional mapping copies an earlier year's form")
        tiers = [t.from_km for t in self.commute_rates]
        if tiers[0] != 1 or tiers != sorted(set(tiers)):
            problems.append("commute_rates start at km 1, from_km strictly increasing")
        classes = [u.item_class for u in self.useful_lives]
        if sorted(classes) != sorted(ITEM_CLASSES):
            problems.append("useful_lives names every item class exactly once")
        for u in self.useful_lives:
            if u.item_class == "subscription" and not u.full_in_year:
                problems.append("a subscription is a running cost (full_in_year)")
            if u.months is None and not u.full_in_year and u.item_class != "other":
                problems.append(f"{u.item_class}: a default life in months")
        if self.other_vorsorge_cap_employee > self.other_vorsorge_cap_self_paying:
            problems.append("the employee cap is the smaller one (§10 Abs. 4)")
        if problems:
            raise ValueError(f"{ANLAGE_N_RULE_SET_INVALID}: {'; '.join(problems)}")
        return self

    def useful_life(self, item_class: str) -> UsefulLifeDefault:
        return next(u for u in self.useful_lives if u.item_class == item_class)


# --- Return inputs (D97, §6, §7) -------------------------------------------------------

CERTIFICATE_AMOUNTS: tuple[str, ...] = (
    "gross",  # Nr. 3
    "income_tax",  # Nr. 4
    "soli",  # Nr. 5
    "church_tax",  # Nr. 6
    "church_tax_spouse",  # Nr. 7
    "replacement_benefits",  # Nr. 15
    "short_time_allowance",  # Nr. 15a (informational)
    "employer_commute_tax_free",  # Nr. 17
    "employer_commute_flat_taxed",  # Nr. 18
    "pension_employer",  # Nr. 22a
    "pension_employee",  # Nr. 23a
    "pension_employee_professional",  # Nr. 23b
    "subsidy_health",  # Nr. 24a
    "subsidy_private_health",  # Nr. 24b
    "subsidy_care",  # Nr. 24c
    "health_employee",  # Nr. 25
    "care_employee",  # Nr. 26
    "unemployment_employee",  # Nr. 27
)
_ZERO = Decimal("0")


class WageCertificate(Schema):
    """The confirmed figures of one Lohnsteuerbescheinigung (§5.1). A line the model did not
    read is 0 with its field listed under `unread` (the review lets the user type it)."""

    period_from: date
    period_to: date
    employer: str = Field(min_length=1, max_length=120)  # the name only
    tax_class: int = Field(ge=1, le=6)
    # "Steuerklasse/Faktor" is read as the class and this flag: IV with a Faktor (§39f), a
    # filing reason of §46 Abs. 2 Nr. 3a; a yes/no, never in an event's amounts
    tax_class_factor: bool = False
    church_tax_marked: bool = False
    child_allowance_count: ChildAllowanceCount = _ZERO
    gross: NonNegativeMoney = _ZERO
    income_tax: NonNegativeMoney = _ZERO
    soli: NonNegativeMoney = _ZERO
    church_tax: NonNegativeMoney = _ZERO
    church_tax_spouse: NonNegativeMoney = _ZERO
    replacement_benefits: NonNegativeMoney = _ZERO
    short_time_allowance: NonNegativeMoney = _ZERO
    employer_commute_tax_free: NonNegativeMoney = _ZERO
    employer_commute_flat_taxed: NonNegativeMoney = _ZERO
    pension_employer: NonNegativeMoney = _ZERO
    pension_employee: NonNegativeMoney = _ZERO
    pension_employee_professional: NonNegativeMoney = _ZERO
    subsidy_health: NonNegativeMoney = _ZERO
    subsidy_private_health: NonNegativeMoney = _ZERO
    subsidy_care: NonNegativeMoney = _ZERO
    health_employee: NonNegativeMoney = _ZERO
    care_employee: NonNegativeMoney = _ZERO
    unemployment_employee: NonNegativeMoney = _ZERO
    unread: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.period_from > self.period_to or self.period_from.year != self.period_to.year:
            raise ValueError("the certificate's period lies within one year, from before to")
        unknown = [f for f in self.unread if f not in CERTIFICATE_AMOUNTS]
        if unknown:
            raise ValueError(f"unread names certificate amounts only: {', '.join(unknown)}")
        if self.short_time_allowance > self.replacement_benefits:
            raise ValueError("Nr. 15a is part of Nr. 15")
        if self.tax_class_factor and self.tax_class != 4:
            raise ValueError("a Faktor stands beside Steuerklasse IV only (§39f)")
        return self


class MonthAndYtd(Schema):
    month: NonNegativeMoney = _ZERO
    ytd: NonNegativeMoney | None = None  # None: the Jahreswerte column was not read


PAYSLIP_FIGURES: tuple[str, ...] = (
    "gross",  # Steuer-Brutto, not Gesamt-Brutto
    "income_tax",
    "soli",
    "church_tax",
    "pension_employee",
    "health_employee",
    "care_employee",
    "unemployment_employee",
    "tax_free_benefits",
)


def employer_key(name: str) -> str:
    """The employer as compared (D105): case folded, whitespace runs collapsed to one space,
    stripped, so "Muster GmbH" and "MUSTER  GMBH" are one employer. Only for comparing and
    grouping; rows keep the spelling as read."""
    return " ".join(name.casefold().split())


class Payslip(Schema):
    """One month's payslip of one employer (D105). months_remaining_paid defaults to
    12 − month in the review; fewer when pay stops before December."""

    period_month: date  # the first of the month
    employer: str = Field(min_length=1, max_length=120)
    tax_class: int = Field(ge=1, le=6)
    tax_class_factor: bool = False  # IV with a Faktor (§39f), as on the certificate
    months_remaining_paid: int = Field(ge=0, le=11)
    gross: MonthAndYtd = MonthAndYtd()
    income_tax: MonthAndYtd = MonthAndYtd()
    soli: MonthAndYtd = MonthAndYtd()
    church_tax: MonthAndYtd = MonthAndYtd()
    pension_employee: MonthAndYtd = MonthAndYtd()
    health_employee: MonthAndYtd = MonthAndYtd()
    care_employee: MonthAndYtd = MonthAndYtd()
    unemployment_employee: MonthAndYtd = MonthAndYtd()
    tax_free_benefits: MonthAndYtd = MonthAndYtd()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.period_month.day != 1:
            raise ValueError("period_month is the first of the month")
        if self.months_remaining_paid > 12 - self.period_month.month:
            raise ValueError("months_remaining_paid cannot run past December")
        if self.tax_class_factor and self.tax_class != 4:
            raise ValueError("a Faktor stands beside Steuerklasse IV only (§39f)")
        return self


def _floor_km(value: Any) -> Any:
    """A typed decimal distance is floored to full kilometres (§9 Abs. 1 S. 3 Nr. 4)."""
    if isinstance(value, str):
        try:
            return int(Decimal(value).to_integral_value(ROUND_FLOOR))
        except InvalidOperation:
            return value
    return value


class CommuteFact(Schema):
    """One route for one period (§5.2). km_car + km_collective + km_other = km; a decimal
    distance typed as a string ("25.9") is floored to full km."""

    kind: Literal["first_workplace", "collection_point"]
    workplace_label: str = Field(min_length=1, max_length=120)
    period_from: date
    period_to: date
    days_per_week: int = Field(ge=1, le=7)
    # the form's "Urlaubs-, Krankheits-, Heimarbeits- und Dienstreisetage" (no Kz; informational)
    absent_days: int = Field(default=0, ge=0, le=366)
    days: int = Field(ge=0, le=366)
    km: int = Field(ge=1, le=1000)
    km_car: int = Field(default=0, ge=0)
    km_collective: int = Field(default=0, ge=0)
    km_other: int = Field(default=0, ge=0)
    public_transport_costs: NonNegativeMoney | None = None

    @field_validator("km", "km_car", "km_collective", "km_other", mode="before")
    @classmethod
    def _floored(cls, value: Any) -> Any:
        return _floor_km(value)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.km_car + self.km_collective + self.km_other != self.km:
            raise ValueError("km_car + km_collective + km_other = km")
        if self.period_from > self.period_to or self.period_from.year != self.period_to.year:
            raise ValueError("a route's period lies within one year, from before to")
        return self


class HomeOfficeFact(Schema):
    days_other_workplace: int = Field(default=0, ge=0, le=366)  # Z58
    days_no_other_workplace: int = Field(default=0, ge=0, le=366)  # Z59


class WorkRoomFact(Schema):
    """The häusliches Arbeitszimmer (D106): Jahrespauschale or actual costs by the m² share."""

    months: int = Field(ge=1, le=12)
    room_m2: Area
    home_m2: Area
    rent_year: NonNegativeMoney
    running_costs_year: NonNegativeMoney = _ZERO
    room_only_costs_year: NonNegativeMoney = _ZERO
    prefer: Literal["flat", "actual"] | None = None

    @model_validator(mode="after")
    def _room_in_home(self) -> Self:
        if self.room_m2 >= self.home_m2:
            raise ValueError("the room is smaller than the home")
        return self


class EquipmentFact(Schema):
    """One work item or subscription (D108). bought_on may lie before the tax year."""

    label: str = Field(min_length=1, max_length=120)
    item_class: ItemClass
    gross: PositiveMoney
    net: PositiveMoney | None = None  # typed from the invoice; else gross ÷ 1.19
    bought_on: date
    disposed_on: date | None = None
    professional_share: Fraction = Fraction(numerator=1, denominator=1)
    useful_life_months: int | None = Field(default=None, ge=1, le=600)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.net is not None and self.net > self.gross:
            raise ValueError("net is at most gross")
        if self.disposed_on is not None and self.disposed_on < self.bought_on:
            raise ValueError("disposed_on lies after bought_on")
        return self


class TelecomFact(Schema):
    bills_year: NonNegativeMoney
    months: int = Field(default=12, ge=1, le=12)
    employer_reimbursed: NonNegativeMoney = _ZERO


class AssociationFact(Schema):
    name: str = Field(min_length=1, max_length=120)
    amount: NonNegativeMoney
    is_union: bool = False


class TrainingFact(Schema):
    label: str = Field(min_length=1, max_length=120)
    amount: NonNegativeMoney


class OtherExpenseFact(Schema):
    expense_class: Literal["account_fee", "application", "ferry_flight", "other"]
    label: str = Field(min_length=1, max_length=120)
    amount: NonNegativeMoney | None = None  # None only for account_fee (the 16.00 flat)

    @model_validator(mode="after")
    def _amount(self) -> Self:
        if self.amount is None and self.expense_class != "account_fee":
            raise ValueError("only the account fee may leave its amount to the flat rate")
        return self


class OtherInsuranceFact(Schema):
    amount: NonNegativeMoney  # §10 Abs. 1 Nr. 3a premiums beyond health, care and AV


class ReplacementFact(Schema):
    """A benefit paid by an authority (D102); the employer's Zuschuss is on the certificate."""

    benefit: Literal[
        "elterngeld",
        "mutterschaftsgeld",
        "krankengeld",
        "arbeitslosengeld",
        "kurzarbeitergeld",
        "other",
    ]
    amount: NonNegativeMoney
    period_from: date | None = None
    period_to: date | None = None

    @model_validator(mode="after")
    def _period(self) -> Self:
        if self.period_from and self.period_to and self.period_from > self.period_to:
            raise ValueError("period_from lies before period_to")
        return self


class VorsorgeOverride(Schema):
    pension: NonNegativeMoney
    health_care_basis: NonNegativeMoney
    other: NonNegativeMoney
    krankengeld_entitlement: bool = True


class ChildcareFact(Schema):
    child_id: UUID  # the child's return_input row
    amount: NonNegativeMoney
    paid_to_account: bool


class HouseholdServicesFact(Schema):
    service_class: Literal["handwerker", "haushaltsnah", "minijob"]
    labour_cost: NonNegativeMoney
    paid_to_account: bool  # §35a Abs. 5 S. 3: required true (return.household_services_cash)
    source: Literal["own_invoice", "nebenkostenabrechnung"]


class SpecialExpenseFact(Schema):
    expense_class: Literal["donation"]
    amount: NonNegativeMoney
    recipient_label: str = Field(min_length=1, max_length=120)


ReturnInputValue = (
    WageCertificate
    | Payslip
    | CommuteFact
    | HomeOfficeFact
    | WorkRoomFact
    | EquipmentFact
    | TelecomFact
    | AssociationFact
    | TrainingFact
    | OtherExpenseFact
    | OtherInsuranceFact
    | ReplacementFact
    | VorsorgeOverride
    | ChildFact
    | ChildcareFact
    | HouseholdServicesFact
    | SpecialExpenseFact
)

R = ReturnInputKind
RETURN_INPUT_VALUES: dict[ReturnInputKind, type[Schema]] = {
    R.WAGE_CERTIFICATE: WageCertificate,
    R.PAYSLIP: Payslip,
    R.COMMUTE: CommuteFact,
    R.HOME_OFFICE: HomeOfficeFact,
    R.WORK_ROOM: WorkRoomFact,
    R.WORK_EQUIPMENT: EquipmentFact,
    R.TELECOM: TelecomFact,
    R.PROFESSIONAL_ASSOCIATION: AssociationFact,
    R.TRAINING: TrainingFact,
    R.OTHER_EXPENSE: OtherExpenseFact,
    R.OTHER_INSURANCE: OtherInsuranceFact,
    R.REPLACEMENT_INCOME: ReplacementFact,
    R.VORSORGE_OVERRIDE: VorsorgeOverride,
    R.CHILD: ChildFact,
    R.CHILDCARE_COST: ChildcareFact,
    R.HOUSEHOLD_SERVICES: HouseholdServicesFact,
    R.SPECIAL_EXPENSE: SpecialExpenseFact,
}
# A document required: the certificate and the payslip are their documents (D96, D105).
DOCUMENT_REQUIRED = frozenset({R.WAGE_CERTIFICATE, R.PAYSLIP})
del R


def return_value_by_kind(data: Any) -> Any:
    """Parse `value` by `kind`: several kinds share a shape ({amount}), so a plain union would
    guess. Used by ReturnInputCreate and the return_input_recorded payloads."""
    if isinstance(data, dict) and isinstance(data.get("value"), dict):
        try:
            model = RETURN_INPUT_VALUES[ReturnInputKind(data.get("kind"))]
        except ValueError:
            return data  # an unknown kind fails on its own field
        return data | {"value": model.model_validate(data["value"])}
    return data


# Decimal fields of a value that are not money (they never enter an event's amounts).
_NOT_MONEY = frozenset({"child_allowance_count", "room_m2", "home_m2"})


def return_input_amounts(value: Schema) -> dict[str, Decimal]:
    """The money fields of a return input value, by name, for the event payload (§6): a
    payslip's pairs as "gross.month" / "gross.ytd"; None (an unread ytd, an account fee left to
    the flat) is left out. Never a label, an employer, a date or a share."""
    amounts: dict[str, Decimal] = {}
    for name in type(value).model_fields:
        v = getattr(value, name)
        if isinstance(v, Decimal) and name not in _NOT_MONEY:
            amounts[name] = v
        elif isinstance(v, MonthAndYtd):
            amounts[f"{name}.month"] = v.month
            if v.ytd is not None:
                amounts[f"{name}.ytd"] = v.ytd
    return amounts


def carried_allowed(kind: ReturnInputKind, value: Schema) -> bool:
    """Whether a row of this kind may be confirmed from last year's (D110): the yearly kinds,
    and of work_equipment only a subscription (a yearly item); never a spanning item or a
    year's document."""
    if kind in NOT_CARRIED_KINDS or kind is ReturnInputKind.CHILD:
        return False
    if kind is ReturnInputKind.WORK_EQUIPMENT:
        return isinstance(value, EquipmentFact) and value.item_class == "subscription"
    return True


class ReturnInputCreate(Schema):
    """One return input row as written. A correction is a new row with supersedes_id (a current
    row of the same kind and year, and the same owner); withdrawn = true ends it. Rows are
    never changed."""

    owner_id: UUID | None = None  # None exactly for the household kinds
    kind: ReturnInputKind
    tax_year: TaxYear  # the year; for a spanning kind the first year it counts
    document_id: UUID | None = None
    value: ReturnInputValue
    note: str | None = Field(default=None, max_length=500)
    supersedes_id: UUID | None = None
    carried_from_id: UUID | None = None  # D110: the Y−1 row this one was confirmed from
    withdrawn: bool = False

    @model_validator(mode="before")
    @classmethod
    def _value_by_kind(cls, data: Any) -> Any:
        return return_value_by_kind(data)

    @model_validator(mode="after")
    def _scope(self) -> Self:
        k = self.kind
        problems = []
        if type(self.value) is not RETURN_INPUT_VALUES[k]:
            problems.append("value does not match the kind")
        if (self.owner_id is None) != (k in HOUSEHOLD_KINDS):
            problems.append("owner_id is set exactly for the owner kinds")
        if k in DOCUMENT_REQUIRED and self.document_id is None:
            problems.append("a certificate or payslip row needs its document")
        if self.withdrawn and self.supersedes_id is None:
            problems.append("a withdrawal supersedes the row it withdraws")
        v = self.value
        if isinstance(v, WageCertificate) and v.period_from.year != self.tax_year:
            problems.append("the certificate's period lies in tax_year")
        if isinstance(v, Payslip) and v.period_month.year != self.tax_year:
            problems.append("the payslip's month lies in tax_year")
        if isinstance(v, CommuteFact) and v.period_from.year != self.tax_year:
            problems.append("the route's period lies in tax_year")
        if problems:
            raise ValueError(f"{RETURN_INPUT_SCOPE_INVALID}: {'; '.join(problems)}")
        if self.carried_from_id is not None and not carried_allowed(k, self.value):
            raise ValueError(
                f"{RETURN_INPUT_CARRIED_FROM_INVALID}: only a yearly kind is confirmed from last"
                " year's row (never a document, a child or an item with a useful life)"
            )
        return self


class ReturnInputFact(ReturnInputCreate):
    """A stored row as the rules see it (every row of the history, as TaxInputFact)."""

    id: UUID


class ReturnInput(ReturnInputFact):
    """API read model."""

    workspace_id: UUID
    recorded_at: datetime
    recorded_by: UUID | None = None


class OwnerReturnFacts(Schema):
    """Everything draft_n and vorsorge read for one owner and year: the owner's rows (yearly
    kinds of the year, spanning kinds up to it), the expected-pay row as the last fallback,
    and the owner's church tax flag (anlage_n.church_mismatch)."""

    owner_id: UUID
    church_tax: bool = False
    inputs: list[ReturnInputFact] = Field(default_factory=list)
    expected_gross_pay: IncomeInputFact | None = None

    @model_validator(mode="after")
    def _owner(self) -> Self:
        if any(i.owner_id != self.owner_id for i in self.inputs):
            raise ValueError("every input is the owner's own (no household kind)")
        if self.expected_gross_pay is not None and (
            self.expected_gross_pay.owner_id != self.owner_id
        ):
            raise ValueError("the expected-pay row is the owner's")
        return self


# --- The questionnaire (D110, §4.2) ----------------------------------------------------

QuestionScope = Literal["owner", "household", "property"]
QUESTION_KEY_PATTERN = r"^(owner|household|property)\.[a-z0-9_]{1,52}$"
CHOICE_CODE_PATTERN = r"^[a-z0-9_]{1,32}$"


class Choice(Schema):
    code: str = Field(pattern=CHOICE_CODE_PATTERN)
    label_de: str = Field(min_length=1)


class QuestionTarget(Schema):
    """What a "yes" leads to: rows of a return_input or tax_input kind, or a reminder printed on
    the Gesamt card and the sheet. value_match selects the target rows by their value's coded
    fields (e.g. {"item_class": "subscription"}, {"expense_class": "account_fee"}), so two
    questions can share one kind (spec §7 addition, 10 Nov 2026)."""

    kind: Literal["return_input", "tax_input", "reminder"]
    input_kind: ReturnInputKind | TaxInputKind | None = None
    value_match: dict[str, str] = Field(default_factory=dict)
    reminder_de: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.kind == "reminder":
            if not self.reminder_de or self.input_kind is not None or self.value_match:
                raise ValueError("a reminder has its sentence and no input kind")
        else:
            expected = ReturnInputKind if self.kind == "return_input" else TaxInputKind
            if not isinstance(self.input_kind, expected) or self.reminder_de is not None:
                raise ValueError(f"a {self.kind} target names a {expected.__name__} member")
        return self


class Question(Schema):
    key: str = Field(pattern=QUESTION_KEY_PATTERN)  # stable across years
    section: Literal["einkommen", "werbungskosten", "haushalt", "kinder", "immobilie"]
    scope: QuestionScope
    question_de: str = Field(min_length=1)
    why_de: str = Field(min_length=1)
    answer_type: Literal["yes_no", "choice", "facts"]
    choices: list[Choice] | None = None
    targets: list[QuestionTarget] = Field(default_factory=list)
    carry_forward: Literal["copy", "reconfirm", "none"]
    default_answer: Literal["yes", "no"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems = []
        if self.key.split(".", 1)[0] != self.scope:
            problems.append("the key starts with its scope")
        if self.answer_type == "choice" and not self.choices:
            problems.append("a choice question lists its choices")
        if self.answer_type == "yes_no" and self.choices:
            problems.append("a yes/no question has no choices")
        if self.choices and len({c.code for c in self.choices}) != len(self.choices):
            problems.append("choice codes are unique")
        for t in self.targets:
            if t.kind == "tax_input" and self.scope != "property":
                problems.append("a tax_input target belongs to a property question")
            if t.kind == "return_input":
                household = t.input_kind in HOUSEHOLD_KINDS
                if household != (self.scope == "household"):
                    problems.append(f"{t.input_kind}: its scope is the question's")
        if self.carry_forward != "none" and not any(t.kind != "reminder" for t in self.targets):
            problems.append("only a question with rows carries forward")
        if problems:
            raise ValueError(f"{self.key}: {'; '.join(problems)}")
        return self


class Questionnaire(Schema):
    """The year's questions (rules/tax/questionnaire/<year>.yaml). reused_from: set by
    registry.questionnaire when the year has no file of its own (D81's fallback); the screen
    then shows questionnaire_provisional."""

    tax_year: TaxYear
    version: str = Field(min_length=1)
    reused_from: TaxYear | None = None
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        keys = [q.key for q in self.questions]
        if len(set(keys)) != len(keys):
            raise ValueError("question keys are unique")
        if self.reused_from is not None and self.reused_from >= self.tax_year:
            raise ValueError("reused_from is an earlier year")
        return self

    def question(self, key: str) -> Question | None:
        return next((q for q in self.questions if q.key == key), None)


class QuestionnaireAnswerCreate(Schema):
    """One answer (D110). No free-text column: choice is a coded option. The question's scope
    decides which id is set (owner. → owner_id, property. → property_id, household. → none);
    the service checks the key against the year's file."""

    question_key: str = Field(pattern=QUESTION_KEY_PATTERN, max_length=64)
    tax_year: TaxYear
    owner_id: UUID | None = None
    property_id: UUID | None = None
    answer: QuestionnaireAnswerValue
    choice: str | None = Field(default=None, pattern=CHOICE_CODE_PATTERN)
    supersedes_id: UUID | None = None
    carried_from_id: UUID | None = None
    withdrawn: bool = False

    @model_validator(mode="after")
    def _scope(self) -> Self:
        scope = self.question_key.split(".", 1)[0]
        problems = []
        if (self.owner_id is not None) != (scope == "owner"):
            problems.append("owner_id is set exactly for an owner question")
        if (self.property_id is not None) != (scope == "property"):
            problems.append("property_id is set exactly for a property question")
        if self.withdrawn and self.supersedes_id is None:
            problems.append("a withdrawal supersedes the answer it withdraws")
        if problems:
            raise ValueError(f"{QUESTIONNAIRE_SCOPE_INVALID}: {'; '.join(problems)}")
        return self


class QuestionnaireAnswer(QuestionnaireAnswerCreate):
    id: UUID
    workspace_id: UUID
    recorded_at: datetime
    recorded_by: UUID | None = None


class QuestionPrefill(Schema):
    """One answer slot of the year with what last year leaves (§5.11). proposed_answer is the
    prior answer carried, or the question's default_answer when there is none (spec §7
    addition, 10 Nov 2026)."""

    key: str = Field(pattern=QUESTION_KEY_PATTERN)
    scope: QuestionScope
    owner_id: UUID | None = None
    property_id: UUID | None = None
    prior_answer: QuestionnaireAnswer | None = None
    proposed_answer: Literal["yes", "no"] | None = None
    proposed_rows: list[ReturnInputCreate | TaxInputCreate] = Field(default_factory=list)
    reconfirm_rows: list[ReturnInputFact] = Field(default_factory=list)


class OpenQuestion(Schema):
    key: str = Field(pattern=QUESTION_KEY_PATTERN)
    scope: QuestionScope
    owner_id: UUID | None = None
    property_id: UUID | None = None


class QuestionnaireProgress(Schema):
    answered: int = Field(ge=0)
    total: int = Field(ge=0)
    open: list[OpenQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _counts(self) -> Self:
        if self.answered + len(self.open) != self.total:
            raise ValueError("answered + open = total slots")
        return self


# --- Rule results (§5) -----------------------------------------------------------------


class AnlageNDraft(Schema):
    """One owner's Anlage N for one year (§5.6). status: certified (from certificates),
    estimate (payslip projection or expected pay), none (no pay source)."""

    owner_id: UUID
    tax_year: TaxYear
    status: Literal["certified", "estimate", "none"]
    gross_source: Literal["certificate", "payslip", "expected"] | None = None
    lines: list[DraftLine] = Field(default_factory=list)
    gross: NonNegativeMoney
    wk_itemised: NonNegativeMoney
    wk_union: NonNegativeMoney = _ZERO
    wk_allowance: NonNegativeMoney
    wk_applied: NonNegativeMoney
    result: Money
    replacement_benefits: NonNegativeMoney = _ZERO  # Σ Nr. 15 (Z20)
    warnings: list[DraftWarning] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    form: RuleSetStamp

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        if self.result != self.gross - self.wk_applied:
            raise ValueError("result = gross − wk_applied")
        if (self.status == "none") != (self.gross_source is None):
            raise ValueError("a draft with pay names its source")
        return self


class VorsorgeResult(Schema):
    owner_id: UUID
    pension: NonNegativeMoney
    health_care_basis: NonNegativeMoney
    other: NonNegativeMoney
    deductible: NonNegativeMoney
    source: Literal["certificate", "payslip", "override", "expected", "none"]
    sources: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sum(self) -> Self:
        if self.deductible != self.pension + self.health_care_basis + self.other:
            raise ValueError("deductible = pension + health_care_basis + other")
        return self


class OwnerEmployment(Schema):
    """What phase 4's zvE estimate takes from 4b per owner (D98)."""

    owner_id: UUID
    gross: NonNegativeMoney
    gross_source: Literal["certificate", "payslip", "expected"]
    wk_applied: NonNegativeMoney
    vorsorge: NonNegativeMoney
    sources: list[SourceRef] = Field(default_factory=list)


class ProgressionInfo(Schema):
    """§32b (D102): the special rate is income_tax(zvE + L) ÷ (zvE + L), truncated to four
    decimals of the percentage (six of the fraction, H 32b EStH 2025 Fall B: 6,8648 %), and
    applied to the zvE with the tax floored to whole euros."""

    benefits: NonNegativeMoney  # L after the §32b Abs. 2 reduction
    allowance_unused: NonNegativeMoney  # the Pauschbetrag part not used against pay
    rate: Rate
    fictitious_zve: NonNegativeMoney  # zvE + L
    fictitious_tax: NonNegativeMoney  # income_tax(zvE + L)


class ChildrenResult(Schema):
    months: int = Field(ge=0)  # Σ months over the children
    kindergeld: NonNegativeMoney
    allowances: NonNegativeMoney
    saving: Money  # est_without − est_with
    favourable: Literal["kindergeld", "allowances"]


class AssessedTax(Schema):
    """§5.10: the tax as assessed. est = est_with + kindergeld when the allowances win, else
    est_without; Soli and church tax on est_with minus the §35a credit (§51a Abs. 2 S. 1)."""

    zve: NonNegativeMoney
    zve_after_allowances: NonNegativeMoney
    est_without: NonNegativeMoney
    est_with: NonNegativeMoney
    est: NonNegativeMoney
    credit_35a: NonNegativeMoney = _ZERO
    est_final: NonNegativeMoney
    progression: ProgressionInfo | None = None
    children: ChildrenResult | None = None
    soli: NonNegativeMoney
    church: NonNegativeMoney
    total: NonNegativeMoney

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        if self.est_final != self.est - self.credit_35a:
            raise ValueError("est_final = est − credit_35a")
        if self.total != self.est_final + self.soli + self.church:
            raise ValueError("total = est_final + soli + church")
        return self


class FilingCheck(Schema):
    """§46 Abs. 2 EStG. deadline: 31 July of the following year (§149 Abs. 2 S. 1 AO), moved
    to the next working day when that is a Saturday, Sunday or a nationwide public holiday
    (§108 Abs. 3 AO); computed by rule, never typed. Regional holidays are not considered."""

    required: bool
    reasons: list[str] = Field(default_factory=list)
    deadline: date


class OwnerIncome(Schema):
    owner_id: UUID
    result: Money  # the Anlage N result
    status: Literal["certified", "estimate", "none"]


class PropertyIncome(Schema):
    property_id: UUID
    result: Money  # the Anlage V result


class Withheld(Schema):
    income_tax: NonNegativeMoney = _ZERO
    soli: NonNegativeMoney = _ZERO
    church_tax: NonNegativeMoney = _ZERO
    total: NonNegativeMoney = _ZERO

    @model_validator(mode="after")
    def _sum(self) -> Self:
        if self.total != self.income_tax + self.soli + self.church_tax:
            raise ValueError("total = income_tax + soli + church_tax")
        return self


class MissingItem(Schema):
    """Something the estimate still waits for: an open question slot, a document, or a
    benefit letter answered "yes, pending"."""

    kind: Literal["question", "document", "benefit_letter"]
    key: str | None = None  # the question key, or the return input kind
    owner_id: UUID | None = None
    property_id: UUID | None = None


class ReturnEstimate(Schema):
    """The whole-return estimate (§5.8, D101). result = assessed.total − withheld.total:
    positive is a back-payment, negative a refund."""

    tax_year: TaxYear
    status: Literal["estimate", "certified"]
    income_n: list[OwnerIncome] = Field(default_factory=list)
    income_v: list[PropertyIncome] = Field(default_factory=list)
    total_income: Money
    vorsorge_total: NonNegativeMoney
    church_tax_paid: NonNegativeMoney = _ZERO
    donations: NonNegativeMoney = _ZERO
    childcare: NonNegativeMoney = _ZERO
    special_expenses: NonNegativeMoney
    zve: NonNegativeMoney
    assessed: AssessedTax
    withheld: Withheld
    result: Money
    filing_required: FilingCheck
    questionnaire: QuestionnaireProgress
    missing: list[MissingItem] = Field(default_factory=list)
    not_modelled: list[str] = Field(default_factory=list)
    reminders: list[str] = Field(default_factory=list)
    warnings: list[DraftWarning] = Field(default_factory=list)
    tariff: TariffStamp
    sources: list[SourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _arithmetic(self) -> Self:
        problems = []
        parts = sum((i.result for i in self.income_n), _ZERO) + sum(
            (i.result for i in self.income_v), _ZERO
        )
        if (self.income_n or self.income_v) and parts != self.total_income:
            problems.append("total_income = Σ Anlage N + Σ Anlage V results")
        if self.result != self.assessed.total - self.withheld.total:
            problems.append("result = assessed.total − withheld.total")
        if self.assessed.zve != self.zve:
            problems.append("the assessed zvE is the estimate's")
        if problems:
            raise ValueError("; ".join(problems))
        return self
