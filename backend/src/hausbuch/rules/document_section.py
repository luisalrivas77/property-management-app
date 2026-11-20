"""Which section of Dokumente a document belongs to, and the search over it (phase 10 D282).

Pure: the service reads what a document is (type, subtype, the depot kind code set at upload)
and what relies on it (``SectionSignal``), and this module decides. Never stored, so a new link
moves a document at the next read.

Sections in priority order (``DocumentSection``'s member order): Immobilie, Steuern, Konten,
Anlagen, Haushalt & Sonstiges. Every section that can claim a document is collected, and the
first one wins, so a Grunderwerbsteuerbescheid that is a Nebenkosten row's Beleg is Immobilie.

What a document is claims one section: its subtype when it has one (the document catalogue's
groups, phase-2.md §6.2.1), else its type (a plain statement is a bank or card statement, a
Wirtschaftsplan, Hausgeldabrechnung or Zinsbescheinigung belongs to the flat, a
Lohnsteuerbescheinigung or Gehaltsabrechnung to the tax return). A subtype wins over its type
because it is the finer reading: a `depot_statement` is a depot's statement, not a bank's, and
a `loan_statement` is the flat's loan. Every subtype and type is mapped explicitly (the
completeness test in tests/test_document_section.py), so a new one cannot fall silently into
Haushalt & Sonstiges.

The search (``matches``) is case- and umlaut-insensitive: "ubergabe", "uebergabe" and
"Übergabe" all find „Übergabeprotokoll“. Every word of the query must occur in one of the
document's search texts (title, type label in German and English, file name, issuer, dates,
amount).
"""

import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from enum import StrEnum

from hausbuch.schemas.enums import DocumentSection, DocumentSubtype, DocumentType


class SectionSignal(StrEnum):
    """What relies on a document, read by the service in a few batched queries."""

    # Linked to a property, unit, tenancy, tenant or loan; a Nebenkosten row's Beleg; a payment
    # proof's bill or the proof of one; a confirmed tax input of a property; a rent row's
    # Beleg; the Beleg of an allocation booked to a property.
    PROPERTY_RECORD = "property_record"
    # Linked to an owner; the Beleg of an owner's income input or a return input.
    TAX_RECORD = "tax_record"
    # Imported onto a bank account or a credit card, or fetched as the bank's own statement.
    BANK_STATEMENT = "bank_statement"
    # Imported onto a depot, exchange or wallet; a movement's, holding statement's or price's
    # source; a wallet sync's original; a depot document recognised by its heading.
    HOLDING_RECORD = "holding_record"


_SECTION_BY_SIGNAL: dict[SectionSignal, DocumentSection] = {
    SectionSignal.PROPERTY_RECORD: DocumentSection.PROPERTY,
    SectionSignal.TAX_RECORD: DocumentSection.TAX,
    SectionSignal.BANK_STATEMENT: DocumentSection.ACCOUNTS,
    SectionSignal.HOLDING_RECORD: DocumentSection.INVESTMENTS,
}

_P = DocumentSection.PROPERTY
_T = DocumentSection.TAX
_I = DocumentSection.INVESTMENTS

SECTION_BY_SUBTYPE: dict[DocumentSubtype, DocumentSection] = {
    # Property and purchase
    DocumentSubtype.PURCHASE_CONTRACT: _P,
    DocumentSubtype.PRICE_SPLIT_EVIDENCE: _P,
    DocumentSubtype.APPRAISAL: _P,
    DocumentSubtype.COST_BASIS_EVIDENCE: _P,
    DocumentSubtype.GRUNDSTEUERBESCHEID: _P,
    DocumentSubtype.LAND_REGISTER_EXTRACT: _P,
    DocumentSubtype.DECLARATION_OF_DIVISION: _P,
    DocumentSubtype.LAND_CHARGE_DEED: _P,
    DocumentSubtype.ENERGY_CERTIFICATE: _P,
    DocumentSubtype.BUILDING_RECORDS: _P,
    DocumentSubtype.INSURANCE_POLICY: _P,
    DocumentSubtype.MANAGEMENT_CONTRACT: _P,
    DocumentSubtype.SERVICE_CONTRACT: _P,
    # WEG
    DocumentSubtype.WEG_MINUTES: _P,
    DocumentSubtype.WEG_INVITATION: _P,
    DocumentSubtype.SPECIAL_LEVY: _P,
    # Loan
    DocumentSubtype.LOAN_CONTRACT: _P,
    DocumentSubtype.REPAYMENT_SCHEDULE: _P,
    DocumentSubtype.LOAN_TERMS_CHANGE: _P,
    DocumentSubtype.LOAN_STATEMENT: _P,
    DocumentSubtype.FINANCING_INVOICE: _P,
    # Tenancy
    DocumentSubtype.LEASE: _P,
    DocumentSubtype.HANDOVER_PROTOCOL: _P,
    DocumentSubtype.DEPOSIT_RECORD: _P,
    DocumentSubtype.LEASE_AMENDMENT: _P,
    DocumentSubtype.RENT_INCREASE: _P,
    DocumentSubtype.TERMINATION_NOTICE: _P,
    DocumentSubtype.TENANT_SELF_DISCLOSURE: _P,
    DocumentSubtype.LANDLORD_CONFIRMATION: _P,
    DocumentSubtype.OPERATING_COSTS_STATEMENT: _P,
    DocumentSubtype.TENANT_CORRESPONDENCE: _P,
    # Running costs, and the proof that a bill was paid (phase 3 D143)
    DocumentSubtype.UTILITY_BILL: _P,
    DocumentSubtype.QUOTE: _P,
    DocumentSubtype.CHIMNEY_SWEEP_NOTICE: _P,
    DocumentSubtype.PAYMENT_PROOF: _P,
    # Income tax
    DocumentSubtype.EINKOMMENSTEUERBESCHEID: _T,
    DocumentSubtype.PREPAYMENT_NOTICE: _T,
    DocumentSubtype.TAX_RETURN_COPY: _T,
    DocumentSubtype.DONATION_RECEIPT: _T,
    DocumentSubtype.CHILDCARE_INVOICE: _T,
    DocumentSubtype.HOUSEHOLD_SERVICES_INVOICE: _T,
    DocumentSubtype.INSURANCE_CONTRIBUTION_CERTIFICATE: _T,
    DocumentSubtype.PENSION_CONTRIBUTION_CERTIFICATE: _T,
    DocumentSubtype.BENEFIT_NOTICE: _T,
    DocumentSubtype.CHILD_BENEFIT_NOTICE: _T,
    DocumentSubtype.EMPLOYMENT_CONTRACT: _T,
    DocumentSubtype.EMPLOYER_CERTIFICATE: _T,
    DocumentSubtype.WORK_EXPENSE_INVOICE: _T,
    # Household finances: the depot's and the exchanges' documents, the wallet's raw records.
    # The Jahressteuerbescheinigung stays with its depot (the catalogue's household-finances
    # group) until phase 8 reads it for the Anlage KAP.
    DocumentSubtype.DEPOT_STATEMENT: _I,
    DocumentSubtype.DEPOT_REPORT: _I,
    DocumentSubtype.TRADE_CONFIRMATION: _I,
    DocumentSubtype.CAPITAL_INCOME_TAX_CERTIFICATE: _I,
    DocumentSubtype.EXCHANGE_EXPORT: _I,
    DocumentSubtype.DISTRIBUTION_NOTE: _I,
    DocumentSubtype.SECURITIES_TRANSFER: _I,
    DocumentSubtype.CHAIN_SYNC: _I,
}

# A type without a subtype; None: the type alone says nothing (an invoice, a letter, a
# contract, a Bescheid or anything else can belong anywhere, so only its links decide).
SECTION_BY_TYPE: dict[DocumentType, DocumentSection | None] = {
    DocumentType.STATEMENT: DocumentSection.ACCOUNTS,
    DocumentType.INVOICE: None,
    DocumentType.LETTER: None,
    DocumentType.CONTRACT: None,
    DocumentType.BESCHEID: None,
    DocumentType.ZINSBESCHEINIGUNG: _P,
    DocumentType.WIRTSCHAFTSPLAN: _P,
    DocumentType.HAUSGELDABRECHNUNG: _P,
    DocumentType.OTHER: None,
    DocumentType.LOHNSTEUERBESCHEINIGUNG: _T,
    DocumentType.GEHALTSABRECHNUNG: _T,
}

_ORDER = list(DocumentSection)


def document_section(
    doc_type: DocumentType | None,
    subtype: DocumentSubtype | None,
    *,
    depot_kind: str | None = None,
    signals: Iterable[SectionSignal] = (),
) -> DocumentSection:
    """The one section of a document: the first in priority order that anything claims."""
    claims: set[DocumentSection] = {_SECTION_BY_SIGNAL[s] for s in signals}
    if subtype is not None:
        claims.add(SECTION_BY_SUBTYPE[subtype])
    elif doc_type is not None and (by_type := SECTION_BY_TYPE[doc_type]) is not None:
        claims.add(by_type)
    if depot_kind is not None:  # a depot's heading read from the text layer (D177)
        claims.add(DocumentSection.INVESTMENTS)
    for section in _ORDER:
        if section in claims:
            return section
    return DocumentSection.OTHER


# --- Search -------------------------------------------------------------------------------

# The type labels the search knows, in German (the frontend's components/labels.ts) and in
# English (the Dokumente screen's catalogue, i18n/documents.ts), so a person finds a document
# by the word the screen shows them in either language.
TYPE_LABELS: dict[DocumentType, tuple[str, str]] = {
    DocumentType.STATEMENT: ("Kontoauszug", "Statement"),
    DocumentType.INVOICE: ("Rechnung", "Invoice"),
    DocumentType.LETTER: ("Brief", "Letter"),
    DocumentType.CONTRACT: ("Vertrag", "Contract"),
    DocumentType.BESCHEID: ("Bescheid", "Notice"),
    DocumentType.ZINSBESCHEINIGUNG: ("Zinsbescheinigung", "Interest certificate"),
    DocumentType.WIRTSCHAFTSPLAN: ("Wirtschaftsplan", "Budget plan"),
    DocumentType.HAUSGELDABRECHNUNG: ("Hausgeldabrechnung", "Service charge statement"),
    DocumentType.LOHNSTEUERBESCHEINIGUNG: ("Lohnsteuerbescheinigung", "Wage tax certificate"),
    DocumentType.GEHALTSABRECHNUNG: ("Gehaltsabrechnung", "Payslip"),
    DocumentType.OTHER: ("Sonstiges", "Other"),
}

SUBTYPE_LABELS: dict[DocumentSubtype, tuple[str, str]] = {
    DocumentSubtype.PURCHASE_CONTRACT: ("Kaufvertrag", "Purchase contract"),
    DocumentSubtype.PRICE_SPLIT_EVIDENCE: ("Nachweis Kaufpreisaufteilung", "Price split"),
    DocumentSubtype.APPRAISAL: ("Gutachten", "Appraisal"),
    DocumentSubtype.COST_BASIS_EVIDENCE: ("Nachweis Anschaffungskosten", "Purchase costs"),
    DocumentSubtype.GRUNDSTEUERBESCHEID: ("Grundsteuerbescheid", "Property tax notice"),
    DocumentSubtype.LAND_REGISTER_EXTRACT: ("Grundbuchauszug", "Land register extract"),
    DocumentSubtype.DECLARATION_OF_DIVISION: ("Teilungserklärung", "Declaration of division"),
    DocumentSubtype.LAND_CHARGE_DEED: ("Grundschuldbestellungsurkunde", "Land charge deed"),
    DocumentSubtype.ENERGY_CERTIFICATE: ("Energieausweis", "Energy certificate"),
    DocumentSubtype.BUILDING_RECORDS: ("Grundriss, Wohnflächen, Baupläne", "Floor plans"),
    DocumentSubtype.INSURANCE_POLICY: ("Versicherungsschein", "Insurance policy"),
    DocumentSubtype.MANAGEMENT_CONTRACT: ("Verwaltervertrag", "Management contract"),
    DocumentSubtype.SERVICE_CONTRACT: (
        "Versorgungs- oder Wartungsvertrag",
        "Utility or maintenance contract",
    ),
    DocumentSubtype.WEG_MINUTES: ("Protokoll Eigentümerversammlung", "Owners' meeting minutes"),
    DocumentSubtype.WEG_INVITATION: (
        "Einladung Eigentümerversammlung",
        "Owners' meeting invitation",
    ),
    DocumentSubtype.SPECIAL_LEVY: ("Sonderumlage", "Special levy"),
    DocumentSubtype.LOAN_CONTRACT: ("Darlehensvertrag", "Loan contract"),
    DocumentSubtype.REPAYMENT_SCHEDULE: ("Tilgungsplan", "Repayment schedule"),
    DocumentSubtype.LOAN_TERMS_CHANGE: ("Prolongation, Konditionsanpassung", "Loan terms change"),
    DocumentSubtype.LOAN_STATEMENT: ("Darlehenskontoauszug", "Loan statement"),
    DocumentSubtype.FINANCING_INVOICE: (
        "Rechnung zur Finanzierung (Grundschuld, Darlehen)",
        "Financing invoice",
    ),
    DocumentSubtype.LEASE: ("Mietvertrag", "Lease"),
    DocumentSubtype.LEASE_AMENDMENT: ("Nachtrag zum Mietvertrag", "Lease amendment"),
    DocumentSubtype.HANDOVER_PROTOCOL: ("Übergabeprotokoll", "Handover record"),
    DocumentSubtype.DEPOSIT_RECORD: ("Kautionsnachweis", "Deposit record"),
    DocumentSubtype.RENT_INCREASE: ("Mieterhöhung", "Rent increase"),
    DocumentSubtype.TERMINATION_NOTICE: ("Kündigung, Mietaufhebung", "Termination"),
    DocumentSubtype.TENANT_SELF_DISCLOSURE: (
        "Mieterselbstauskunft, Einkommensnachweis",
        "Tenant self-disclosure",
    ),
    DocumentSubtype.LANDLORD_CONFIRMATION: (
        "Wohnungsgeberbestätigung",
        "Landlord confirmation",
    ),
    DocumentSubtype.OPERATING_COSTS_STATEMENT: (
        "Betriebskostenabrechnung an Mieter",
        "Operating costs statement",
    ),
    DocumentSubtype.TENANT_CORRESPONDENCE: ("Schreiben von oder an Mieter", "Tenant letter"),
    DocumentSubtype.UTILITY_BILL: ("Abrechnung Strom, Gas, Wasser", "Utility bill"),
    DocumentSubtype.QUOTE: ("Kostenvoranschlag, Angebot", "Quote"),
    DocumentSubtype.CHIMNEY_SWEEP_NOTICE: ("Feuerstättenbescheid", "Chimney sweep notice"),
    DocumentSubtype.PAYMENT_PROOF: (
        "Zahlungsnachweis (Überweisung, Kontoauszug)",
        "Payment proof",
    ),
    DocumentSubtype.EINKOMMENSTEUERBESCHEID: ("Einkommensteuerbescheid", "Income tax assessment"),
    DocumentSubtype.PREPAYMENT_NOTICE: ("Vorauszahlungsbescheid", "Prepayment notice"),
    DocumentSubtype.TAX_RETURN_COPY: ("Steuererklärung, Übertragungsprotokoll", "Tax return"),
    DocumentSubtype.DONATION_RECEIPT: ("Zuwendungsbestätigung", "Donation receipt"),
    DocumentSubtype.CHILDCARE_INVOICE: ("Kinderbetreuung", "Childcare"),
    DocumentSubtype.HOUSEHOLD_SERVICES_INVOICE: (
        "Handwerker, haushaltsnahe Dienste (§35a)",
        "Household services (§35a)",
    ),
    DocumentSubtype.INSURANCE_CONTRIBUTION_CERTIFICATE: (
        "Beitragsbescheinigung Versicherung",
        "Insurance contribution certificate",
    ),
    DocumentSubtype.PENSION_CONTRIBUTION_CERTIFICATE: (
        "Riester- oder Rürup-Bescheinigung",
        "Pension contribution certificate",
    ),
    DocumentSubtype.BENEFIT_NOTICE: ("Bescheid Lohnersatzleistung", "Benefit notice"),
    DocumentSubtype.CHILD_BENEFIT_NOTICE: ("Kindergeldbescheid", "Child benefit notice"),
    DocumentSubtype.EMPLOYMENT_CONTRACT: ("Arbeitsvertrag", "Employment contract"),
    DocumentSubtype.EMPLOYER_CERTIFICATE: ("Arbeitgeberbescheinigung", "Employer certificate"),
    DocumentSubtype.WORK_EXPENSE_INVOICE: ("Arbeitsmittel, Fortbildung", "Work equipment"),
    DocumentSubtype.DEPOT_STATEMENT: ("Depotauszug", "Depot statement"),
    DocumentSubtype.DEPOT_REPORT: ("Depotbericht", "Depot report"),
    DocumentSubtype.TRADE_CONFIRMATION: ("Wertpapierabrechnung", "Trade confirmation"),
    DocumentSubtype.CAPITAL_INCOME_TAX_CERTIFICATE: (
        "Jahressteuerbescheinigung Kapitalerträge",
        "Annual tax certificate",
    ),
    DocumentSubtype.EXCHANGE_EXPORT: ("Börse / Exchange-Export (CSV)", "Exchange export (CSV)"),
    DocumentSubtype.DISTRIBUTION_NOTE: ("Ertragsabrechnung (Ausschüttung)", "Distribution note"),
    DocumentSubtype.SECURITIES_TRANSFER: (
        "Wertpapierübertrag (Ein- oder Ausbuchung)",
        "Securities transfer",
    ),
    DocumentSubtype.CHAIN_SYNC: ("Wallet-Abruf (Rohdaten der Chain)", "Wallet sync"),
}

_TRANSLITERATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _fold(text: str) -> str:
    """Lower case, umlauts and accents dropped to their base letter, ß as ss."""
    lowered = text.casefold().replace("ß", "ss")
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _folds(text: str) -> tuple[str, str]:
    """Both spellings a German word is searched by: "übergabe" as "ubergabe" and "uebergabe"."""
    lowered = text.casefold()
    return _fold(lowered), _fold(lowered.translate(_TRANSLITERATION))


def _amount_texts(amount: Decimal) -> list[str]:
    """1234.5 as "1234.50", "1234,50", "1.234,50" and "1,234.50"."""
    plain = f"{abs(amount):.2f}"
    grouped = f"{abs(amount):,.2f}"
    german = grouped.translate(str.maketrans({",": ".", ".": ","}))
    return [plain, plain.replace(".", ","), german, grouped]


def search_texts(
    *,
    title: str | None,
    doc_type: DocumentType | None,
    subtype: DocumentSubtype | None,
    original_filename: str | None,
    issuer: str | None,
    document_date: date | None,
    received_on: date | None,
    tax_year: int | None,
    amount: Decimal | None,
) -> list[str]:
    """Everything the search looks in for one document."""
    texts = [t for t in (title, original_filename, issuer) if t]
    if doc_type is not None:
        texts.extend(TYPE_LABELS[doc_type])
    if subtype is not None:
        texts.extend(SUBTYPE_LABELS[subtype])
    for day in (document_date, received_on):
        if day is not None:
            texts.extend((day.isoformat(), f"{day:%d.%m.%Y}", f"{day:%m.%Y}"))
    if tax_year is not None:
        texts.append(str(tax_year))
    if amount is not None:
        texts.extend(_amount_texts(amount))
    return texts


def query_words(q: str | None) -> list[str]:
    """The words of a search, folded; empty for no search."""
    if q is None:
        return []
    return [_fold(word) for word in q.split() if word.strip()]


def matches(words: list[str], texts: list[str]) -> bool:
    """True when every word occurs in one of the texts (case- and umlaut-insensitive)."""
    if not words:
        return True
    haystack = " ".join(f"{a} {b}" for a, b in map(_folds, texts))
    return all(word in haystack for word in words)
