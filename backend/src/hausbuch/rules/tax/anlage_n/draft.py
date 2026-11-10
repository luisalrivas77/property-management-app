"""§5.6: one owner's Anlage N draft for one year (`draft_n`).

Pay: the year's certificates (`certified`), else the payslip projection of the employers without
one (`estimate`, D105), else the owner's `expected_gross_pay` row (`estimate`, warning
`anlage_n.certificate_missing`), else RuleError `anlage_n.no_pay`. Werbungskosten itemised =
commute + Tagespauschale + work room + equipment + telecom + dues (all) + training + other;
applied = union + max(min(Pauschbetrag, gross), itemised − union) while union dues stand beside
the Pauschbetrag (§9a S. 3 from 2026), else max(min(Pauschbetrag, gross), itemised); result =
gross − applied.

Lines (by key, the form's two pay columns D99): the plain pay lines sum the Steuerklasse-1–5
certificates, the `_class_6` lines the Steuerklasse-6 ones; `pay.gross`, `wk.itemised`,
`wk.allowance`, `wk.applied` always, the others when not zero; the route lines for the first
three routes; `home_office.days_*` carry day counts. Every line names its rows (principle 2).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from hausbuch.rules.errors import ANLAGE_N_NO_PAY, RuleError
from hausbuch.rules.tax._common import unique, warning
from hausbuch.rules.tax.rounding import whole_euros
from hausbuch.schemas.employment import (
    COMMUTE_ROUTES,
    AmountItem,
    AnlageNDraft,
    AnlageNRuleSet,
    CommuteComponent,
    CommuteRouteDetail,
    EquipmentComponent,
    EquipmentItemDetail,
    HomeOfficeComponent,
    OwnerReturnFacts,
    ReturnInputFact,
    SumComponent,
    TelecomComponent,
    WageCertificate,
    WerbungskostenComponents,
    WorkRoomComponent,
)
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import Tariff
from hausbuch.schemas.tax import (
    DraftLine,
    DraftWarning,
    FormStatus,
    RuleSetStamp,
    SourceRef,
    WarningCode,
)

from ._base import ZERO, cents, in_year, row_sources
from .commute import CommuteResult, commute_lines
from .equipment import EquipmentSlice, equipment_slice
from .home_office import HomeOfficeResult, home_office_result
from .other import Dues, dues_lines, other_expense_items, training_lines
from .payslip import PaySources, pay_of_rows
from .telecom import TelecomResult, telecom_result
from .work_room import WorkRoomResult, work_room_line

RULE_SET_SOURCE = SourceRef(kind="anlage_n_rule_set")
TARIFF_SOURCE = SourceRef(kind="tariff")


@dataclass
class Components:
    """The itemised Werbungskosten by block (§5.6)."""

    commute: Decimal = ZERO
    home_office: Decimal = ZERO
    work_room: Decimal = ZERO
    equipment: Decimal = ZERO
    telecom: Decimal = ZERO
    dues: Decimal = ZERO
    training: Decimal = ZERO
    other: Decimal = ZERO

    def total(self) -> Decimal:
        return sum(vars(self).values(), ZERO)


@dataclass
class Itemised:
    """Everything draft_n computes before it is laid out as lines."""

    pay: PaySources
    gross: Decimal
    status: str
    gross_source: str
    components: Components
    union: Decimal
    allowance: Decimal
    applied: Decimal
    commute: CommuteResult
    work_room: WorkRoomResult | None
    rows: list[ReturnInputFact]
    warnings: list[DraftWarning] = field(default_factory=list)
    line_sources: dict[str, list[SourceRef]] = field(default_factory=dict)
    blocks: WerbungskostenComponents = field(default_factory=WerbungskostenComponents)

    @property
    def itemised(self) -> Decimal:
        return self.components.total()

    @property
    def result(self) -> Decimal:
        return self.gross - self.applied

    @property
    def replacement_benefits(self) -> Decimal:
        return sum((c.replacement_benefits for c in self.pay.certificates), ZERO)


def itemise(facts: OwnerReturnFacts, tax_year: int, rs: AnlageNRuleSet, tariff: Tariff) -> Itemised:
    """The figures of draft_n (§5.6). RuleError anlage_n.no_pay without any pay source,
    anlage_n.work_room_excludes_day_rate, anlage_n.useful_life_missing."""
    rows = in_year(facts.inputs, tax_year)
    year_rows = [r for r in rows if r.tax_year == tax_year]
    pay = pay_of_rows(rows, tax_year)
    warnings = list(pay.warnings)
    certs = pay.certificates
    pay_refs = pay.sources()
    if certs:
        gross = sum((c.gross for c in certs), ZERO)
        status, source = ("estimate", "payslip") if pay.projected else ("certified", "certificate")
    elif facts.expected_gross_pay is not None:
        gross = facts.expected_gross_pay.value.amount
        status, source = "estimate", "expected"
        pay_refs = [SourceRef(kind="income_input", id=facts.expected_gross_pay.id)]
        warnings.append(warning(WarningCode.ANLAGE_N_CERTIFICATE_MISSING))
    else:
        raise RuleError(ANLAGE_N_NO_PAY, f"no certificate, payslip or expected pay in {tax_year}")

    def of(kind: K) -> list[ReturnInputFact]:
        return [r for r in year_rows if r.kind is kind]

    routes = of(K.COMMUTE)
    commute = commute_lines([r.value for r in routes], certs, rs)
    warnings += commute.warnings
    rooms = of(K.WORK_ROOM)
    room = rooms[0].value if rooms else None
    wr = work_room_line(room, rs) if room is not None else None
    if wr is not None:
        warnings += wr.warnings
    offices = of(K.HOME_OFFICE)
    office_amount = ZERO
    office: HomeOfficeResult | None = None
    if offices:
        office = home_office_result(offices[0].value, sum(r.value.days for r in routes), room, rs)
        office_amount = office.amount
        warnings += office.warnings
    equipment_rows = [r for r in rows if r.kind is K.WORK_EQUIPMENT]
    equipment_parts = [(r, equipment_slice(r.value, tax_year, rs)) for r in equipment_rows]
    equipment_slices = [(r, part.amount) for r, part in equipment_parts]
    equipment = sum((amount for _, amount in equipment_slices), ZERO)
    telecom_rows = of(K.TELECOM)
    telecom_parts = [(r, telecom_result(r.value, rs)) for r in telecom_rows]
    telecom = sum((part.amount for _, part in telecom_parts), ZERO)
    dues = dues_lines(year_rows)
    training, training_warnings = training_lines(year_rows)
    warnings += training_warnings
    other_rows = of(K.OTHER_EXPENSE)
    other_parts = other_expense_items(other_rows, rs)
    other = sum((amount for _, amount in other_parts), ZERO)
    components = Components(
        commute=commute.deductible,
        home_office=office_amount,
        work_room=wr.amount if wr is not None else ZERO,
        equipment=equipment,
        telecom=telecom,
        dues=dues.total,
        training=training,
        other=other,
    )
    itemised = components.total()
    allowance = min(tariff.employee_allowance, gross)
    if rs.union_dues_beside_allowance:
        applied = dues.union + max(allowance, itemised - dues.union)
    else:
        applied = max(allowance, itemised)

    if rs.form.status is FormStatus.PROVISIONAL:
        warnings.append(
            warning(WarningCode.ANLAGE_N_FORM_PROVISIONAL, form_year=str(rs.form.form_year))
        )
    real = pay.real
    marked = any(c.church_tax > 0 or c.church_tax_marked for c in real)
    if real and marked != facts.church_tax:
        warnings.append(warning(WarningCode.ANLAGE_N_CHURCH_MISMATCH, row_sources(pay.real_rows)))
    if any(c.tax_class == 6 for c in certs):
        warnings.append(warning(WarningCode.ANLAGE_N_TAX_CLASS_6))
    main = [c for c in real if c.tax_class != 6]
    for i, a in enumerate(main):
        for b in main[i + 1 :]:
            if a.period_from <= b.period_to and b.period_from <= a.period_to:
                warnings.append(warning(WarningCode.ANLAGE_N_PERIODS_OVERLAP))
    if room is not None and not routes:
        warnings.append(warning(WarningCode.ANLAGE_N_FIRST_WORKPLACE_CHECK, row_sources(rooms)))

    commute_refs = row_sources(routes) + pay_refs if routes or commute.employer_offset else []
    line_sources = {
        "pay": pay_refs,
        "commute": commute_refs,
        "home_office": row_sources(offices),
        "work_room": row_sources(rooms),
        "equipment": row_sources([r for r, amount in equipment_slices if amount != 0]),
        "telecom": row_sources(telecom_rows),
        "dues": row_sources(dues.rows),
        "training": row_sources(of(K.TRAINING)),
        "other": row_sources(other_rows),
    }
    return Itemised(
        pay=pay,
        gross=gross,
        status=status,
        gross_source=source,
        components=components,
        union=dues.union,
        allowance=allowance,
        applied=applied,
        commute=commute,
        work_room=wr,
        rows=rows,
        warnings=warnings,
        line_sources=line_sources,
        blocks=_blocks(
            components,
            routes=routes,
            commute=commute,
            rs=rs,
            office=(offices[0], office) if office is not None else None,
            room=(rooms[0], wr) if wr is not None else None,
            equipment=equipment_parts,
            telecom=telecom_parts,
            dues=dues,
            training=of(K.TRAINING),
            other=other_parts,
        ),
    )


def _blocks(
    c: Components,
    *,
    routes: list[ReturnInputFact],
    commute: CommuteResult,
    rs: AnlageNRuleSet,
    office: tuple[ReturnInputFact, HomeOfficeResult] | None,
    room: tuple[ReturnInputFact, WorkRoomResult] | None,
    equipment: list[tuple[ReturnInputFact, EquipmentSlice]],
    telecom: list[tuple[ReturnInputFact, TelecomResult]],
    dues: Dues,
    training: list[ReturnInputFact],
    other: list[tuple[ReturnInputFact, Decimal]],
) -> WerbungskostenComponents:
    """The blocks of `c` with what their rules computed on the way (no figure computed here:
    every amount is the rule's, every block amount the one in `c`)."""
    blocks: dict[str, object] = {}
    if routes or commute.employer_offset:
        blocks["commute"] = CommuteComponent(
            amount=c.commute,
            routes=[
                CommuteRouteDetail(
                    input_id=r.id,
                    workplace_label=r.value.workplace_label,
                    days=r.value.days,
                    km=r.value.km,
                    km_car=r.value.km_car,
                    km_rest=r.value.km_collective + r.value.km_other,
                    pauschale_car=cents(car),
                    pauschale_rest=cents(rest),
                    public_transport_costs=r.value.public_transport_costs,
                )
                for r, (car, rest) in zip(routes, commute.route_parts, strict=True)
            ],
            rates=rs.commute_rates,
            pauschale_car=commute.pauschale_car,
            pauschale_rest=commute.pauschale_rest,
            cap_non_car=rs.commute_cap_non_car,
            rest_capped=commute.rest_capped,
            public_transport_costs=commute.tickets,
            public_transport_used="public_transport_costs_exceed" in commute.notes,
            total=commute.total,
            employer_offset=commute.employer_offset,
        )
    if office is not None:
        row, result = office
        blocks["home_office"] = HomeOfficeComponent(
            amount=c.home_office,
            input_id=row.id,
            days_other_workplace=row.value.days_other_workplace,
            days_no_other_workplace=row.value.days_no_other_workplace,
            day_rate=rs.home_office_day_rate,
            full=result.full,
            cap=rs.home_office_cap,
            capped=result.full > rs.home_office_cap,
        )
    if room is not None:
        row, wr = room
        blocks["work_room"] = WorkRoomComponent(
            amount=c.work_room,
            input_id=row.id,
            months=row.value.months,
            actual=wr.actual,
            flat=wr.flat,
            method=wr.method,
            prefer=row.value.prefer,
        )
    if equipment:
        blocks["equipment"] = EquipmentComponent(
            amount=c.equipment,
            items=[
                EquipmentItemDetail(
                    input_id=r.id,
                    label=r.value.label,
                    item_class=r.value.item_class,
                    bought_year=r.value.bought_on.year,
                    professional_share=r.value.professional_share,
                    gross=r.value.gross,
                    base=part.base,
                    method=part.method,
                    life_months=part.life_months,
                    months_in_year=part.months_in_year,
                    disposed=part.disposed,
                    amount=part.amount,
                )
                for r, part in equipment
            ],
        )
    if telecom:
        # one row per owner and year (ONE_PER_YEAR); the block names the current one
        row, part = telecom[0]
        blocks["telecom"] = TelecomComponent(
            amount=c.telecom,
            input_id=row.id,
            bills_year=row.value.bills_year,
            share=rs.telecom_share,
            share_amount=part.share_amount,
            months=row.value.months,
            cap_monthly=rs.telecom_cap_monthly,
            cap=part.cap,
            employer_reimbursed=row.value.employer_reimbursed,
        )
    if dues.rows:
        blocks["dues"] = SumComponent(
            amount=c.dues,
            items=[
                AmountItem(
                    input_id=r.id,
                    label=r.value.name,
                    amount=r.value.amount,
                    is_union=r.value.is_union,
                )
                for r in dues.rows
            ],
        )
    if training:
        blocks["training"] = SumComponent(
            amount=c.training,
            items=[
                AmountItem(input_id=r.id, label=r.value.label, amount=r.value.amount)
                for r in training
            ],
        )
    if other:
        blocks["other"] = SumComponent(
            amount=c.other,
            items=[
                AmountItem(
                    input_id=r.id,
                    label=r.value.label,
                    amount=amount,
                    flat=r.value.amount is None,
                )
                for r, amount in other
            ],
        )
    return WerbungskostenComponents.model_validate(blocks)


def _total(certs: list[WageCertificate], name: str) -> Decimal:
    return sum((getattr(c, name) for c in certs), ZERO)


def line_amounts(item: Itemised, facts_rows: list[ReturnInputFact]) -> dict[str, Decimal]:
    """The money and day-count lines by key, in form order (route lines excluded)."""
    certs = item.pay.certificates
    first = [c for c in certs if c.tax_class != 6]
    sixth = [c for c in certs if c.tax_class == 6]
    offices = [r.value for r in facts_rows if r.kind is K.HOME_OFFICE]
    office = offices[0] if offices else None
    c = item.components
    lines: dict[str, Decimal] = {"pay.gross": _total(first, "gross") if certs else item.gross}
    optional = {
        "pay.income_tax": _total(first, "income_tax"),
        "pay.soli": _total(first, "soli"),
        "pay.church_tax": _total(first, "church_tax"),
        "pay.church_tax_spouse": _total(first, "church_tax_spouse"),
        "pay.gross_class_6": _total(sixth, "gross"),
        "pay.income_tax_class_6": _total(sixth, "income_tax"),
        "pay.soli_class_6": _total(sixth, "soli"),
        "pay.church_tax_class_6": _total(sixth, "church_tax"),
        "pay.church_tax_spouse_class_6": _total(sixth, "church_tax_spouse"),
        "pay.replacement_benefits": _total(certs, "replacement_benefits"),
        "commute.employer_tax_free": _total(certs, "employer_commute_tax_free"),
        "commute.employer_flat_taxed": _total(certs, "employer_commute_flat_taxed"),
        "dues.associations": c.dues,
        "equipment.total": c.equipment,
        "work_room.amount": c.work_room,
        "home_office.days_other_workplace": Decimal(office.days_other_workplace if office else 0),
        "home_office.days_no_other_workplace": Decimal(
            office.days_no_other_workplace if office else 0
        ),
        "training.total": c.training,
        "other.total": c.telecom + c.other,
    }
    lines |= {k: v for k, v in optional.items() if v != 0}
    lines |= {
        "wk.itemised": item.itemised,
        "wk.allowance": item.allowance,
        "wk.applied": item.applied,
    }
    return lines


_ROUTE_FIELDS = ("days", "km", "km_car", "km_collective", "km_other", "public_transport_costs")


def _line_group(key: str) -> str:
    return "other_total" if key == "other.total" else key.split(".", 1)[0]


def draft_n(
    facts: OwnerReturnFacts, tax_year: int, rs: AnlageNRuleSet, tariff: Tariff
) -> AnlageNDraft:
    """One owner's Anlage N for `tax_year` (§5.6). RuleError as `itemise`."""
    item = itemise(facts, tax_year, rs, tariff)
    year_rows = [r for r in item.rows if r.tax_year == tax_year]
    s = item.line_sources
    every = unique(ref for refs in s.values() for ref in refs)
    group_sources = {
        "pay": s["pay"],
        "commute": s["commute"],
        "dues": s["dues"],
        "equipment": s["equipment"],
        "work_room": s["work_room"],
        "home_office": s["home_office"],
        "training": s["training"],
        "other_total": unique(s["telecom"] + s["other"]),
        "wk": every,
    }
    lines: list[DraftLine] = []
    for key, amount in line_amounts(item, year_rows).items():
        lines.append(_line(rs, key, amount, group_sources.get(_line_group(key), [])))
    routes = [r for r in year_rows if r.kind is K.COMMUTE][:COMMUTE_ROUTES]
    for n, route in enumerate(routes, start=1):
        for name in _ROUTE_FIELDS:
            value = getattr(route.value, name)
            if value is None:
                continue
            key = f"commute.route_{n}.{name}"
            lines.append(_line(rs, key, Decimal(value), row_sources([route])))
    return AnlageNDraft(
        owner_id=facts.owner_id,
        tax_year=tax_year,
        status=item.status,
        gross_source=item.gross_source,
        lines=lines,
        gross=item.gross,
        wk_itemised=item.itemised,
        wk_union=item.union,
        wk_allowance=item.allowance,
        wk_applied=item.applied,
        result=item.result,
        replacement_benefits=item.replacement_benefits,
        warnings=item.warnings,
        sources=unique([*every, RULE_SET_SOURCE, TARIFF_SOURCE]),
        components=item.blocks,
        form=RuleSetStamp(
            tax_year=tax_year,
            version=rs.version,
            form_year=rs.form.form_year,
            form_status=rs.form.status,
        ),
    )


def _line(rs: AnlageNRuleSet, key: str, amount: Decimal, sources: list[SourceRef]) -> DraftLine:
    form = rs.form.lines[key]
    return DraftLine(
        key=key,
        amount=amount,
        sources=sources,
        zeile_from=form.zeile_from,
        zeile=form.zeile,
        kennzahl=form.kennzahl,
        label=form.label,
        amount_rounded=whole_euros(amount, form.rounding),
    )
