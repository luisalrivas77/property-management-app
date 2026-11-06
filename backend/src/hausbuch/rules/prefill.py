"""Building a pre-fill proposal from an extraction (tests/fixtures/phase2/prefill.yaml).

build_proposal(kind, extraction, context) -> list[ProposalRecord]. Spec:
docs/phases/phase-2.md §6.2, §6.3, D14, D15. Pure: no I/O, no model. It decides per record
whether it creates, patches, corrects or leaves an existing record unchanged, computes the
derived values (building share, the Rate from the Tilgungssatz, ownership shares, the
prepayment sum, rent model, the tenancy's unit) and writes notes. Nothing it returns reaches a
domain table: only the user's apply does, through the phase 1 services (D14).

Every value inside a record (`fields[*].value`, `current`) is JSON: dates as ISO strings,
decimals as strings, fractions as {numerator, denominator}, ids as strings. The result never
depends on the order of the context's lists (the fixture runs every case shuffled): where
several existing records match, the one with the smallest id wins.

Record keys, in this order when a kind proposes them: typing, property, correction:<field>,
unit, price_split, ownership, cost:<kind>, loan, tenant:<n>, tenancy, rent, link:property /
link:loan / link:tenancy.
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import JsonValue

from hausbuch.schemas.ai import ClassifyOutput, ConfidenceLevel, ExtractedValue
from hausbuch.schemas.enums import (
    AcquisitionCostKind,
    DepositForm,
    DocumentSubtype,
    DocumentType,
    ExtractionKind,
    PropertyKind,
    RentModel,
)
from hausbuch.schemas.extraction import _Typing
from hausbuch.schemas.proposal import (
    ExistingLoan,
    ExistingProperty,
    ExistingUnit,
    PrefillContext,
    ProposalRecord,
)

_CONFIDENCE_ORDER: dict[ConfidenceLevel, int] = {"low": 0, "medium": 1, "high": 2}
_SIX_PLACES = Decimal("0.000001")
_CENTS = Decimal("0.01")
# The property facts a Kaufvertrag states, by record field and extraction field.
_PROPERTY_FIELDS = (
    ("kind", "property_kind"),
    ("address.street", "street"),
    ("address.house_number", "house_number"),
    ("address.postal_code", "postal_code"),
    ("address.city", "city"),
    ("build_year", "build_year"),
    ("notarized_on", "notarized_on"),
    ("purchase_date", "purchase_date"),
    ("purchase_price", "purchase_price"),
)
# Milestone dates (D7): never patched, corrected through correct_* (§6.5).
_MILESTONES = ("notarized_on", "purchase_date")
_ADDRESS_KEYS = ("street", "house_number", "postal_code")
# A handover protocol is linked to the tenancy whose move-in is this close to its date.
_HANDOVER_WINDOW_DAYS = 31


# --- Classification -> extraction kind -----------------------------------------------


def extraction_kind(classification: ClassifyOutput) -> ExtractionKind | None:
    """The field set a classified document is read with; None for one without a field set
    (a letter, "other"): its proposal is the typing only."""
    t, s = classification.type, classification.subtype
    by_subtype = {
        DocumentSubtype.PURCHASE_CONTRACT: ExtractionKind.PURCHASE_CONTRACT,
        DocumentSubtype.LOAN_CONTRACT: ExtractionKind.LOAN_CONTRACT,
        DocumentSubtype.LEASE: ExtractionKind.LEASE,
        DocumentSubtype.HANDOVER_PROTOCOL: ExtractionKind.HANDOVER_PROTOCOL,
        DocumentSubtype.DEPOSIT_RECORD: ExtractionKind.DEPOSIT_RECORD,
        DocumentSubtype.GRUNDSTEUERBESCHEID: ExtractionKind.GRUNDSTEUERBESCHEID,
    }
    if s in by_subtype:
        return by_subtype[s]
    by_type = {
        DocumentType.INVOICE: ExtractionKind.INVOICE,
        DocumentType.BESCHEID: ExtractionKind.BESCHEID,
        DocumentType.WIRTSCHAFTSPLAN: ExtractionKind.WIRTSCHAFTSPLAN,
        DocumentType.HAUSGELDABRECHNUNG: ExtractionKind.HAUSGELDABRECHNUNG,
        DocumentType.ZINSBESCHEINIGUNG: ExtractionKind.ZINSBESCHEINIGUNG,
        DocumentType.STATEMENT: ExtractionKind.STATEMENT,
    }
    return by_type.get(t)


# --- Entry point ---------------------------------------------------------------------


def build_proposal(
    kind: ExtractionKind | None, extraction: _Typing, context: PrefillContext
) -> list[ProposalRecord]:
    """The records a document proposes (fixture prefill.yaml). `extraction` is the kind's
    extraction model (schemas/extraction.py), `_Typing` when kind is None."""
    b = _Builder(extraction, context)
    b.typing()
    match kind:
        case ExtractionKind.PURCHASE_CONTRACT:
            b.purchase_contract()
        case ExtractionKind.INVOICE | ExtractionKind.BESCHEID:
            b.cost_document()
        case ExtractionKind.LOAN_CONTRACT:
            b.loan_contract()
        case ExtractionKind.LEASE:
            b.lease()
        case ExtractionKind.HANDOVER_PROTOCOL:
            b.handover_protocol()
        case (
            ExtractionKind.GRUNDSTEUERBESCHEID
            | ExtractionKind.WIRTSCHAFTSPLAN
            | ExtractionKind.HAUSGELDABRECHNUNG
        ):
            b.link_property(_resolve_property(extraction, context))
        case ExtractionKind.ZINSBESCHEINIGUNG:
            loan = _match_loan(extraction, context.loans)
            if loan is None and len(context.loans) == 1:
                loan = context.loans[0]
            if loan is not None:
                b.link("link:loan", loan.id)
        case _:  # statement, deposit record (step 2), unknown: typing only
            pass
    return b.records


# --- The builder -----------------------------------------------------------------------


class _Builder:
    def __init__(self, extraction: _Typing, context: PrefillContext) -> None:
        self.x = extraction
        self.ctx = context
        self.records: list[ProposalRecord] = []

    # -- values

    def ev(self, name: str) -> ExtractedValue:
        """The extraction's value of a field; missing when the model has no such field."""
        value = getattr(self.x, name, None)
        return value if isinstance(value, ExtractedValue) else ExtractedValue.missing()

    def stated(self, name: str) -> bool:
        return self.ev(name).value is not None

    def add(
        self,
        key: str,
        entity_type: str,
        mode: str,
        fields: dict[str, ExtractedValue],
        *,
        target_id: UUID | None = None,
        current: dict[str, JsonValue] | None = None,
        notes: Sequence[str] = (),
    ) -> None:
        self.records.append(
            ProposalRecord(
                key=key,
                entity_type=entity_type,
                mode=mode,
                target_id=target_id,
                fields=fields,
                current=current,
                notes=list(notes),
            )
        )

    def link(self, key: str, target_id: UUID | None) -> None:
        self.add(key, "link", "create", {}, target_id=target_id)

    def link_property(self, prop: ExistingProperty | None) -> None:
        if prop is not None:
            self.link("link:property", prop.id)

    # -- typing (every kind)

    def typing(self) -> None:
        c = self.ctx.classification
        fields = {"type": _code_value(c.type.value, c.confidence)}
        if c.subtype is not None:
            fields["subtype"] = _code_value(c.subtype.value, c.confidence)
        for name in ("title", "document_date", "tax_year"):
            if self.stated(name):
                fields[name] = self.ev(name)
        self.add("typing", "typing", "create", fields)

    # -- Kaufvertrag

    def purchase_contract(self) -> None:
        proposed = {
            field: (_mapped(self.ev(src), _property_kind) if field == "kind" else self.ev(src))
            for field, src in _PROPERTY_FIELDS
        }
        notes = []
        purchase = self.ev("purchase_date")
        if purchase.value is None and purchase.source is not None:
            notes.append(f"Übergang laut S. {purchase.source.page}: „{purchase.source.quote}“")

        prop = _match_property(self.x, self.ctx.properties)
        if prop is None:
            self.add("property", "property", "create", proposed, notes=notes)
        else:
            self._match_record(
                "property",
                "property",
                prop.id,
                proposed,
                _property_current(prop),
                notes=notes,
                milestones=_MILESTONES,
            )
        self._unit(prop)
        self._price_split()
        self._ownership()
        self.link("link:property", None if prop is None else prop.id)

    def _match_record(
        self,
        key: str,
        entity_type: str,
        target_id: UUID,
        proposed: dict[str, ExtractedValue],
        existing: dict[str, JsonValue],
        *,
        notes: Sequence[str] = (),
        milestones: Iterable[str] = (),
    ) -> None:
        """unchanged when every stated field the context holds equals it, else patch with
        the differing ones; a differing milestone date gets its own correction record."""
        compared = {f: v for f, v in proposed.items() if v.value is not None and f in existing}
        corrections = {
            f: v for f, v in compared.items() if f in milestones and not _same(v.value, existing[f])
        }
        kept = {f: v for f, v in compared.items() if f not in corrections}
        differing = {f: v for f, v in kept.items() if not _same(v.value, existing[f])}
        fields = differing or kept
        self.add(
            key,
            entity_type,
            "patch" if differing else "unchanged",
            fields,
            target_id=target_id,
            current={f: existing[f] for f in fields},
            notes=notes,
        )
        for f, v in corrections.items():
            self.add(
                f"correction:{f}",
                entity_type,
                "correction",
                {f: v},
                target_id=target_id,
                current={f: existing[f]},
            )

    def _unit(self, prop: ExistingProperty | None) -> None:
        kind = _property_kind(self.ev("property_kind").value)
        if not self.stated("unit_label") and kind is not PropertyKind.ETW:
            return
        label = self.ev("unit_label")
        proposed = {
            "label": label,
            "mea": self.ev("mea"),
            "floor_area_m2": self.ev("floor_area_m2"),
        }
        unit = None
        if prop is not None and label.value is not None:
            unit = _first(
                u
                for u in self.ctx.units
                if u.property_id == prop.id and _norm(u.label) == _norm(label.value)
            )
        elif prop is not None:
            return  # an existing property and no label to match a unit by
        if unit is None:
            fields = {f: v for f, v in proposed.items() if f != "mea" or v.value is not None}
            self.add("unit", "unit", "create", fields)
        else:
            self._match_record("unit", "unit", unit.id, proposed, _unit_current(unit))

    def _price_split(self) -> None:
        price, land = self.ev("purchase_price"), self.ev("land_value")
        if price.value is None or land.value is None:
            return
        share = None
        p, lv = _decimal(price.value), _decimal(land.value)
        if p is not None and lv is not None and p > 0 and 0 <= lv < p:
            share = ((p - lv) / p).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
            share = share if Decimal(0) < share < Decimal(1) else None
        value = None if share is None else str(share)
        derived = _derived(value, [("purchase_price", price), ("land_value", land)])
        self.add("price_split", "price_split", "create", {"building_share": derived})

    def _ownership(self) -> None:
        buyers, clause = self.ev("buyers"), self.ev("ownership_clause")
        names = buyers.value
        if not isinstance(names, list) or not names or clause.value is None:
            return
        owners = []
        for name in names:
            if not isinstance(name, str):
                return
            matches = [o for o in self.ctx.owners if _norm(o.full_name) == _norm(name)]
            if len(matches) != 1:
                return
            owners.append(matches[0])
        if len({o.id for o in owners}) != len(owners):
            return
        share = _ownership_share(str(clause.value), len(owners))
        if share is None:
            return
        numerator, denominator = share
        value = [
            {
                "owner_id": str(o.id),
                "share": {"numerator": numerator, "denominator": denominator},
            }
            for o in owners
        ]
        derived = _derived(value, [("buyers", buyers), ("ownership_clause", clause)])
        self.add("ownership", "ownership", "create", {"shares": derived})

    # -- Nebenkosten invoices and Bescheide

    def cost_document(self) -> None:
        prop = _resolve_property(self.x, self.ctx)
        if prop is None:
            return
        if self.ctx.classification.subtype is DocumentSubtype.COST_BASIS_EVIDENCE:
            self._cost(prop)
        self.link_property(prop)

    def _cost(self, prop: ExistingProperty) -> None:
        sender = self.ev("sender")
        if sender.value is None:
            kind_value = _code_value(AcquisitionCostKind.OTHER.value, "low")
        else:
            kind_value = _mapped(sender, _cost_kind)
        amount, note = self.ev("amount_total"), self.ev("reference")
        stated = {f: v for f, v in (("amount", amount), ("note", note)) if v.value is not None}

        # 1. A row whose Beleg is this document: never a second row for the same Beleg.
        same_beleg = _first(c for c in self.ctx.costs if c.document_id == self.ctx.document_id)
        if same_beleg is not None:
            existing = {"amount": _json(same_beleg.amount), "note": same_beleg.note}
            differing = {f: v for f, v in stated.items() if not _same(v.value, existing[f])}
            fields = differing or stated
            self.add(
                f"cost:{same_beleg.kind.value}",
                "acquisition_cost",
                "patch" if differing else "unchanged",
                fields,
                target_id=same_beleg.id,
                current={f: existing[f] for f in fields},
            )
            return

        kind = kind_value.value
        # 2. A row of that kind on the property without a Beleg: the apply attaches this one.
        without_beleg = sorted(
            (
                c
                for c in self.ctx.costs
                if c.property_id == prop.id and c.kind.value == kind and c.document_id is None
            ),
            key=lambda c: (not _same(amount.value, _json(c.amount)), c.id),
        )
        if without_beleg:
            row = without_beleg[0]
            existing = {"amount": _json(row.amount), "note": row.note}
            self.add(
                f"cost:{kind}",
                "acquisition_cost",
                "patch",
                stated,
                target_id=row.id,
                current={f: existing[f] for f in stated},
            )
            return

        # 3. Otherwise a new row (another bill of the same kind is real).
        fields = {"kind": kind_value, "amount": amount}
        if note.value is not None:
            fields["note"] = note
        self.add(f"cost:{kind}", "acquisition_cost", "create", fields)

    # -- Darlehensvertrag

    def loan_contract(self) -> None:
        monthly = self.ev("monthly_payment")
        if monthly.value is None:
            monthly = self._derived_rate() or monthly
        values = {
            "lender": self.ev("lender"),
            "contract_number": self.ev("contract_number"),
            "principal": self.ev("principal"),
            "nominal_rate": self.ev("nominal_rate"),
            "monthly_payment": monthly,
            "fixed_until": self.ev("fixed_until"),
            "disbursed_on": self.ev("disbursed_on"),
            "first_payment_on": self.ev("first_payment_on"),
            "account_iban": self.ev("account_iban"),
        }
        loan = _match_loan(self.x, self.ctx.loans)
        if loan is None:
            optional = ("contract_number", "fixed_until", "account_iban")
            fields = {f: v for f, v in values.items() if f not in optional or v.value is not None}
            self.add("loan", "loan", "create", fields)
        else:
            fields = {
                f: v
                for f, v in values.items()
                if f not in ("lender", "contract_number") and v.value is not None
            }
            self.add("loan", "loan", "patch" if fields else "unchanged", fields, target_id=loan.id)
        self.link("link:loan", None if loan is None else loan.id)

    def _derived_rate(self) -> ExtractedValue | None:
        """Rate = principal x (Sollzins + anfängliche Tilgung) / 12, in cents, half up, when
        the contract states only the Tilgungssatz."""
        names = ("principal", "nominal_rate", "initial_repayment_rate")
        inputs = [(n, self.ev(n)) for n in names]
        numbers = [_decimal(v.value) for _, v in inputs]
        if any(n is None for n in numbers):
            return None
        principal, rate, repayment = numbers
        value = (principal * (rate + repayment) / 12).quantize(_CENTS, rounding=ROUND_HALF_UP)
        return _derived(str(value) if value > 0 else None, inputs)

    # -- Mietvertrag (step 2, gated by ai_tenant_documents)

    def lease(self) -> None:
        for n in (1, 2):
            if self.stated(f"tenant_{n}_full_name"):
                self._tenant(n)
        unit = self._lease_unit()
        tenancy = None
        if unit is not None:
            start = self.ev("start_date").value
            tenancy = _first(
                t
                for t in self.ctx.tenancies
                if t.unit_id == unit.id and start is not None and t.start_date.isoformat() == start
            )
        unit_value = _derived(
            None if unit is None else str(unit.id),
            [(n, self.ev(n)) for n in ("street", "house_number", "postal_code", "unit_label")],
        )
        rent_model = _derived(
            _rent_model(self.ev("rent_clause").value), [("rent_clause", self.ev("rent_clause"))]
        )
        deposit_form = _mapped(self.ev("deposit_clause"), _deposit_form)
        if tenancy is None:
            fields = {"unit_id": unit_value, "start_date": self.ev("start_date")}
            for name in ("signed_on", "fixed_term_until"):
                if self.stated(name):
                    fields[name] = self.ev(name)
            fields["rent_model"] = rent_model
            if self.stated("deposit_amount"):
                fields["deposit_amount"] = self.ev("deposit_amount")
            if self.stated("deposit_clause"):
                fields["deposit_form"] = deposit_form
            self.add("tenancy", "tenancy", "create", fields)
            self._initial_rent()
        else:
            fields = {}
            if self.stated("fixed_term_until"):
                fields["fixed_term_until"] = self.ev("fixed_term_until")
            if self.stated("rent_clause"):
                fields["rent_model"] = rent_model
            if self.stated("deposit_amount"):
                fields["deposit_amount"] = self.ev("deposit_amount")
            if self.stated("deposit_clause"):
                fields["deposit_form"] = deposit_form
            mode = "patch" if fields else "unchanged"
            self.add("tenancy", "tenancy", mode, fields, target_id=tenancy.id)
        self.link("link:tenancy", None if tenancy is None else tenancy.id)

    def _tenant(self, n: int) -> None:
        name = self.ev(f"tenant_{n}_full_name")
        contact = {
            field: self.ev(f"tenant_{n}_{field}")
            for field in ("email", "phone", "iban")
            if self.stated(f"tenant_{n}_{field}")
        }
        existing = _first(
            t
            for t in self.ctx.tenants
            if t.full_name is not None and _norm(t.full_name) == _norm(name.value)
        )
        key = f"tenant:{n}"
        if existing is None:
            self.add(key, "tenant", "create", {"full_name": name} | contact)
        else:
            mode = "patch" if contact else "unchanged"
            self.add(key, "tenant", mode, contact, target_id=existing.id)

    def _lease_unit(self) -> ExistingUnit | None:
        prop = _resolve_property(self.x, self.ctx)
        if prop is None:
            return None
        units = [u for u in self.ctx.units if u.property_id == prop.id]
        label = self.ev("unit_label").value
        if label is not None:
            return _first(u for u in units if _norm(u.label) == _norm(label))
        return units[0] if len(units) == 1 else None

    def _initial_rent(self) -> None:
        fields = {"cold_rent": self.ev("cold_rent")}
        op, heat = self.ev("operating_costs_prepayment"), self.ev("heating_costs_prepayment")
        if op.value is not None and heat.value is not None:
            a, b = _decimal(op.value), _decimal(heat.value)
            total = None if a is None or b is None else str((a + b).quantize(_CENTS))
            fields["operating_costs_prepayment"] = _derived(
                total,
                [("operating_costs_prepayment", op), ("heating_costs_prepayment", heat)],
            )
        elif op.value is not None:
            fields["operating_costs_prepayment"] = op
        elif heat.value is not None:
            fields["operating_costs_prepayment"] = _derived(
                heat.value, [("heating_costs_prepayment", heat)]
            )
        if self.stated("start_date"):
            fields["effective_from"] = self.ev("start_date")
        self.add("rent", "rent_schedule", "create", fields)

    # -- Übergabeprotokoll (step 2)

    def handover_protocol(self) -> None:
        prop = _resolve_property(self.x, self.ctx)
        on = _iso_date(self.ev("document_date").value)
        if prop is None or on is None:
            return
        units = {u.id for u in self.ctx.units if u.property_id == prop.id}
        near = sorted(
            (abs((t.start_date - on).days), t.id)
            for t in self.ctx.tenancies
            if t.unit_id in units and abs((t.start_date - on).days) <= _HANDOVER_WINDOW_DAYS
        )
        if near:
            self.link("link:tenancy", near[0][1])


# --- Matching existing records ----------------------------------------------------------


def _norm(value: Any) -> str:
    """Casefold, whitespace collapsed."""
    return " ".join(str(value).split()).casefold()


def _first[T](items: Iterable[T]) -> T | None:
    """The match with the smallest id, so the result does not depend on list order."""
    found = sorted(items, key=lambda item: item.id)
    return found[0] if found else None


def _address_of(extraction: _Typing) -> tuple[str, ...] | None:
    parts = []
    for name in _ADDRESS_KEYS:
        value = getattr(extraction, name, None)
        if not isinstance(value, ExtractedValue) or value.value is None:
            return None
        parts.append(_norm(value.value))
    return tuple(parts)


def _match_property(
    extraction: _Typing, properties: Sequence[ExistingProperty]
) -> ExistingProperty | None:
    """By street + house number + postal code."""
    address = _address_of(extraction)
    if address is None:
        return None
    return _first(
        p
        for p in properties
        if (_norm(p.address.street), _norm(p.address.house_number), _norm(p.address.postal_code))
        == address
    )


def _resolve_property(extraction: _Typing, context: PrefillContext) -> ExistingProperty | None:
    """The property a document belongs to: by its address when it states one, else the only
    property of the workspace."""
    if _address_of(extraction) is not None:
        return _match_property(extraction, context.properties)
    return context.properties[0] if len(context.properties) == 1 else None


def _match_loan(extraction: _Typing, loans: Sequence[ExistingLoan]) -> ExistingLoan | None:
    """By contract number (whitespace removed) and lender."""
    number = getattr(extraction, "contract_number", None)
    lender = getattr(extraction, "lender", None)
    if not isinstance(number, ExtractedValue) or number.value is None:
        return None
    if not isinstance(lender, ExtractedValue) or lender.value is None:
        return None
    wanted = (_compact(number.value), _norm(lender.value))
    return _first(
        loan
        for loan in loans
        if loan.contract_number is not None
        and (_compact(loan.contract_number), _norm(loan.lender)) == wanted
    )


def _compact(value: Any) -> str:
    return "".join(str(value).split()).casefold()


def _property_current(prop: ExistingProperty) -> dict[str, JsonValue]:
    d = prop.model_dump(mode="json")
    return {
        "kind": d["kind"],
        "address.street": d["address"]["street"],
        "address.house_number": d["address"]["house_number"],
        "address.postal_code": d["address"]["postal_code"],
        "address.city": d["address"]["city"],
        "build_year": d["build_year"],
        "notarized_on": d["notarized_on"],
        "purchase_date": d["purchase_date"],
        "purchase_price": d["purchase_price"],
    }


def _unit_current(unit: ExistingUnit) -> dict[str, JsonValue]:
    d = unit.model_dump(mode="json")
    return {"label": d["label"], "mea": d["mea"]}


def _same(proposed: JsonValue, existing: JsonValue) -> bool:
    """Equal as values: decimals by number, fractions by ratio, text casefolded."""
    if proposed == existing:
        return True
    if isinstance(proposed, dict) and isinstance(existing, dict):
        try:
            return (
                proposed["numerator"] * existing["denominator"]
                == existing["numerator"] * proposed["denominator"]
            )
        except (KeyError, TypeError):
            return False
    if isinstance(proposed, str) and isinstance(existing, str):
        a, b = _decimal(proposed), _decimal(existing)
        if a is not None and b is not None:
            return a == b
        return _norm(proposed) == _norm(existing)
    return False


# --- Mapping and deriving values -------------------------------------------------------


def _code_value(value: JsonValue, confidence: ConfidenceLevel) -> ExtractedValue:
    """A value code set (the classifier's typing, a default): no source, validated."""
    return ExtractedValue(value=value, checks=["validates"], confidence=confidence)


def _mapped(source: ExtractedValue, mapping: Callable[[Any], Any]) -> ExtractedValue:
    """The extraction's value mapped to the target type, keeping its source, checks and
    confidence; a value that does not map is proposed as missing, its quote kept."""
    if source.value is None:
        return source
    mapped = mapping(source.value)
    if mapped is None:
        checks = ["cited"] if source.source is not None else []
        return ExtractedValue(value=None, source=source.source, checks=checks, confidence="low")
    value = mapped.value if hasattr(mapped, "value") else mapped
    return source.model_copy(update={"value": value})


def _derived(
    value: JsonValue | None, inputs: Sequence[tuple[str, ExtractedValue]]
) -> ExtractedValue:
    """Computed by code: no source, the inputs named, confidence the lowest of the inputs'
    (a missing input counts as low)."""
    levels = [v.confidence if v.value is not None else "low" for _, v in inputs]
    confidence = min(levels, key=_CONFIDENCE_ORDER.__getitem__) if levels else "low"
    if value is None:
        confidence = "low"
    return ExtractedValue(
        value=value,
        derived_from=[name for name, _ in inputs],
        checks=["derived", "validates"] if value is not None else ["derived"],
        confidence=confidence,
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _json(value: Any) -> JsonValue:
    return str(value) if isinstance(value, Decimal) else value


def _property_kind(text: Any) -> PropertyKind | None:
    if text is None:
        return None
    t = _norm(text)
    if t in {k.value for k in PropertyKind}:
        return PropertyKind(t)
    if any(w in t for w in ("wohnungseigentum", "eigentumswohnung", "teileigentum")):
        return PropertyKind.ETW
    if "einfamilienhaus" in t:
        return PropertyKind.EFH
    if "mehrfamilienhaus" in t:
        return PropertyKind.MFH
    return None


def _cost_kind(sender: Any) -> AcquisitionCostKind:
    t = _norm(sender)
    if "grunderwerbsteuer" in t:
        return AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX
    if "notar" in t:
        return AcquisitionCostKind.NOTARY
    if "grundbuch" in t or "amtsgericht" in t:
        return AcquisitionCostKind.LAND_REGISTRY
    if "makler" in t:
        return AcquisitionCostKind.BROKER
    return AcquisitionCostKind.OTHER


def _deposit_form(clause: Any) -> DepositForm | None:
    t = _norm(clause)
    if "bankbürgschaft" in t or "bankburgschaft" in t:
        return DepositForm.BANK_GUARANTEE
    if "bürgschaft" in t or "burgschaft" in t:
        return DepositForm.SURETY
    if any(w in t for w in ("barkaution", "kautionskonto", "sparbuch", "bar")):
        return DepositForm.CASH_ACCOUNT
    return None


def _rent_model(clause: Any) -> str:
    t = "" if clause is None else _norm(clause)
    if "staffel" in t:
        return RentModel.STAFFEL.value
    if "index" in t:
        return RentModel.INDEX.value
    return RentModel.FIXED.value


def _ownership_share(clause: str, buyers: int) -> tuple[int, int] | None:
    """The share each buyer holds, when the clause is understood."""
    t = _norm(clause)
    if buyers == 2 and ("je zur hälfte" in t or "je 1/2" in t or "zu je 1/2" in t):
        return (1, 2)
    if "zu gleichen teilen" in t or "zu gleichen anteilen" in t:
        return (1, buyers)
    if buyers == 1 and "allein" in t:
        return (1, 1)
    return None


__all__ = ["build_proposal", "extraction_kind"]
