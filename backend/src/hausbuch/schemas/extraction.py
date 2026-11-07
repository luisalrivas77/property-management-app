"""Per-type extraction models (docs/phases/phase-2.md §4.1, field lists from §6.2).

Each model is a flat object of ExtractedValues, so the review screen renders any kind the same
way. A field the response did not state is ExtractedValue.missing() (value None, confidence
low). Every field declares the kind of value it holds (`value_kind`), which is what
rules.extraction_parse normalizes the document's own form into and validates against:

| value_kind | written in the document      | stored value                         |
| ---------- | ---------------------------- | ------------------------------------ |
| money      | 1.234,56 € / 289.000 EUR     | decimal string "1234.56"             |
| decimal    | 68,40 m²                     | decimal string "68.40"               |
| rate       | 3,65 % (p.a.)                | fraction as decimal string "0.0365"  |
| date       | 01.03.2027 / 1.3.2027        | ISO date "2027-03-01"                |
| year       | 1994                         | integer                              |
| fraction   | 85/1000 (stel)               | {"numerator": 85, "denominator": 1000} |
| iban       | DE89 3704 0044 0532 0130 00  | normalized "DE89370400440532013000"  |
| text       | anything                     | the text, whitespace-collapsed       |
| text_list  | a; b                         | list of texts, split on ";"          |

Field names are the target *Create / *Patch field names where one exists (rules.prefill maps
the rest). Tenants are numbered (tenant_1_*, tenant_2_*): a lease with more than two tenants
proposes the first two and says so in a note.
"""

from typing import Any, Literal

from pydantic import Field

from .ai import ExtractedValue
from .common import Schema
from .enums import ExtractionKind

ValueKind = Literal[
    "money", "decimal", "rate", "date", "year", "fraction", "iban", "text", "text_list"
]


def ev(kind: ValueKind, meaning: str) -> Any:
    """An extraction field: missing unless stated, with its value kind in the JSON schema."""
    return Field(
        default_factory=ExtractedValue.missing,
        description=meaning,
        json_schema_extra={"value_kind": kind},
    )


def value_kind(model: type[Schema], field: str) -> ValueKind:
    """The value kind a field of an extraction model holds."""
    extra = model.model_fields[field].json_schema_extra
    assert isinstance(extra, dict)
    return extra["value_kind"]  # type: ignore[return-value]


class _Typing(Schema):
    """What every kind proposes for the document's own typing (§6.2, last row)."""

    # Never in events (D5). The one field the model writes itself rather than copies (D36):
    # its words are usually not in the cited quote, so it parses at medium confidence. The
    # model sees this description plus the rule in extract.md.
    title: ExtractedValue = ev(
        "text", "a short German title, two to six words naming what the document is"
    )
    document_date: ExtractedValue = ev("date", "the date the document carries")


class PurchaseContractExtraction(_Typing):
    """Kaufvertrag (contract / purchase_contract)."""

    property_kind: ExtractedValue = ev("text", "e.g. Wohnungseigentum (-> etw)")
    street: ExtractedValue = ev("text", "street of the property")
    house_number: ExtractedValue = ev("text", "house number")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ")
    city: ExtractedValue = ev("text", "city")
    build_year: ExtractedValue = ev("year", "Baujahr / Fertigstellung, often absent")
    notarized_on: ExtractedValue = ev("date", "Urkundendatum")
    # The Besitzübergang clause: a date, or a condition such as "Tag der vollständigen
    # Kaufpreiszahlung", which leaves the value null and the clause quoted (Q5).
    purchase_date: ExtractedValue = ev("date", "Übergang von Besitz, Nutzen und Lasten")
    purchase_price: ExtractedValue = ev("money", "Kaufpreis")
    land_value: ExtractedValue = ev("money", "price share allocated to the land, if stated")
    unit_label: ExtractedValue = ev("text", "WE Nr. as in the Teilungserklärung, e.g. WE 3")
    mea: ExtractedValue = ev("fraction", "Miteigentumsanteil, e.g. 85/1000")
    floor_area_m2: ExtractedValue = ev("decimal", "Wohnfläche in m², if stated")
    buyers: ExtractedValue = ev("text_list", "the buyers' full names")
    ownership_clause: ExtractedValue = ev("text", "e.g. je zur Hälfte")


class LoanContractExtraction(_Typing):
    """Darlehensvertrag (contract / loan_contract)."""

    lender: ExtractedValue = ev("text", "Darlehensgeber")
    contract_number: ExtractedValue = ev("text", "Darlehensnummer / Vertragsnummer")
    principal: ExtractedValue = ev("money", "Nennbetrag")
    nominal_rate: ExtractedValue = ev("rate", "Sollzins p.a.")
    monthly_payment: ExtractedValue = ev("money", "monatliche Rate, if stated")
    initial_repayment_rate: ExtractedValue = ev("rate", "anfängliche Tilgung p.a.")
    fixed_until: ExtractedValue = ev("date", "Ende der Sollzinsbindung")
    disbursed_on: ExtractedValue = ev("date", "Auszahlung, if stated")
    first_payment_on: ExtractedValue = ev("date", "erste Rate, if stated")
    account_iban: ExtractedValue = ev("iban", "Darlehenskonto")
    signed_on: ExtractedValue = ev("date", "Vertragsdatum")


class LeaseExtraction(_Typing):
    """Mietvertrag (contract / lease). Step 2, gated by ai_tenant_documents (D16)."""

    street: ExtractedValue = ev("text", "street of the rented flat")
    house_number: ExtractedValue = ev("text", "house number")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ")
    city: ExtractedValue = ev("text", "city")
    unit_label: ExtractedValue = ev("text", "the flat's label, e.g. WE 3")
    tenant_1_full_name: ExtractedValue = ev("text", "first named Mieter (primary)")
    tenant_1_email: ExtractedValue = ev("text", "if stated")
    tenant_1_phone: ExtractedValue = ev("text", "if stated")
    tenant_1_iban: ExtractedValue = ev("iban", "if stated")
    tenant_2_full_name: ExtractedValue = ev("text", "second named Mieter (co_tenant)")
    tenant_2_email: ExtractedValue = ev("text", "if stated")
    tenant_2_phone: ExtractedValue = ev("text", "if stated")
    tenant_2_iban: ExtractedValue = ev("iban", "if stated")
    start_date: ExtractedValue = ev("date", "Mietbeginn")
    signed_on: ExtractedValue = ev("date", "Vertragsdatum")
    fixed_term_until: ExtractedValue = ev("date", "Zeitmietvertrag bis, if befristet")
    rent_clause: ExtractedValue = ev("text", "Staffel- / Indexmiete clause, if any")
    deposit_amount: ExtractedValue = ev("money", "Kaution")
    deposit_clause: ExtractedValue = ev("text", "Barkaution / Bürgschaft ...")
    cold_rent: ExtractedValue = ev("money", "Nettokaltmiete")
    operating_costs_prepayment: ExtractedValue = ev("money", "Betriebskostenvorauszahlung")
    heating_costs_prepayment: ExtractedValue = ev("money", "Heizkostenvorauszahlung")


class HandoverProtocolExtraction(_Typing):
    """Übergabeprotokoll (letter / handover_protocol). Step 2."""

    street: ExtractedValue = ev("text", "street")
    house_number: ExtractedValue = ev("text", "house number")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ")
    meter_readings: ExtractedValue = ev("text_list", "Zählerstände; stay in the extraction")


class DepositRecordExtraction(_Typing):
    """Kautionsnachweis (letter / deposit_record). Step 2."""

    amount: ExtractedValue = ev("money", "Kautionsbetrag")
    received_on: ExtractedValue = ev("date", "Eingang")


class InvoiceExtraction(_Typing):
    """Invoices: Nebenkosten (notary, Grundbuchamt, broker) now, repairs and others later."""

    sender: ExtractedValue = ev("text", "who issued it: decides the cost kind")
    # rules.prefill reads it to recognize a financing invoice (D35).
    subject: ExtractedValue = ev("text", "what it is for, as written (Betreff / Gegenstand)")
    amount_total: ExtractedValue = ev("money", "gross total (brutto)")
    amount_net: ExtractedValue = ev("money", "net amount, if stated")
    reference: ExtractedValue = ev("text", "invoice no. / Aktenzeichen")
    iban: ExtractedValue = ev("iban", "the payee's IBAN, if stated")
    due_on: ExtractedValue = ev("date", "Zahlungsziel, if stated")


class BescheidExtraction(_Typing):
    """A Bescheid other than Grundsteuer, e.g. the Grunderwerbsteuerbescheid."""

    sender: ExtractedValue = ev("text", "the authority")
    amount_total: ExtractedValue = ev("money", "the amount assessed")
    reference: ExtractedValue = ev("text", "Aktenzeichen / Steuernummer reference")
    iban: ExtractedValue = ev("iban", "the authority's IBAN, if stated")
    due_on: ExtractedValue = ev("date", "fällig am")
    tax_year: ExtractedValue = ev("year", "the year it concerns, if stated")


class GrundsteuerbescheidExtraction(_Typing):
    """Grundsteuerbescheid (bescheid / grundsteuerbescheid)."""

    street: ExtractedValue = ev("text", "street of the property")
    house_number: ExtractedValue = ev("text", "house number")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ")
    tax_year: ExtractedValue = ev("year", "first year the Bescheid applies to")
    reference: ExtractedValue = ev("text", "Aktenzeichen")
    yearly_amount: ExtractedValue = ev("money", "Jahresbetrag")
    installment_amount: ExtractedValue = ev("money", "Vierteljahresbetrag, if stated")
    due_dates: ExtractedValue = ev("text_list", "Fälligkeiten, e.g. 15.02.; 15.05.; ...")
    iban: ExtractedValue = ev("iban", "the Stadtkasse's IBAN, if stated")


class WirtschaftsplanExtraction(_Typing):
    """Wirtschaftsplan: the monthly Hausgeld split the by_wirtschaftsplan rule reads (§8.1)."""

    street: ExtractedValue = ev("text", "street of the property")
    house_number: ExtractedValue = ev("text", "house number")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ")
    tax_year: ExtractedValue = ev("year", "the Wirtschaftsjahr")
    valid_from: ExtractedValue = ev("date", "first month the plan applies to")
    hausgeld_total_monthly: ExtractedValue = ev("money", "Hausgeld gesamt pro Monat")
    hausgeld_umlagefaehig_monthly: ExtractedValue = ev("money", "davon umlagefähig")
    hausgeld_verwaltung_monthly: ExtractedValue = ev("money", "davon nicht umlagefähig")
    hausgeld_ruecklage_monthly: ExtractedValue = ev("money", "davon Erhaltungsrücklage")
    iban: ExtractedValue = ev("iban", "the WEG account the Hausgeld is paid to")


class HausgeldabrechnungExtraction(_Typing):
    """Hausgeldabrechnung: the year's settlement (read by phase 3's Hausgeld rule)."""

    street: ExtractedValue = ev("text", "street of the property")
    house_number: ExtractedValue = ev("text", "house number")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ")
    tax_year: ExtractedValue = ev("year", "the Abrechnungsjahr")
    settlement_result: ExtractedValue = ev("money", "Nachzahlung (+) / Guthaben (-)")
    umlagefaehig_total: ExtractedValue = ev("money", "umlagefähige Kosten des Jahres")
    verwaltung_total: ExtractedValue = ev("money", "nicht umlagefähige Kosten des Jahres")
    ruecklage_contribution: ExtractedValue = ev("money", "Zuführung zur Rücklage")
    ruecklage_spent: ExtractedValue = ev("money", "Entnahme aus der Rücklage")


class ZinsbescheinigungExtraction(_Typing):
    """Zinsbescheinigung: the year's interest, reconciled against the loan schedule."""

    lender: ExtractedValue = ev("text", "the bank")
    contract_number: ExtractedValue = ev("text", "Darlehensnummer")
    tax_year: ExtractedValue = ev("year", "the year")
    interest_paid: ExtractedValue = ev("money", "gezahlte Zinsen")


class StatementExtraction(_Typing):
    """PDF Kontoauszug (§7.1): balances and lines. Every amount is a model reading, so the
    reconciliation is the guard; `lines` holds one text per booking line, parsed into
    StatementLines by bank.pdf."""

    account_iban: ExtractedValue = ev("iban", "the statement's account")
    period_start: ExtractedValue = ev("date", "first day covered")
    period_end: ExtractedValue = ev("date", "last day covered")
    opening_balance: ExtractedValue = ev("money", "alter Kontostand")
    closing_balance: ExtractedValue = ev("money", "neuer Kontostand")
    lines: ExtractedValue = ev("text_list", "the booking lines")


EXTRACTION_MODELS: dict[ExtractionKind, type[_Typing]] = {
    ExtractionKind.PURCHASE_CONTRACT: PurchaseContractExtraction,
    ExtractionKind.LOAN_CONTRACT: LoanContractExtraction,
    ExtractionKind.LEASE: LeaseExtraction,
    ExtractionKind.HANDOVER_PROTOCOL: HandoverProtocolExtraction,
    ExtractionKind.DEPOSIT_RECORD: DepositRecordExtraction,
    ExtractionKind.INVOICE: InvoiceExtraction,
    ExtractionKind.BESCHEID: BescheidExtraction,
    ExtractionKind.GRUNDSTEUERBESCHEID: GrundsteuerbescheidExtraction,
    ExtractionKind.WIRTSCHAFTSPLAN: WirtschaftsplanExtraction,
    ExtractionKind.HAUSGELDABRECHNUNG: HausgeldabrechnungExtraction,
    ExtractionKind.ZINSBESCHEINIGUNG: ZinsbescheinigungExtraction,
    ExtractionKind.STATEMENT: StatementExtraction,
}

AnyExtraction = (
    PurchaseContractExtraction
    | LoanContractExtraction
    | LeaseExtraction
    | HandoverProtocolExtraction
    | DepositRecordExtraction
    | InvoiceExtraction
    | BescheidExtraction
    | GrundsteuerbescheidExtraction
    | WirtschaftsplanExtraction
    | HausgeldabrechnungExtraction
    | ZinsbescheinigungExtraction
    | StatementExtraction
)
