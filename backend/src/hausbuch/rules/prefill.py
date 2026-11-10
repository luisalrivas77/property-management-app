"""Building a pre-fill proposal from an extraction (tests/fixtures/phase2/prefill.yaml).

build_proposal(kind, extraction, context) -> list[ProposalRecord]. Spec:
docs/phases/phase-2.md §6.2, §6.3, D14, D15. Pure: no I/O, no model. It decides per record
whether it creates, patches, corrects or leaves an existing record unchanged, computes the
derived values (building share, the Rate from the Tilgungssatz, a loan's interest-only flag,
ownership shares, the prepayment sum, rent model, the tenancy's unit, an invoice's tax year:
D36) and writes notes.
A financing invoice (Grundschuld, Hypothek, Darlehen) is decided here, not by the
classifier: it proposes no Nebenkosten row (D35). Nothing it returns reaches a
domain table: only the user's apply does, through the phase 1 services (D14).

Every value inside a record (`fields[*].value`, `current`) is JSON: dates as ISO strings,
decimals as strings, fractions as {numerator, denominator}, ids as strings. The result never
depends on the order of the context's lists (the fixture runs every case shuffled): where
several existing records match, the one with the smallest id wins.

Record keys, in this order when a kind proposes them: typing, property, correction:<field>,
unit, price_split, ownership, cost:<kind>, loan, tenant:<n>, tenancy, rent, deposit_received,
link:property / link:loan / link:tenancy / link:owner, tax_input:<kind>,
return_input:<kind>.

Phase 3 (docs/phases/phase-3.md §9): a Zinsbescheinigung also proposes
tax_input:interest_certified {tax_year, loan_id, value.amount}, a Hausgeldabrechnung
tax_input:ruecklage_spent {tax_year, value.amount}; the apply writes them through
services/tax_inputs (D14).

Phase 4b (docs/phases/phase-4b.md §8.2, D96, D105): a Lohnsteuerbescheinigung proposes
link:owner (the employee's name against this workspace's owners, by name tokens; unmatched,
the user picks the owner) and return_input:wage_certificate {owner_id, tax_year,
value.<field>} with every WageCertificate field (a line the model did not read is proposed
empty, never 0, and named in a note); a Gehaltsabrechnung link:owner and return_input:payslip
{owner_id, tax_year, value.<field>, value.<figure>.month / .ytd} with months_remaining_paid
= 12 − month and the gross projection in a note. return_input_value turns the confirmed
values into the row's value: an unread certificate line becomes 0 and is listed in `unread`
(Q-O29). The apply writes them through services/return_inputs (D14).

Phase 4b D129 (§8.4): an Einkommensteuerbescheid proposes income_input:bescheid_zve
{tax_year, value.amount ← taxable_income}; the apply writes it through
services/income_inputs with this document as the Bescheid and supersedes the year's current
row. Notes say which row it replaces ("ersetzt den Bescheid vom …"), that the document is
already recorded under another year (the apply refuses it), and warn, never blocking, when
the assessment is not a Zusammenveranlagung or the addressee names neither owner. The
addressee's names stay in the extraction: no note quotes them.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import JsonValue

from hausbuch.schemas.ai import ConfidenceLevel, ExtractedValue
from hausbuch.schemas.employment import CERTIFICATE_AMOUNTS, PAYSLIP_FIGURES
from hausbuch.schemas.employment import employer_key as _shared_employer_key
from hausbuch.schemas.enums import (
    AcquisitionCostKind,
    DepositForm,
    DocumentSubtype,
    ExtractionKind,
    PropertyKind,
    RentModel,
)
from hausbuch.schemas.extraction import _Typing
from hausbuch.schemas.proposal import (
    ExistingLoan,
    ExistingOwner,
    ExistingPayRow,
    ExistingProperty,
    ExistingUnit,
    PrefillContext,
    ProposalRecord,
)

from .extraction_parse import _MONTHS

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
# A new loan's fields that are proposed only when the contract states them.
_LOAN_OPTIONAL = ("contract_number", "fixed_until", "follow_up_rate", "account_iban", "signed_on")
# A loan contract's clause that only interest is paid until repayment starts (Luis's Sparkasse
# contract, 8 Nov 2026: "Bis zum Tilgungsbeginn sind nur die Sollzinsen zu diesen Terminen zu
# zahlen"; also "nur Zinsen", "nur Sollzinsen"), searched in the casefolded clause. A clause
# without "nur" before the interest ("Die Zinsen sind ... zu zahlen") does not tick the box.
_INTEREST_ONLY = re.compile(r"\bnur (?:die )?(?:soll)?zinsen\b")
# A handover protocol is linked to the tenancy whose move-in is this close to its date.
_HANDOVER_WINDOW_DAYS = 31
# An invoice whose subject or title names one of these is a financing cost (D35). Deliberately
# not finanzierung / kaufvertrag / zwangsvollstreckungsunterwerfung: a Kaufvertrag bill names
# its Finanzierungsvollmacht, a Grundschuld bill the Kaufvertrag it finances.
_FINANCING_TERMS = ("grundschuld", "hypothek", "darlehen")
_FINANCING_NOTE = (
    "Diese Rechnung betrifft die Finanzierung (Grundschuld, Hypothek oder Darlehen). Solche "
    "Kosten sind Geldbeschaffungskosten: im Jahr der Zahlung als Werbungskosten abziehbar, "
    "nicht Teil der Anschaffungsnebenkosten und damit nicht der AfA-Grundlage. Darum wird "
    "kein Nebenkosten-Eintrag vorgeschlagen; die Zahlung wird beim Abgleich des Kontoauszugs "
    "als Darlehenskosten gebucht. Enthält dieselbe Rechnung auch einen Anteil für den Kauf, "
    "bitte diesen von Hand als Nebenkosten erfassen."
)
# A Kautionsnachweis whose tenancy cannot be told (D42): typing only, and why.
_DEPOSIT_SEVERAL_NOTE = (
    "Die Kaution passt zu keinem oder zu mehreren Mietverhältnissen; bitte den Eingang beim "
    "richtigen Mietverhältnis von Hand erfassen (Kaution erhalten)."
)
_DEPOSIT_NONE_NOTE = (
    "Es gibt noch kein Mietverhältnis, zu dem die Kaution gehören könnte; bitte zuerst den "
    "Mietvertrag erfassen und den Eingang dann beim Mietverhältnis eintragen."
)
_FINANCING_BELEG_NOTE = (
    "Diese Rechnung ist schon als Beleg eines Nebenkosten-Eintrags hinterlegt. Der Eintrag "
    "gehört zu den Darlehenskosten: bitte ihn aus den Nebenkosten entfernen."
)


# --- Entry point ---------------------------------------------------------------------


def build_proposal(
    kind: ExtractionKind | None, extraction: _Typing, context: PrefillContext
) -> list[ProposalRecord]:
    """The records a document proposes (fixture prefill.yaml). `extraction` is the kind's
    extraction model (schemas/extraction.py), `_Typing` when kind is None."""
    b = _Builder(kind, extraction, context)
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
        case ExtractionKind.GRUNDSTEUERBESCHEID | ExtractionKind.WIRTSCHAFTSPLAN:
            b.link_property(_resolve_property(extraction, context))
        case ExtractionKind.HAUSGELDABRECHNUNG:
            b.hausgeldabrechnung()
        case ExtractionKind.ZINSBESCHEINIGUNG:
            b.zinsbescheinigung()
        case ExtractionKind.DEPOSIT_RECORD:
            b.deposit_record()
        case ExtractionKind.LOHNSTEUERBESCHEINIGUNG:
            b.wage_certificate()
        case ExtractionKind.GEHALTSABRECHNUNG:
            b.payslip()
        case ExtractionKind.EINKOMMENSTEUERBESCHEID:
            b.income_tax_assessment()
        case _:  # statement, unknown: typing only
            pass
    return b.records


# --- The builder -----------------------------------------------------------------------


class _Builder:
    def __init__(
        self, kind: ExtractionKind | None, extraction: _Typing, context: PrefillContext
    ) -> None:
        self.kind = kind
        self.x = extraction
        self.ctx = context
        self.records: list[ProposalRecord] = []
        # A Grundbuchamt Kostenrechnung may be typed bescheid, which has no subject (D35, F1).
        self.financing = (
            kind is ExtractionKind.INVOICE
            and names_financing(self.ev("subject").value, self.ev("title").value)
        ) or (kind is ExtractionKind.BESCHEID and names_financing(self.ev("title").value))

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
        # A financing invoice carries no subtype, whatever the classifier said (D35).
        if c.subtype is not None and not self.financing:
            fields["subtype"] = _code_value(c.subtype.value, c.confidence)
        for name in ("title", "document_date", "tax_year"):
            if self.stated(name):
                fields[name] = self.ev(name)
        if self.kind is ExtractionKind.INVOICE:
            tax_year = self._invoice_tax_year()
            if tax_year is not None:
                fields["tax_year"] = tax_year
        if self.kind in _PAY_KINDS and "tax_year" not in fields:
            tax_year = self._pay_tax_year()
            if tax_year.value is not None:
                fields["tax_year"] = tax_year
        notes = []
        if self.financing:
            notes.append(_FINANCING_NOTE)
            if any(cost.document_id == self.ctx.document_id for cost in self.ctx.costs):
                notes.append(_FINANCING_BELEG_NOTE)
        self.add("typing", "typing", "create", fields, notes=notes)

    def _invoice_tax_year(self) -> ExtractedValue | None:
        """The filing year of an invoice (D36): the year of due_on, else of document_date;
        neither stated, nothing. The year a cost counts in stays the payment's (D31)."""
        for name in ("due_on", "document_date"):
            on = _iso_date(self.ev(name).value)
            if on is not None:
                return _derived(on.year, [(name, self.ev(name))])
        return None

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
                keyed={"address.street": _street_key, "address.house_number": _house_number_key},
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
        keyed: dict[str, Callable[[Any], str]] | None = None,
    ) -> None:
        """unchanged when every stated field the context holds equals it, else patch with
        the differing ones; a differing milestone date gets its own correction record.
        `keyed` fields compare by that key instead (an address spelled another way is the
        same address, so the stored spelling is not proposed for change)."""
        keyed = keyed or {}

        def same(f: str, value: JsonValue) -> bool:
            if f in keyed:
                return keyed[f](value) == keyed[f](existing[f])
            return _same(value, existing[f])

        compared = {f: v for f, v in proposed.items() if v.value is not None and f in existing}
        corrections = {
            f: v for f, v in compared.items() if f in milestones and not same(f, v.value)
        }
        kept = {f: v for f, v in compared.items() if f not in corrections}
        differing = {f: v for f, v in kept.items() if not same(f, v.value)}
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
        if self.financing:
            # D35: no cost record at all (not even unchanged or patch); the note on typing
            # says why. The loan is linked only when it is not a guess.
            self.link_property(prop)
            loans = [loan for loan in self.ctx.loans if loan.property_id == prop.id]
            if len(loans) == 1:
                self.link("link:loan", loans[0].id)
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
            "follow_up_rate": self.ev("follow_up_rate"),
            "disbursed_on": self.ev("disbursed_on"),
            "first_payment_on": self.ev("first_payment_on"),
            "account_iban": self.ev("account_iban"),
            "signed_on": self.ev("signed_on"),
        }
        interest_only = self._interest_only()
        loan = _match_loan(self.x, self.ctx.loans)
        if loan is None:
            fields = {
                f: v for f, v in values.items() if f not in _LOAN_OPTIONAL or v.value is not None
            }
            if interest_only is not None:
                fields["interest_only_before_first_payment"] = interest_only
            self.add("loan", "loan", "create", fields)
        else:
            # LoanPatch has no signed_on: the contract date is set when the loan is created.
            keys = ("lender", "contract_number", "signed_on")
            fields = {f: v for f, v in values.items() if f not in keys and v.value is not None}
            # The one term the context holds: proposed only when it changes false to true.
            if interest_only is not None and not loan.interest_only_before_first_payment:
                fields["interest_only_before_first_payment"] = interest_only
            self.add("loan", "loan", "patch" if fields else "unchanged", fields, target_id=loan.id)
        self.link("link:loan", None if loan is None else loan.id)

    def _interest_only(self) -> ExtractedValue | None:
        """interest_only_before_first_payment = true, derived from the contract's clause, when
        the clause says only interest is paid until repayment starts; else None (not
        proposed, the loan keeps false). Code decides, not the model (principle 1)."""
        clause = self.ev("interest_only_clause")
        if clause.value is None or not _INTEREST_ONLY.search(_norm(clause.value)):
            return None
        return _derived(True, [("interest_only_clause", clause)])

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
        unit, exact = self._lease_unit()
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
        if not exact and unit_value.confidence == "high":
            # Found inside a longer label, not equal to it: the review should look twice.
            unit_value = unit_value.model_copy(update={"confidence": "medium"})
        rent_model = _derived(
            _rent_model(self.ev("rent_clause").value), [("rent_clause", self.ev("rent_clause"))]
        )
        deposit_form = self._deposit_form()
        if tenancy is None:
            fields = {"unit_id": unit_value, "start_date": self.ev("start_date")}
            for name in ("signed_on", "fixed_term_until"):
                if self.stated(name):
                    fields[name] = self.ev(name)
            fields["rent_model"] = rent_model
            if self.stated("deposit_amount"):
                fields["deposit_amount"] = self.ev("deposit_amount")
            if deposit_form is not None:
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
            if deposit_form is not None:
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

    def _deposit_form(self) -> ExtractedValue | None:
        """The form the deposit clause names; else, when the lease states a deposit amount
        above 0, a money deposit (cash_account), derived from the amount and the clause: a
        lease that names no Bürgschaft asks for money ("eine Kaution in Höhe von 1.000,00 €
        ... zu überweisen", Luis's room lease, 7 Nov 2026), and a deposit amount with the
        form "none" is not a valid tenancy. The derived form is low confidence whenever the
        clause named no form (the code inferred it, review 7 Nov 2026). An amount that is not
        a number ("drei Nettokaltmieten") proposes the form as missing, so the form asks for
        it next to the amount. None when neither is stated."""
        clause, amount = self.ev("deposit_clause"), self.ev("deposit_amount")
        mapped = _mapped(clause, _deposit_form)
        if mapped.value is not None:
            return mapped
        inputs = [("deposit_amount", amount), ("deposit_clause", mapped)]
        value = _decimal(amount.value)
        if value is not None and value > 0:
            return _derived(DepositForm.CASH_ACCOUNT.value, inputs)
        if amount.value is not None and value is None:
            return _derived(None, inputs)
        return mapped if clause.value is not None else None

    def _lease_unit(self) -> tuple[ExistingUnit | None, bool]:
        """The tenancy's unit, and whether it was found exactly (an equal label, or the
        property's only unit); a unit found inside a longer label is not exact."""
        prop = _resolve_property(self.x, self.ctx)
        if prop is None:
            return None, True
        units = [u for u in self.ctx.units if u.property_id == prop.id]
        label = self.ev("unit_label").value
        if label is not None:
            return _unit_by_label(units, str(label))
        return (units[0] if len(units) == 1 else None), True

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

    # -- Kautionsnachweis (step 2, D42)

    def deposit_record(self) -> None:
        """A deposit_received record when amount and date are stated and exactly one tenancy
        is found: the one whose agreed Kaution equals the amount, else the workspace's only
        tenancy. Several or none: typing only, with a note. A receipt already recorded on
        that tenancy with the same amount and date: unchanged."""
        amount, received_on = self.ev("amount"), self.ev("received_on")
        on = _iso_date(received_on.value)
        if _decimal(amount.value) is None or on is None:
            return
        tenancies = self.ctx.tenancies
        by_amount = [
            t
            for t in tenancies
            if t.deposit_amount is not None and _same(amount.value, _json(t.deposit_amount))
        ]
        if len(by_amount) == 1:
            tenancy = by_amount[0]
            tenancy_value = _derived(str(tenancy.id), [("amount", amount)])
        elif not by_amount and len(tenancies) == 1:
            # Nothing in the document points at it: the only candidate, at low confidence.
            tenancy = tenancies[0]
            tenancy_value = _code_value(str(tenancy.id), "low")
        else:
            self.records[0].notes.append(
                _DEPOSIT_SEVERAL_NOTE if by_amount or tenancies else _DEPOSIT_NONE_NOTE
            )
            return
        fields = {"amount": amount, "received_on": received_on}
        recorded = sorted(
            (
                r
                for r in tenancy.deposit_receipts
                if r.received_on == on and _same(amount.value, _json(r.amount))
            ),
            key=lambda r: r.amount,
        )
        if recorded:
            current = {"amount": _json(recorded[0].amount), "received_on": on.isoformat()}
            self.add(
                "deposit_received",
                "deposit_received",
                "unchanged",
                fields,
                target_id=tenancy.id,
                current=current,
            )
        else:
            fields["tenancy_id"] = tenancy_value
            self.add("deposit_received", "deposit_received", "create", fields)
        self.link("link:tenancy", tenancy.id)

    # -- the two certificates (phase 3 §9)

    def zinsbescheinigung(self) -> None:
        """link:loan by lender and contract number, else the only loan; and the certified
        interest, proposed even without a loan (the user picks it in the review)."""
        loan = _match_loan(self.x, self.ctx.loans)
        if loan is not None:
            loan_value = _derived(
                str(loan.id),
                [("lender", self.ev("lender")), ("contract_number", self.ev("contract_number"))],
            )
        elif len(self.ctx.loans) == 1:
            loan = self.ctx.loans[0]
            loan_value = _code_value(str(loan.id), "low")  # nothing in the document names it
        else:
            loan_value = ExtractedValue.missing()
        if loan is not None:
            self.link("link:loan", loan.id)
        self.add(
            "tax_input:interest_certified",
            "tax_input",
            "create",
            {
                "tax_year": self.ev("tax_year"),
                "loan_id": loan_value,
                "value.amount": self.ev("interest_paid"),
            },
        )

    def hausgeldabrechnung(self) -> None:
        """link:property by address; the Rücklage the WEG spent, only for a property found
        (a tax input belongs to one)."""
        prop = _resolve_property(self.x, self.ctx)
        if prop is None:
            return
        self.link_property(prop)
        self.add(
            "tax_input:ruecklage_spent",
            "tax_input",
            "create",
            {"tax_year": self.ev("tax_year"), "value.amount": self.ev("ruecklage_spent")},
        )

    # -- the two pay documents (phase 4b §8.2)

    def wage_certificate(self) -> None:
        """link:owner and return_input:wage_certificate with every certificate field."""
        owner = self._owner()
        tax_year = self._pay_tax_year()
        tax_class = self.ev("tax_class")
        fields = {
            "owner_id": owner,
            "tax_year": tax_year,
            "value.period_from": self.ev("period_from"),
            "value.period_to": self.ev("period_to"),
            "value.employer": self.ev("employer_name"),
            "value.tax_class": _mapped(tax_class, _tax_class_number),
            "value.tax_class_factor": _mapped(tax_class, _tax_class_factor),
            "value.church_tax_marked": _mapped(self.ev("church_tax_mark"), _church_tax_marked),
            "value.child_allowance_count": self.ev("child_allowance_count"),
        }
        for name in CERTIFICATE_AMOUNTS:
            fields[f"value.{name}"] = self.ev(name)
        notes = []
        unread = [name for name in CERTIFICATE_AMOUNTS if fields[f"value.{name}"].value is None]
        if unread:
            notes.append(
                f"Nicht gelesen: {', '.join(CERTIFICATE_LINES[n] for n in unread)}. Steht dort "
                "ein Betrag, bitte eintragen; ein leeres Feld wird als 0 übernommen und als "
                "nicht gelesen vermerkt."
            )
        replaced = self._replaced("wage_certificate", owner, tax_year, fields)
        if replaced is not None and replaced.document_id == self.ctx.document_id:
            notes.append(
                "Diese Lohnsteuerbescheinigung ist schon übernommen; geänderte Werte ersetzen "
                "den Eintrag."
            )
        elif replaced is not None:
            notes.append(
                "Für diesen Zeitraum gibt es von diesem Arbeitgeber schon eine "
                "Lohnsteuerbescheinigung; diese ersetzt sie (eine berichtigte Bescheinigung)."
            )
        notes += self._pay_row_notes("wage_certificate", owner, tax_year, fields)
        self.add("return_input:wage_certificate", "return_input", "create", fields, notes=notes)

    def payslip(self) -> None:
        """link:owner and return_input:payslip: the month's and the year-to-date figures,
        months_remaining_paid = 12 − month, the gross projection in a note (D105)."""
        owner = self._owner()
        tax_year = self._pay_tax_year()
        month = _mapped(self.ev("period_month"), _month_start)
        on = _iso_date(month.value)
        tax_class = self.ev("tax_class")
        fields = {
            "owner_id": owner,
            "tax_year": tax_year,
            "value.period_month": month,
            "value.employer": self.ev("employer_name"),
            "value.tax_class": _mapped(tax_class, _tax_class_number),
            "value.tax_class_factor": _mapped(tax_class, _tax_class_factor),
            "value.months_remaining_paid": _derived(
                None if on is None else 12 - on.month, [("period_month", month)]
            ),
        }
        for figure in PAYSLIP_FIGURES:
            fields[f"value.{figure}.month"] = self.ev(f"{figure}_month")
            fields[f"value.{figure}.ytd"] = self.ev(f"{figure}_ytd")
        notes = []
        no_month = [f for f in PAYSLIP_FIGURES if fields[f"value.{f}.month"].value is None]
        if no_month:
            notes.append(
                f"Nicht gelesen (Monat): {', '.join(PAYSLIP_LINES[f] for f in no_month)}. Steht "
                "dort ein Betrag, bitte eintragen; ein leeres Monatsfeld wird als 0 übernommen."
            )
        no_ytd = [f for f in PAYSLIP_FIGURES if fields[f"value.{f}.ytd"].value is None]
        if no_ytd:
            notes.append(
                f"Nicht gelesen (Jahreswerte): {', '.join(PAYSLIP_LINES[f] for f in no_ytd)}. "
                "Steht dort ein Betrag, bitte eintragen; ein leeres Jahresfeld bleibt leer und "
                "wird hochgerechnet."
            )
        if on is not None:
            notes.append(
                _projection_note(on, fields["value.gross.month"], fields["value.gross.ytd"])
            )
            if self._replaced("payslip", owner, tax_year, fields) is not None:
                notes.append(
                    f"Für {MONTH_NAMES[on.month - 1]} {on.year} gibt es von diesem Arbeitgeber "
                    "schon eine Gehaltsabrechnung; diese ersetzt sie."
                )
        notes += self._pay_row_notes("payslip", owner, tax_year, fields)
        self.add("return_input:payslip", "return_input", "create", fields, notes=notes)

    def _owner(self) -> ExtractedValue:
        """link:owner and the owner_id value: the one owner of this workspace whose name is
        the employee's (by name tokens); else no target and no value (the user picks). The
        context holds this workspace's owners only."""
        name = self.ev("employee_name")
        owner, exact = _match_owner(name.value, self.ctx.owners)
        self.link("link:owner", None if owner is None else owner.id)
        value = _derived(None if owner is None else str(owner.id), [("employee_name", name)])
        if owner is not None and not exact and value.confidence == "high":
            value = value.model_copy(update={"confidence": "medium"})
        return value

    def _pay_tax_year(self) -> ExtractedValue:
        """A certificate's stated year, else the year of its period; a payslip's month's."""
        if self.kind is ExtractionKind.LOHNSTEUERBESCHEINIGUNG:
            if self.stated("tax_year"):
                return self.ev("tax_year")
            sources = [(n, self.ev(n)) for n in ("period_from", "period_to")]
        else:
            sources = [("period_month", _mapped(self.ev("period_month"), _month_start))]
        for name, source in sources:
            on = _iso_date(source.value)
            if on is not None:
                return _derived(on.year, [(name, source)])
        return ExtractedValue.missing()

    def _replaced(
        self,
        kind: str,
        owner: ExtractedValue,
        tax_year: ExtractedValue,
        fields: dict[str, ExtractedValue],
    ) -> ExistingPayRow | None:
        """The current row the proposed one would replace, as the apply decides it."""
        if owner.value is None or tax_year.value is None:
            return None
        return replaced_pay_row(
            kind,
            self.ctx.pay_rows,
            owner_id=UUID(str(owner.value)),
            tax_year=int(tax_year.value),
            document_id=self.ctx.document_id,
            value={name: v.value for name, v in fields.items()},
        )

    def _pay_row_notes(
        self,
        kind: str,
        owner: ExtractedValue,
        tax_year: ExtractedValue,
        fields: dict[str, ExtractedValue],
    ) -> list[str]:
        """This document already recorded under another owner or year (the apply refuses it
        until that row is withdrawn), and the owner's rows from an employer spelled
        differently but likely the same (one name's words all in the other's). Notes only:
        no proposed value changes."""
        notes = []
        names = {o.id: o.full_name for o in self.ctx.owners}
        owner_id = None if owner.value is None else UUID(str(owner.value))
        for row in self.ctx.pay_rows:
            if row.kind != kind or row.document_id != self.ctx.document_id:
                continue
            if row.owner_id != owner_id or row.tax_year != tax_year.value:
                notes.append(
                    f"Dieses Dokument ist schon für {names.get(row.owner_id, 'einen Eigentümer')}"
                    f" im Jahr {row.tax_year} übernommen. Für einen anderen Eigentümer oder ein "
                    "anderes Jahr zuerst jenen Eintrag zurückziehen."
                )
        employer = fields["value.employer"].value
        if owner_id is None or not employer:
            return notes
        key, words = employer_key(employer), _employer_words(employer)
        seen = set()
        for row in self.ctx.pay_rows:
            if row.owner_id != owner_id or employer_key(row.employer) in seen | {key}:
                continue
            stored = _employer_words(row.employer)
            if words and stored and (words <= stored or stored <= words):
                seen.add(employer_key(row.employer))
                notes.append(
                    f"Für diesen Eigentümer gibt es schon Zeilen vom Arbeitgeber "
                    f"'{row.employer}'; prüfe, ob es derselbe Arbeitgeber ist."
                )
        return notes

    def income_tax_assessment(self) -> None:
        """income_input:bescheid_zve: the year and the zvE; the notes of D129 (§8.4)."""
        tax_year = self.ev("tax_year")
        fields = {"tax_year": tax_year, "value.amount": self.ev("taxable_income")}
        notes = []
        if fields["value.amount"].value is None:
            notes.append(_ZVE_UNREAD_NOTE)
        year = tax_year.value
        for row in self.ctx.bescheid_rows:
            if row.document_id == self.ctx.document_id and row.tax_year != year:
                notes.append(
                    f"Dieser Bescheid ist schon als zvE für {row.tax_year} übernommen. Für ein "
                    "anderes Jahr zuerst jenen Eintrag zurückziehen."
                )
        replaced = _first(
            sorted(
                (r for r in self.ctx.bescheid_rows if year is not None and r.tax_year == year),
                key=lambda r: str(r.id),
            )
        )
        if replaced is not None and replaced.document_id == self.ctx.document_id:
            notes.append(
                "Dieser Bescheid ist schon übernommen; ein geänderter Betrag ersetzt den Eintrag."
            )
        elif replaced is not None and replaced.document_date is not None:
            notes.append(
                f"Für {year} gibt es schon einen Bescheid; dieser ersetzt den Bescheid vom "
                f"{replaced.document_date:%d.%m.%Y} (ein Änderungsbescheid)."
            )
        elif replaced is not None:
            notes.append(
                f"Für {year} gibt es schon einen Bescheid; dieser ersetzt den bisher "
                "übernommenen Bescheid (ein Änderungsbescheid)."
            )
        assessment = self.ev("assessment_type").value
        if assessment is not None and not _JOINT_ASSESSMENT.search(str(assessment).casefold()):
            notes.append(_NOT_JOINT_NOTE)
        addressee = self.ev("addressee").value
        if (
            addressee is not None
            and self.ctx.owners
            and not _names_an_owner(addressee, self.ctx.owners)
        ):
            notes.append(_NOT_THE_OWNERS_NOTE)
        self.add("income_input:bescheid_zve", "income_input", "create", fields, notes=notes)


# --- Matching existing records ----------------------------------------------------------


def _norm(value: Any) -> str:
    """Casefold, whitespace collapsed."""
    return " ".join(str(value).split()).casefold()


_LABEL_TOKEN = re.compile(r"[^\W\d_]+|\d+")
_LABEL_FILLERS = frozenset({"nr", "no", "nummer"})


def _label_key(label: str) -> tuple[str, ...]:
    """A unit label's tokens: runs of letters or of digits, casefolded, a number without its
    leading zeros, the filler words nr / no / nummer dropped ("WE03" -> we 3, "Zimmer Nr. 1"
    -> zimmer 1). Punctuation and brackets only separate."""
    tokens = []
    for token in _LABEL_TOKEN.findall(label.casefold()):
        if token.isdigit():
            tokens.append(token.lstrip("0") or "0")
        elif token not in _LABEL_FILLERS:
            tokens.append(token)
    return tuple(tokens)


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


_ROOM = "zimmer"


def _may_be_inside(unit_key: tuple[str, ...], label_key: tuple[str, ...]) -> bool:
    """Whether a unit key may match inside a longer label: it has both a word and a number
    (a bare "1" is inside "Wohnung 1. OG", a bare "EG" or "Zimmer" names no one unit), and a
    label naming a room only matches a unit whose key names one too (a room lease is never
    booked on the flat: a tenancy cannot move to another unit)."""
    has_word = any(not t.isdigit() for t in unit_key)
    has_number = any(t.isdigit() for t in unit_key)
    if not (has_word and has_number):
        return False
    return _ROOM not in label_key or _ROOM in unit_key


def _unit_by_label(units: Sequence[ExistingUnit], label: str) -> tuple[ExistingUnit | None, bool]:
    """The unit a lease's label names, among the property's units (fixture prefill.yaml,
    "lease unit"), and whether its label was equal: the one unit whose key equals the
    label's; else the one unit whose key occurs as a run of whole tokens inside it ("Zimmer
    Nr. 1 (WE 3, Zimmer 1)" -> Zimmer 1, never Zimmer 10), within _may_be_inside. Several
    candidates or none: None, never a guess."""
    key = _label_key(label)
    equal = [u for u in units if _label_key(u.label) == key]
    if equal:
        return (equal[0], True) if len(equal) == 1 else (None, True)
    inside = [
        u
        for u in units
        if _may_be_inside(_label_key(u.label), key) and _contains(key, _label_key(u.label))
    ]
    return (inside[0], False) if len(inside) == 1 else (None, False)


def names_financing(*texts: Any) -> bool:
    """Whether any of the texts names a financing term (Grundschuld, Hypothek, Darlehen),
    anywhere, case-insensitive (D35). Public for services.matching, which decides a
    financing invoice exactly as the proposal does: an invoice by its subject or title, a
    Bescheid by its title."""
    return any(
        term in _norm(text) for text in texts if text is not None for term in _FINANCING_TERMS
    )


def _first[T](items: Iterable[T]) -> T | None:
    """The match with the smallest id, so the result does not depend on list order."""
    found = sorted(items, key=lambda item: item.id)
    return found[0] if found else None


def _address_of(extraction: _Typing) -> tuple[str, str, str | None] | None:
    """The street and house number keys and the postal code, or None when the document
    states no street or no house number. The postal code counts only when its quote
    contains it (`value_in_quote`): a contract may name the flat by its land register entry
    and street alone (Luis's real Kaufvertrag, 7 Nov 2026), and an unverified code must
    neither find nor block a match."""
    street, number = (_stated_value(extraction, name) for name in ("street", "house_number"))
    if street is None or number is None:
        return None
    postal = getattr(extraction, "postal_code", None)
    verified = (
        isinstance(postal, ExtractedValue)
        and postal.value is not None
        and "value_in_quote" in postal.checks
    )
    return (
        _street_key(street),
        _house_number_key(number),
        _norm(postal.value) if verified else None,
    )


def _stated_value(extraction: _Typing, name: str) -> Any:
    value = getattr(extraction, name, None)
    return value.value if isinstance(value, ExtractedValue) else None


def _match_property(
    extraction: _Typing, properties: Sequence[ExistingProperty]
) -> ExistingProperty | None:
    """By street + house number (compared by key, so "Lindenstr. 12" finds "Lindenstraße
    12") and the postal code. Without a verified postal code: the city instead when the
    document states one, and only when exactly one property fits, so nothing is guessed."""
    address = _address_of(extraction)
    if address is None:
        return None
    street, number, postal = address
    fits = [
        p
        for p in properties
        if (_street_key(p.address.street), _house_number_key(p.address.house_number))
        == (street, number)
    ]
    if postal is not None:
        return _first(p for p in fits if _norm(p.address.postal_code) == postal)
    city = _stated_value(extraction, "city")
    if city is not None:
        fits = [p for p in fits if _norm(p.address.city) == _norm(city)]
    return fits[0] if len(fits) == 1 else None


def _street_key(value: Any) -> str:
    """Casefolded (ß as ss), spaces, hyphens and dots removed, and a street ending in the
    abbreviation "str" spelled out: "Lindenstr.", "Linden-Straße" and "Lindenstrasse" are
    one key."""
    key = re.sub(r"[\s.\-]", "", str(value).casefold())
    return f"{key}asse" if key.endswith("str") else key


def _house_number_key(value: Any) -> str:
    """Casefolded, spaces and trailing dots removed: "12 a" is "12a". A hyphen or slash
    stays significant, so "1-2" (or "1 - 2") is never "12" (review, 7 Nov 2026)."""
    return re.sub(r"\s", "", str(value).casefold()).rstrip(".")


def _resolve_property(extraction: _Typing, context: PrefillContext) -> ExistingProperty | None:
    """The property a document belongs to: by its address when it states one, else the only
    property of the workspace."""
    only = context.properties[0] if len(context.properties) == 1 else None
    address = _address_of(extraction)
    if address is None:
        return only
    found = _match_property(extraction, context.properties)
    # Without a verified postal code the address was never decisive: as before, the
    # workspace's only property when the street does not find it.
    return found if found is not None or address[2] is not None else only


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
    """The form a deposit clause names. "bar" counts only as a word, so "vereinbart" is not
    a cash deposit (7 Nov 2026)."""
    t = _norm(clause)
    if "bankbürgschaft" in t or "bankburgschaft" in t:
        return DepositForm.BANK_GUARANTEE
    if "bürgschaft" in t or "burgschaft" in t:
        return DepositForm.SURETY
    if any(
        w in t
        for w in (
            "barkaution",
            "geldkaution",
            "kautionskonto",
            "sparbuch",
            "barzahlung",
            "bareinzahlung",
            "barsicherheit",
            "bargeld",
        )
    ) or re.search(r"\bbar\b", t):
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


# --- The pay documents' values (phase 4b §8.2) ------------------------------------------

_PAY_KINDS = frozenset({ExtractionKind.LOHNSTEUERBESCHEINIGUNG, ExtractionKind.GEHALTSABRECHNUNG})
# D129: the Bescheid's notes. The app computes the joint splitting tariff only (D82, D83).
_JOINT_ASSESSMENT = re.compile(r"zusammen\s*-?\s*veranlag")
_NOT_JOINT_NOTE = (
    "Laut Bescheid ist das keine Zusammenveranlagung; die App rechnet mit dem "
    "Splittingtarif. Bitte prüfen, ob der Betrag trotzdem übernommen werden soll."
)
_NOT_THE_OWNERS_NOTE = (
    "Der Bescheid scheint nicht an die Eigentümer gerichtet (etwa der Einkommensnachweis "
    "eines Mieters). Bitte prüfen, bevor der Betrag übernommen wird."
)
_ZVE_UNREAD_NOTE = (
    "Das zu versteuernde Einkommen wurde nicht gelesen; bitte den Betrag aus dem Bescheid "
    "eintragen (die Zeile „zu versteuerndes Einkommen“)."
)
# The certificate's line of each amount, for the note naming the lines not read (§5.1).
CERTIFICATE_LINES: dict[str, str] = {
    "gross": "Nr. 3",
    "income_tax": "Nr. 4",
    "soli": "Nr. 5",
    "church_tax": "Nr. 6",
    "church_tax_spouse": "Nr. 7",
    "replacement_benefits": "Nr. 15",
    "short_time_allowance": "Nr. 15a",
    "employer_commute_tax_free": "Nr. 17",
    "employer_commute_flat_taxed": "Nr. 18",
    "pension_employer": "Nr. 22a",
    "pension_employee": "Nr. 23a",
    "pension_employee_professional": "Nr. 23b",
    "subsidy_health": "Nr. 24a",
    "subsidy_private_health": "Nr. 24b",
    "subsidy_care": "Nr. 24c",
    "health_employee": "Nr. 25",
    "care_employee": "Nr. 26",
    "unemployment_employee": "Nr. 27",
}
# The payslip's name of each figure, for the note naming the figures not read.
PAYSLIP_LINES: dict[str, str] = {
    "gross": "Steuer-Brutto",
    "income_tax": "Lohnsteuer",
    "soli": "Solidaritätszuschlag",
    "church_tax": "Kirchensteuer",
    "pension_employee": "Rentenversicherung",
    "health_employee": "Krankenversicherung",
    "care_employee": "Pflegeversicherung",
    "unemployment_employee": "Arbeitslosenversicherung",
    "tax_free_benefits": "steuerfreie Bezüge",
}
MONTH_NAMES = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
    "Oktober", "November", "Dezember",
)  # fmt: skip
_NAME_TOKEN = re.compile(r"[^\W\d_]+")
_NAME_FILLERS = frozenset({"dr", "prof", "herr", "frau", "von", "zu", "van", "der", "de"})
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_FACTOR = re.compile(r"(?<![0-9])0[,.][0-9]{1,4}(?![0-9])")
_ARABIC_CLASS = re.compile(r"(?<![0-9.,])[1-6](?![0-9.,])")
_ROMAN_CLASS = re.compile(r"(?<![a-z])(vi|iv|v|iii|ii|i)(?![a-z])")
_NO_CHURCH_TAX = frozenset({"", "-", "--", "kein", "keine", "ohne", "nein", "0", "entfällt"})
_CHURCH_CODE = re.compile(r"[a-z]{2}")
_MONTH_YEAR = re.compile(r"(?<![0-9])([0-9]{1,2})\s*[./-]\s*([0-9]{4})(?![0-9])")
_WORD_MONTH_YEAR = re.compile(r"([a-zäöü]+)\.?\s+([0-9]{4})")


def _name_key(name: Any) -> frozenset[str]:
    """A person's name as a set of tokens: letters only, casefolded, ß as ss, titles and
    particles dropped, so "Musterfrau, Erika" and "Erika Musterfrau" are one key."""
    text = str(name).casefold().replace("ß", "ss")
    return frozenset(t for t in _NAME_TOKEN.findall(text) if t not in _NAME_FILLERS)


def _names_an_owner(addressee: Any, owners: Sequence[ExistingOwner]) -> bool:
    """D129: whether the Bescheid's addressee names at least one owner, by name tokens like
    D96's employee name: an owner whose tokens equal the addressee's, or one of at least two
    tokens all printed in it ("Max und Erika Mustermann" names both owners)."""
    key = _name_key(addressee)
    if not key:
        return False
    for owner in owners:
        owner_key = _name_key(owner.full_name)
        if owner_key and (owner_key == key or (len(owner_key) >= 2 and owner_key <= key)):
            return True
    return False


def _match_owner(name: Any, owners: Sequence[ExistingOwner]) -> tuple[ExistingOwner | None, bool]:
    """The one owner whose name tokens equal the employee's (exact), else the one owner of at
    least two name tokens all printed in it (a middle name printed: not exact); several or
    none: None. `owners` is the workspace's own list (RLS-scoped by prefill_context)."""
    key = _name_key(name) if name is not None else frozenset()
    if not key:
        return None, False
    exact = [o for o in owners if _name_key(o.full_name) == key]
    if exact:
        return (exact[0], True) if len(exact) == 1 else (None, False)
    within = [
        o for o in owners if len(_name_key(o.full_name)) >= 2 and _name_key(o.full_name) <= key
    ]
    return (within[0], False) if len(within) == 1 else (None, False)


def _tax_class_parts(text: Any) -> tuple[int, bool] | None:
    """ "4 / 0,950" -> (4, True); "IV" -> (4, False); "Steuerklasse 1" -> (1, False). A Faktor
    (a number below 1) counts beside class IV only (§39f). None when no class is named."""
    t = _norm(text)
    factor = _FACTOR.search(t)
    rest = t if factor is None else f"{t[: factor.start()]} {t[factor.end() :]}"
    arabic = _ARABIC_CLASS.search(rest)
    if arabic is not None:
        number = int(arabic.group())
    else:
        words = rest.replace("steuerklasse", " ").replace("faktor", " ").replace("stkl", " ")
        roman = _ROMAN_CLASS.search(words)
        if roman is None:
            return None
        number = _ROMAN[roman.group(1)]
    return number, factor is not None and number == 4


def _tax_class_number(text: Any) -> int | None:
    parts = _tax_class_parts(text)
    return None if parts is None else parts[0]


def _tax_class_factor(text: Any) -> bool | None:
    parts = _tax_class_parts(text)
    return None if parts is None else parts[1]


def _church_tax_marked(text: Any) -> bool | None:
    """The Kirchensteuermerkmal: a confession code ("ev", "rk", "ev/rk") is a mark; dashes
    or "keine" are none; anything else is not understood (None: the user decides)."""
    t = _norm(text).replace("–", "-").replace("—", "-")
    words = [w.strip(".") for w in re.split(r"[\s/,;]+", t)]
    if all(w in _NO_CHURCH_TAX or set(w) == {"-"} for w in words):
        return False
    if any(_CHURCH_CODE.fullmatch(w) for w in words):
        return True
    return None


def _month_start(value: Any) -> str | None:
    """The first of the month a payslip pays, from a date or a month written without a day
    ("08/2026", "August 2026"), as an ISO date."""
    on = _iso_date(value)
    if on is not None:
        return on.replace(day=1).isoformat()
    if not isinstance(value, str):
        return None
    t = _norm(value)
    found = _MONTH_YEAR.search(t)
    if found is not None and 1 <= int(found.group(1)) <= 12:
        return date(int(found.group(2)), int(found.group(1)), 1).isoformat()
    found = _WORD_MONTH_YEAR.search(t)
    if found is not None and found.group(1) in _MONTHS:
        return date(int(found.group(2)), _MONTHS[found.group(1)], 1).isoformat()
    return None


def _euro_de(amount: Decimal) -> str:
    """60000 -> "60.000,00 €"."""
    text = f"{amount.quantize(_CENTS, rounding=ROUND_HALF_UP):,.2f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def _projection_note(on: date, month: ExtractedValue, ytd: ExtractedValue) -> str:
    """What the payslip row projects the year's gross to (D105: ytd + month ×
    months_remaining_paid), so the remaining months are checked with their effect shown."""
    remaining = 12 - on.month
    advice = (
        f"Noch bezahlte Monate: {remaining} (bis Dezember); ändern, wenn das Gehalt vor "
        "Dezember endet (Mutterschutz, Jobwechsel)."
    )
    m, y = _decimal(month.value), _decimal(ytd.value)
    if m is None or y is None:
        return advice
    return (
        f"Hochrechnung {on.year}: {_euro_de(y + m * remaining)} (bis "
        f"{MONTH_NAMES[on.month - 1]} {_euro_de(y)}, dazu {remaining} × {_euro_de(m)}). " + advice
    )


def employer_key(name: Any) -> str:
    """An employer's name compared, the same way step 1's services and rules compare it
    (`schemas.employment.employer_key`: casefolded, whitespace collapsed)."""
    return _shared_employer_key(str(name))


_EMPLOYER_WORD = re.compile(r"[^\W_]+")


def _employer_words(name: Any) -> frozenset[str]:
    """An employer's name as its words, casefolded ("Muster GmbH & Co. KG" -> muster, gmbh,
    co, kg): only to spot one employer spelled two ways, never to match rows."""
    return frozenset(_EMPLOYER_WORD.findall(_norm(name)))


def replaced_pay_row(
    kind: str,
    rows: Sequence[ExistingPayRow],
    *,
    owner_id: UUID,
    tax_year: int,
    document_id: UUID,
    value: dict[str, Any],
) -> ExistingPayRow | None:
    """The current row a new pay row replaces (its supersedes_id), from the `value.*` fields
    proposed or confirmed. A payslip: the row of the same owner, year, employer and month
    (§8.2). A certificate: the row read from the same document, else the row of the same
    employer whose period overlaps this one (a corrected certificate; two periods of one
    employer that do not overlap are two certificates, both kept). Several candidates: None,
    nothing is guessed (the new row is added, the user withdraws the old one by hand)."""
    mine = [r for r in rows if r.kind == kind and r.owner_id == owner_id and r.tax_year == tax_year]
    employer = employer_key(value.get("value.employer") or "")
    if kind == "payslip":
        month = _iso_date(_month_start(value.get("value.period_month")))
        found = [
            r for r in mine if r.period_month == month and employer_key(r.employer) == employer
        ]
    else:
        found = [r for r in mine if r.document_id == document_id]
        start = _iso_date(value.get("value.period_from"))
        end = _iso_date(value.get("value.period_to"))
        if not found and start is not None and end is not None:
            found = [
                r
                for r in mine
                if employer_key(r.employer) == employer
                and r.period_from is not None
                and r.period_to is not None
                and r.period_from <= end
                and start <= r.period_to
            ]
    return found[0] if len(found) == 1 else None


def return_input_value(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    """The `value` of a return_input row from the user's confirmed values of its record (the
    `value.*` fields, dotted; any other key is ignored), as ReturnInputCreate reads it.

    An empty field (missing or null) is left to the schema's default, except a certificate
    amount: it becomes "0" and is named in `unread` (Q-O29: a line the model did not read and
    the user left empty is never a silent 0). `unread` is always computed here; a value given
    for it is ignored."""
    nested: dict[str, Any] = {}
    for name, value in values.items():
        head, dot, path = name.partition(".")
        if head != "value" or not dot or value is None or path == "unread":
            continue
        *parents, leaf = path.split(".")
        target = nested
        for part in parents:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"field {name!r} given both whole and by part")
            target = child
        if leaf in target:
            raise ValueError(f"field {name!r} given both whole and by part")
        target[leaf] = value
    if kind == "wage_certificate":
        unread = [n for n in CERTIFICATE_AMOUNTS if nested.get(n) is None]
        for n in unread:
            nested[n] = "0"
        nested["unread"] = unread
    return nested


__all__ = ["build_proposal", "employer_key", "replaced_pay_row", "return_input_value"]
