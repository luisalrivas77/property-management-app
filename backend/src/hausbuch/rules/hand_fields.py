"""Where each AI reading keeps the six hand-entry fields (phase 2 D37, D132).

The typing form's "Felder (von Hand)" (ManualDocumentFields: Betrag, Zeitraum von / bis,
Gegenpartei, Referenz, Notiz) is, over an AI reading, a view of some of the reading's own
fields. HAND_FIELD_MAP names them per extraction kind; hand_values reads them as the hand
types hold them. Both the document service (the form, hand_fields_into) and the pre-fill
builder (the typing record carries them since D132) use this one map. Pure: no I/O.
"""

from typing import Any, get_args

from pydantic import TypeAdapter, ValidationError

from hausbuch.schemas.document import HandField, ManualDocumentFields
from hausbuch.schemas.enums import ExtractionKind

HAND_FIELDS: tuple[HandField, ...] = get_args(HandField)

# D37: where each AI kind keeps the six hand-entry fields (ManualDocumentFields). A kind or a
# hand field not listed has no place: it is not shown, and a non-null value for it is refused.
HAND_FIELD_MAP: dict[ExtractionKind, dict[HandField, str]] = {
    ExtractionKind.INVOICE: {
        "amount_total": "amount_total",
        "counterparty": "sender",
        "reference": "reference",
        "notes": "subject",
    },
    ExtractionKind.BESCHEID: {
        "amount_total": "amount_total",
        "counterparty": "sender",
        "reference": "reference",
    },
    ExtractionKind.DEPOSIT_RECORD: {"amount_total": "amount"},
    ExtractionKind.ZINSBESCHEINIGUNG: {
        "amount_total": "interest_paid",
        "counterparty": "lender",
        "reference": "contract_number",
    },
    ExtractionKind.LOAN_CONTRACT: {"counterparty": "lender", "reference": "contract_number"},
    ExtractionKind.GRUNDSTEUERBESCHEID: {"reference": "reference"},
    ExtractionKind.STATEMENT: {"period_start": "period_start", "period_end": "period_end"},
    # D130, D131: every hand-entry field a letter took before the catalogue (the general
    # reading holds a lender and a number for the loan group; the rest are hand-only fields);
    # a Verwaltervertrag names its manager and its start, the rest by hand (10 Nov 2026).
    ExtractionKind.GENERAL: {
        "amount_total": "amount_total",
        "period_start": "period_start",
        "period_end": "period_end",
        "counterparty": "lender",
        "reference": "contract_number",
        "notes": "notes",
    },
    # Phase 3 D143: a payment proof's amount and payee.
    ExtractionKind.PAYMENT_PROOF: {"amount_total": "amount", "counterparty": "payee"},
    ExtractionKind.MANAGEMENT_CONTRACT: {
        "amount_total": "amount_total",
        "period_start": "start_date",
        "period_end": "period_end",
        "counterparty": "manager",
        "reference": "reference",
        "notes": "notes",
    },
}

_HAND_TYPES = {
    name: TypeAdapter(ManualDocumentFields.model_fields[name].annotation) for name in HAND_FIELDS
}


def hand_values(reading: Any, mapping: dict[HandField, str]) -> ManualDocumentFields:
    """What the reading holds in the mapped fields, as the hand types hold them. A stored
    value the hand type cannot hold (a low-confidence raw text in a money field, "ca. 400
    Euro") is left out, i.e. shown empty; a period whose end is before its start shows
    neither date."""
    values: dict[str, Any] = {}
    for name, target in mapping.items():
        stored = getattr(reading, target).value
        if stored is None:
            continue
        try:
            values[name] = _HAND_TYPES[name].validate_python(stored)
        except ValidationError:
            continue  # not a value the hand field can hold: shown empty, kept as it was
    try:
        return ManualDocumentFields.model_validate(values)
    except ValidationError:
        # Only the period can fail as a whole (an end before its start): show neither.
        values.pop("period_start", None)
        values.pop("period_end", None)
        return ManualDocumentFields.model_validate(values)
