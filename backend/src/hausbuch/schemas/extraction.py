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
from .enums import DocumentSubtype, ExtractionKind

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


def hand_ev(kind: ValueKind, meaning: str) -> Any:
    """A field the model is never asked for: the place of a hand-entry field (D37) a kind
    reads nothing for, so a person can still type it (D131, decided 10 Nov 2026)."""
    return Field(
        default_factory=ExtractedValue.missing,
        description=meaning,
        json_schema_extra={"value_kind": kind, "hand_only": True},
    )


def is_hand_only(model: type[Schema], field: str) -> bool:
    """True for a field made by hand_ev: never asked, never in a prompt."""
    extra = model.model_fields[field].json_schema_extra
    return isinstance(extra, dict) and extra.get("hand_only") is True


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
    # Sparkassen print no Darlehensnummer, only the loan's Kontonummer (Luis's contract, 8 Nov
    # 2026); a label written into the value anyway is removed by rules.extraction_parse.
    contract_number: ExtractedValue = ev(
        "text",
        "Darlehensnummer / Vertragsnummer; when none is printed, the loan's Kontonummer. The"
        " number only, without its label",
    )
    principal: ExtractedValue = ev("money", "Nennbetrag")
    nominal_rate: ExtractedValue = ev("rate", "Sollzins p.a.")
    monthly_payment: ExtractedValue = ev("money", "monatliche Rate, if stated")
    initial_repayment_rate: ExtractedValue = ev("rate", "anfängliche Tilgung p.a.")
    fixed_until: ExtractedValue = ev("date", "Ende der Sollzinsbindung")
    follow_up_rate: ExtractedValue = ev(
        "rate",
        "Sollzins p.a. after the Sollzinsbindung ends, if stated: an agreed Anschlusszins, or the"
        " variable Sollzins the loan continues at (e.g. 'Der veränderliche Sollzinssatz beträgt"
        " derzeit ...'); never the Mindestzins, never the effektiver Jahreszins",
    )
    disbursed_on: ExtractedValue = ev("date", "Auszahlung, if stated")
    first_payment_on: ExtractedValue = ev("date", "erste Rate, if stated")
    # Code, not the model, decides the loan's interest-only flag from this (rules.prefill).
    interest_only_clause: ExtractedValue = ev(
        "text",
        "the sentence saying that until Tilgungsbeginn, or until the first full Rate, only"
        " interest (Sollzinsen) is paid, as written, if the contract has one",
    )
    account_iban: ExtractedValue = ev("iban", "Darlehenskonto")
    signed_on: ExtractedValue = ev(
        "date",
        "Vertragsdatum: when it was signed, often only as 'Ort, den <Datum>' by the signatures;"
        " when the parties signed on different dates, the latest of them",
    )


class LeaseExtraction(_Typing):
    """Mietvertrag (contract / lease). Step 2, gated by ai_tenant_documents (D16)."""

    street: ExtractedValue = ev("text", "street of the rented flat")
    house_number: ExtractedValue = ev("text", "house number")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ")
    city: ExtractedValue = ev("text", "city")
    unit_label: ExtractedValue = ev(
        "text",
        "the let unit's label: for a single room let on its own the room (e.g. Zimmer 3),"
        " not the flat's floor or number; else the flat's label (e.g. WE 3)",
    )
    tenant_1_full_name: ExtractedValue = ev("text", "first named Mieter (primary)")
    tenant_1_email: ExtractedValue = ev("text", "if stated")
    tenant_1_phone: ExtractedValue = ev("text", "if stated")
    tenant_1_iban: ExtractedValue = ev("iban", "if stated")
    tenant_2_full_name: ExtractedValue = ev("text", "second named Mieter (co_tenant)")
    tenant_2_email: ExtractedValue = ev("text", "if stated")
    tenant_2_phone: ExtractedValue = ev("text", "if stated")
    tenant_2_iban: ExtractedValue = ev("iban", "if stated")
    start_date: ExtractedValue = ev("date", "Mietbeginn")
    signed_on: ExtractedValue = ev(
        "date",
        "Vertragsdatum: when it was signed, often only as 'Ort, den <Datum>' by the signatures",
    )
    fixed_term_until: ExtractedValue = ev("date", "Zeitmietvertrag bis, if befristet")
    rent_clause: ExtractedValue = ev("text", "Staffel- / Indexmiete clause, if any")
    deposit_amount: ExtractedValue = ev("money", "Kaution")
    deposit_clause: ExtractedValue = ev("text", "Barkaution / Bürgschaft ...")
    cold_rent: ExtractedValue = ev("money", "Nettokaltmiete")
    operating_costs_prepayment: ExtractedValue = ev(
        "money",
        "the monthly Nebenkosten besides the Kaltmiete: the Betriebskostenvorauszahlung, or a"
        " Nebenkostenpauschale (also one that includes Heizung, Warmwasser or Strom)",
    )
    heating_costs_prepayment: ExtractedValue = ev(
        "money", "Heizkostenvorauszahlung, only when the lease states it as its own amount"
    )


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


# The category codes the reading may give a bill's cost line (phase 2 D135): the cost
# categories of the allocation taxonomy, plus `vat` for the Umsatzsteuer line. Code sorts the
# lines afterwards (rules.prefill.sort_bill): the purchase and financing terms decide over the
# model's code, and the euro parts are computed by code from the printed amounts.
COST_LINE_CODES = (
    "acquisition_cost",
    "loan_fees",
    "repairs",
    "renovation",
    "insurance",
    "grundsteuer",
    "property_management",
    "hausgeld_umlagefaehig",
    "hausgeld_verwaltung",
    "electricity",
    "professional_fees",
    "bank_fees",
    "other_expense",
    "vat",
)
COST_LINES_MEANING = (
    "every cost line of the bill, each written `text | amount | code`: the line's text and "
    "its amount as printed, then the code of the kind of cost it is, the only part you choose "
    "yourself, one of " + ", ".join(COST_LINE_CODES) + " (acquisition_cost for the purchase "
    "and its land register entry, loan_fees for a Grundschuld or loan, vat for the "
    "Umsatzsteuer line); never a subtotal (Netto, Zwischensumme) or the total"
)
# Phase 2 D145 (11 Nov 2026): one captured PDF can hold several Bescheide, e.g. the
# Grunderwerbsteuerbescheid the Finanzamt sends each buyer of a joint purchase on that buyer's
# share. Only the Bescheid request says so, so every other request stays byte-identical.
_SEVERAL_BESCHEIDE = (
    "when the document holds several Bescheide (e.g. one per buyer of a joint purchase)"
)
BESCHEID_AMOUNT_MEANING = (
    "the amount assessed; "
    + _SEVERAL_BESCHEIDE
    + ", the sum of their assessed amounts, the one value you add up yourself"
)
BESCHEID_COST_LINES_MEANING = (
    COST_LINES_MEANING
    + "; "
    + _SEVERAL_BESCHEIDE
    + ", each Bescheid's assessed amount is its own line, its text naming that Bescheid only "
    "as Bescheid 1, Bescheid 2 and so on, or by its share or base (e.g. 3,5 % von 150.000), "
    "never by a person's name, an identification number or a Steuernummer"
)


class InvoiceExtraction(_Typing):
    """Invoices: Nebenkosten (notary, Grundbuchamt, broker) now, repairs and others later."""

    sender: ExtractedValue = ev(
        "text", "name of who issued it, without the address: decides the cost kind"
    )
    # rules.prefill reads it to recognize a financing invoice (D35).
    subject: ExtractedValue = ev("text", "what it is for, as written (Betreff / Gegenstand)")
    amount_total: ExtractedValue = ev("money", "gross total (brutto)")
    amount_net: ExtractedValue = ev("money", "net amount, if stated")
    reference: ExtractedValue = ev("text", "invoice no. / Aktenzeichen")
    iban: ExtractedValue = ev("iban", "the payee's IBAN, if stated")
    due_on: ExtractedValue = ev("date", "Zahlungsziel, if stated")
    # Phase 2 D135: kept only when they sum to amount_total (extraction_parse.derive_fields).
    cost_lines: ExtractedValue = ev("text_list", COST_LINES_MEANING)


class BescheidExtraction(_Typing):
    """A Bescheid other than Grundsteuer, e.g. the Grunderwerbsteuerbescheid."""

    sender: ExtractedValue = ev("text", "name of the authority, without the address")
    amount_total: ExtractedValue = ev("money", BESCHEID_AMOUNT_MEANING)  # phase 2 D145
    reference: ExtractedValue = ev("text", "Aktenzeichen / Steuernummer reference")
    iban: ExtractedValue = ev("iban", "the authority's IBAN, if stated")
    due_on: ExtractedValue = ev("date", "fällig am")
    tax_year: ExtractedValue = ev("year", "the year it concerns, if stated")
    cost_lines: ExtractedValue = ev("text_list", BESCHEID_COST_LINES_MEANING)  # D135, D145


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


# Phase 4b (docs/phases/phase-4b.md §8.1, D96, D103). No identifier is a field: the model is
# never asked for one, and the scrub in rules.extraction_parse removes any that a quote holds.
# Descriptions name the printed label; the line numbers may move between form years.


class WageCertificateExtraction(_Typing):
    """Lohnsteuerbescheinigung: one employer's certificate for one year."""

    employee_name: ExtractedValue = ev("text", "the employee's name, to match the owner")
    employer_name: ExtractedValue = ev("text", "the employer's name only")
    tax_year: ExtractedValue = ev("year", "the year the certificate covers")
    period_from: ExtractedValue = ev("date", "Nr. 1 Dauer des Dienstverhältnisses, vom")
    period_to: ExtractedValue = ev("date", "Nr. 1 Dauer des Dienstverhältnisses, bis")
    tax_class: ExtractedValue = ev("text", "Steuerklasse/Faktor")
    church_tax_mark: ExtractedValue = ev("text", "Kirchensteuermerkmale")
    child_allowance_count: ExtractedValue = ev("decimal", "Zahl der Kinderfreibeträge")
    gross: ExtractedValue = ev("money", "Nr. 3 Bruttoarbeitslohn einschl. Sachbezüge")
    income_tax: ExtractedValue = ev("money", "Nr. 4 Einbehaltene Lohnsteuer von 3.")
    soli: ExtractedValue = ev("money", "Nr. 5 Einbehaltener Solidaritätszuschlag von 3.")
    church_tax: ExtractedValue = ev(
        "money", "Nr. 6 Einbehaltene Kirchensteuer des Arbeitnehmers von 3."
    )
    church_tax_spouse: ExtractedValue = ev(
        "money", "Nr. 7 Einbehaltene Kirchensteuer des Ehegatten von 3."
    )
    replacement_benefits: ExtractedValue = ev(
        "money", "Nr. 15 Kurzarbeitergeld, Zuschuss zum Mutterschaftsgeld und ähnliche"
    )
    short_time_allowance: ExtractedValue = ev("money", "Nr. 15a davon Kurzarbeitergeld")
    employer_commute_tax_free: ExtractedValue = ev(
        "money",
        "Nr. 17 Steuerfreie Arbeitgeberleistungen für Fahrten zwischen Wohnung und erster"
        " Tätigkeitsstätte",
    )
    employer_commute_flat_taxed: ExtractedValue = ev(
        "money",
        "Nr. 18 Pauschal besteuerte Arbeitgeberleistungen für Fahrten zwischen Wohnung und"
        " erster Tätigkeitsstätte",
    )
    pension_employer: ExtractedValue = ev(
        "money", "Nr. 22a Arbeitgeberanteil zur gesetzlichen Rentenversicherung"
    )
    pension_employee: ExtractedValue = ev(
        "money", "Nr. 23a Arbeitnehmeranteil zur gesetzlichen Rentenversicherung"
    )
    pension_employee_professional: ExtractedValue = ev(
        "money", "Nr. 23b Arbeitnehmeranteil an berufsständische Versorgungseinrichtungen"
    )
    subsidy_health: ExtractedValue = ev(
        "money", "Nr. 24a Steuerfreie Arbeitgeberzuschüsse zur gesetzlichen Krankenversicherung"
    )
    subsidy_private_health: ExtractedValue = ev(
        "money", "Nr. 24b Steuerfreie Arbeitgeberzuschüsse zur privaten Krankenversicherung"
    )
    subsidy_care: ExtractedValue = ev(
        "money", "Nr. 24c Steuerfreie Arbeitgeberzuschüsse zur gesetzlichen Pflegeversicherung"
    )
    health_employee: ExtractedValue = ev(
        "money", "Nr. 25 Arbeitnehmerbeiträge zur gesetzlichen Krankenversicherung"
    )
    care_employee: ExtractedValue = ev(
        "money", "Nr. 26 Arbeitnehmerbeiträge zur sozialen Pflegeversicherung"
    )
    unemployment_employee: ExtractedValue = ev(
        "money", "Nr. 27 Arbeitnehmerbeiträge zur Arbeitslosenversicherung"
    )


class PayslipExtraction(_Typing):
    """Gehaltsabrechnung: one month, with the month column and the year-to-date column
    (Jahreswerte / kumuliert). Net pay and the bank account are not read."""

    employee_name: ExtractedValue = ev("text", "the employee's name, to match the owner")
    employer_name: ExtractedValue = ev("text", "the employer's name only")
    period_month: ExtractedValue = ev("date", "the month paid, as its first day")
    tax_class: ExtractedValue = ev("text", "Steuerklasse")
    gross_month: ExtractedValue = ev("money", "Steuer-Brutto of the month (not Gesamt-Brutto)")
    gross_ytd: ExtractedValue = ev("money", "Steuer-Brutto, Jahreswerte / kumuliert")
    income_tax_month: ExtractedValue = ev("money", "Lohnsteuer of the month")
    income_tax_ytd: ExtractedValue = ev("money", "Lohnsteuer, Jahreswerte / kumuliert")
    soli_month: ExtractedValue = ev("money", "Solidaritätszuschlag of the month")
    soli_ytd: ExtractedValue = ev("money", "Solidaritätszuschlag, Jahreswerte / kumuliert")
    church_tax_month: ExtractedValue = ev("money", "Kirchensteuer of the month")
    church_tax_ytd: ExtractedValue = ev("money", "Kirchensteuer, Jahreswerte / kumuliert")
    pension_employee_month: ExtractedValue = ev("money", "Rentenversicherung AN of the month")
    pension_employee_ytd: ExtractedValue = ev(
        "money", "Rentenversicherung AN, Jahreswerte / kumuliert"
    )
    health_employee_month: ExtractedValue = ev("money", "Krankenversicherung AN of the month")
    health_employee_ytd: ExtractedValue = ev(
        "money", "Krankenversicherung AN, Jahreswerte / kumuliert"
    )
    care_employee_month: ExtractedValue = ev("money", "Pflegeversicherung AN of the month")
    care_employee_ytd: ExtractedValue = ev(
        "money", "Pflegeversicherung AN, Jahreswerte / kumuliert"
    )
    unemployment_employee_month: ExtractedValue = ev(
        "money", "Arbeitslosenversicherung AN of the month"
    )
    unemployment_employee_ytd: ExtractedValue = ev(
        "money", "Arbeitslosenversicherung AN, Jahreswerte / kumuliert"
    )
    tax_free_benefits_month: ExtractedValue = ev("money", "steuerfreie Bezüge of the month")
    tax_free_benefits_ytd: ExtractedValue = ev(
        "money", "steuerfreie Bezüge, Jahreswerte / kumuliert"
    )


class IncomeTaxAssessmentExtraction(_Typing):
    """Einkommensteuerbescheid (bescheid / einkommensteuerbescheid; phase-4b.md D129, §8.4):
    the assessed zvE of one year, proposed as the household's bescheid_zve. Only these fields:
    no identifier, no address, no other figure of the assessment is ever read."""

    tax_year: ExtractedValue = ev(
        "year", "the year assessed (Bescheid für 20xx über Einkommensteuer ...)"
    )
    taxable_income: ExtractedValue = ev(
        "money",
        "zu versteuerndes Einkommen (not Summe der Einkünfte, Gesamtbetrag der Einkünfte or"
        " Einkommen)",
    )
    assessment_type: ExtractedValue = ev(
        "text", "the kind of assessment, e.g. Zusammenveranlagung or Einzelveranlagung"
    )
    # Compared with the owners' names in the review (rules.prefill); never in an event.
    addressee: ExtractedValue = ev(
        "text", "the names of the persons the Bescheid is for, names only"
    )


class PaymentProofExtraction(_Typing):
    """Zahlungsnachweis (other / payment_proof; phase-3.md D143): the proof that a bill was
    paid, uploaded from the bill's to-do. Only these fields, besides the title and date every
    reading carries: no IBAN, no account number and no name but the payee is ever asked for,
    and the quotes are scrubbed of identifiers (jobs.document_read)."""

    paid_on: ExtractedValue = ev(
        "date",
        "the day the payment was made: Ausführungsdatum, Buchungstag or Wertstellung of the"
        " transfer",
    )
    amount: ExtractedValue = ev(
        "money", "the amount transferred, as stated; never a balance, a limit or a fee"
    )
    payee: ExtractedValue = ev(
        "text",
        "the payee (Empfänger, Zahlungsempfänger) only; never the payer, the account holder or"
        " any other person",
    )
    purpose: ExtractedValue = ev(
        "text", "the Verwendungszweck as stated, without any account details"
    )


# Phase 2 D139: a Wohnflächenberechnung of a WEG lists every dwelling of the building; code
# (rules.prefill) picks the household's row by the flat's MEA or WE label, never by a sum.
DWELLINGS_MEANING = (
    "every dwelling the document lists, one item per Wohnung, each written `label | "
    "Wohnfläche | MEA | Lage`: the dwelling's label as printed (e.g. WE 3, Wohnung Nr. 3), "
    "its Wohnfläche in m² as printed, its Miteigentumsanteil as printed (e.g. 85/1000) and its "
    "location in the building (e.g. 2. OG links), unbekannt for a part the row does not "
    "state; one item for a document about one flat; never a single room, a sum or a total "
    "row"
)


# Phase 2 D130, D131 (§6.2, §6.2.1): the general reading and the Verwaltervertrag. Neither
# proposes a record, only typing and at most one link, so no field the model reads is a euro
# figure. The hand_ev fields hold what a person types under "Felder (von Hand)" (D37): every
# hand-entry field a letter took before the catalogue, so none is lost (decided 10 Nov 2026).


class GeneralExtraction(_Typing):
    """The general reading (D130): title and date for every document without a reader of its
    own, plus the one field set its subtype's group needs to find its link (D131): the
    property's address for the property, WEG, running-costs and tenancy groups, the lender and
    contract number for the loan group. A request asks only those (asked_fields); an
    owner's home address is never asked. A Wohnflächenberechnung (building_records) is also
    asked its list of dwellings (D139), a Gutachten (appraisal) its remaining useful life and
    Stichtag (D147)."""

    street: ExtractedValue = ev("text", "street of the property the document concerns")
    house_number: ExtractedValue = ev("text", "house number of that property")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ of that property")
    # D139: asked of a Wohnflächenberechnung (building_records) only. Code picks the
    # household's row and proposes its area as the property's living_area_m2 (phase 3 D71).
    dwellings: ExtractedValue = ev("text_list", DWELLINGS_MEANING)
    # D147: asked of a Gutachten (appraisal) only. Code derives the shorter useful life
    # (§7 Abs. 4 S. 2 EStG, phase 3 D79) from both and proposes it as a tax input.
    remaining_useful_life: ExtractedValue = ev(
        "decimal",
        "the Restnutzungsdauer or tatsächliche Nutzungsdauer of the building the Gutachten "
        "concludes, in years as written (e.g. 21 Jahre); never the Gesamtnutzungsdauer, the "
        "building's age or a life the Gutachten only quotes from a table",
    )
    valuation_date: ExtractedValue = ev(
        "date",
        "the Wertermittlungsstichtag or Bewertungsstichtag the remaining life is stated for; "
        "never the date the Gutachten was written or signed",
    )
    lender: ExtractedValue = ev("text", "the bank that gave the loan")
    contract_number: ExtractedValue = ev(
        "text",
        "Darlehensnummer / Vertragsnummer; when none is printed, the loan's Kontonummer. The"
        " number only, without its label",
    )
    # By hand only (lender and contract_number hold Gegenpartei and Referenz).
    amount_total: ExtractedValue = hand_ev("money", "Betrag, typed by hand")
    period_start: ExtractedValue = hand_ev("date", "Zeitraum von, typed by hand")
    period_end: ExtractedValue = hand_ev("date", "Zeitraum bis, typed by hand")
    notes: ExtractedValue = hand_ev("text", "Notiz, typed by hand")


class ManagementContractExtraction(_Typing):
    """Verwaltervertrag (contract / management_contract, D130): a Mietverwaltungsvertrag with
    the household's letting manager, or the WEG's Verwaltervertrag. Proposes typing and
    link:property; the fee is booked from the bank lines that pay it, never from here."""

    manager: ExtractedValue = ev("text", "the Verwalter's name only, without the address")
    street: ExtractedValue = ev("text", "street of the managed property")
    house_number: ExtractedValue = ev("text", "house number of the managed property")
    postal_code: ExtractedValue = ev("text", "five-digit PLZ of the managed property")
    start_date: ExtractedValue = ev("date", "Vertragsbeginn: when the management starts")
    monthly_fee: ExtractedValue = ev(
        "text", "the Verwaltervergütung per month as written, e.g. 30,00 € zzgl. MwSt. je Einheit"
    )
    notice_period: ExtractedValue = ev("text", "Kündigungsfrist, as written")
    fee_net: ExtractedValue = ev(
        "money",
        "the Verwaltervergütung per month as a number, as the contract states it, before "
        "any MwSt. the contract adds",
    )
    # Betrag holds the monthly fee with MwSt., computed by code from fee_net and the fee's
    # wording (rules.extraction_parse.management_fee_gross; Luis, 10 Nov 2026: the box stayed
    # empty although the fee was read), or typed by hand. A contract is never a Beleg for the
    # matcher, so the amount books nothing. Never asked.
    amount_total: ExtractedValue = hand_ev("money", "Betrag: the monthly fee with MwSt.")
    # By hand only (manager holds Gegenpartei, start_date Zeitraum von).
    period_end: ExtractedValue = hand_ev("date", "Zeitraum bis, typed by hand")
    reference: ExtractedValue = hand_ev("text", "Referenz, typed by hand")
    notes: ExtractedValue = hand_ev("text", "Notiz, typed by hand")


GeneralLink = Literal["property", "loan", "tenancy"]

# The link each catalogue subtype's group proposes (§6.2.1); a subtype not here (income tax,
# household finances, or none) is read for title and date only.
_PROPERTY_GROUPS = (
    # property and purchase
    DocumentSubtype.LAND_REGISTER_EXTRACT,
    DocumentSubtype.DECLARATION_OF_DIVISION,
    DocumentSubtype.LAND_CHARGE_DEED,
    DocumentSubtype.ENERGY_CERTIFICATE,
    DocumentSubtype.BUILDING_RECORDS,
    DocumentSubtype.APPRAISAL,  # D147: from before the catalogue, linked like its group
    DocumentSubtype.INSURANCE_POLICY,
    DocumentSubtype.MANAGEMENT_CONTRACT,
    DocumentSubtype.SERVICE_CONTRACT,
    # WEG
    DocumentSubtype.WEG_MINUTES,
    DocumentSubtype.WEG_INVITATION,
    DocumentSubtype.SPECIAL_LEVY,
    # running costs
    DocumentSubtype.UTILITY_BILL,
    DocumentSubtype.QUOTE,
    DocumentSubtype.CHIMNEY_SWEEP_NOTICE,
)
LOAN_GROUP = (
    DocumentSubtype.REPAYMENT_SCHEDULE,
    DocumentSubtype.LOAN_TERMS_CHANGE,
    DocumentSubtype.LOAN_STATEMENT,
    DocumentSubtype.FINANCING_INVOICE,  # D138: read by its type's reader, not the general one
)
TENANCY_GROUP = (
    DocumentSubtype.LEASE_AMENDMENT,
    DocumentSubtype.RENT_INCREASE,
    DocumentSubtype.TERMINATION_NOTICE,
    DocumentSubtype.TENANT_SELF_DISCLOSURE,
    DocumentSubtype.LANDLORD_CONFIRMATION,
    DocumentSubtype.OPERATING_COSTS_STATEMENT,
    DocumentSubtype.TENANT_CORRESPONDENCE,
)
GENERAL_LINK: dict[DocumentSubtype, GeneralLink] = {
    **dict.fromkeys(_PROPERTY_GROUPS, "property"),
    **dict.fromkeys(LOAN_GROUP, "loan"),
    **dict.fromkeys(TENANCY_GROUP, "tenancy"),
}
_ADDRESS = ("street", "house_number", "postal_code")
_LINK_FIELDS: dict[GeneralLink, tuple[str, ...]] = {
    "property": _ADDRESS,
    "loan": ("lender", "contract_number"),
    "tenancy": _ADDRESS,
}
# A subtype's own fields beyond its group's link fields: a Wohnflächenberechnung's list of
# dwellings (D139), a Gutachten's remaining useful life and its Stichtag (D147). Every other
# subtype's request stays as it was.
_SUBTYPE_FIELDS: dict[DocumentSubtype, tuple[str, ...]] = {
    DocumentSubtype.BUILDING_RECORDS: ("dwellings",),
    DocumentSubtype.APPRAISAL: ("remaining_useful_life", "valuation_date"),
}


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
    ExtractionKind.LOHNSTEUERBESCHEINIGUNG: WageCertificateExtraction,
    ExtractionKind.GEHALTSABRECHNUNG: PayslipExtraction,
    ExtractionKind.EINKOMMENSTEUERBESCHEID: IncomeTaxAssessmentExtraction,
    ExtractionKind.GENERAL: GeneralExtraction,
    ExtractionKind.MANAGEMENT_CONTRACT: ManagementContractExtraction,
    ExtractionKind.PAYMENT_PROOF: PaymentProofExtraction,
}


def asked_fields(kind: ExtractionKind, subtype: DocumentSubtype | None = None) -> tuple[str, ...]:
    """The fields an extract request asks for, in the model's order: every field of the kind
    but those only typed by hand (hand_ev), except for the general reading, which asks title
    and date plus the link fields of the subtype's group (D131) and the subtype's own fields
    (D139: a Wohnflächenberechnung's dwellings; D147: a Gutachten's remaining useful life).
    The job keeps only these, so a line the model volunteered (an address on a
    Vorauszahlungsbescheid) is never stored."""
    model = EXTRACTION_MODELS[kind]
    names = tuple(name for name in model.model_fields if not is_hand_only(model, name))
    if kind is not ExtractionKind.GENERAL:
        return names
    link = GENERAL_LINK.get(subtype) if subtype is not None else None
    wanted = {"title", "document_date", *(_LINK_FIELDS[link] if link is not None else ())}
    if subtype is not None:
        wanted.update(_SUBTYPE_FIELDS.get(subtype, ()))
    return tuple(name for name in names if name in wanted)


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
    | WageCertificateExtraction
    | PayslipExtraction
    | IncomeTaxAssessmentExtraction
    | GeneralExtraction
    | ManagementContractExtraction
    | PaymentProofExtraction
)
