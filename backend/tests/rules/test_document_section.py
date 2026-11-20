"""The Dokumente section of a document and the search over it (phase 10 D282), pure."""

from datetime import date
from decimal import Decimal

import pytest

from hausbuch.rules.document_section import (
    SECTION_BY_SUBTYPE,
    SECTION_BY_TYPE,
    SUBTYPE_LABELS,
    TYPE_LABELS,
    SectionSignal,
    document_section,
    matches,
    query_words,
    search_texts,
)
from hausbuch.schemas.enums import DocumentSection, DocumentSubtype, DocumentType

S = DocumentSection
T = DocumentType
ST = DocumentSubtype


def test_every_type_and_subtype_is_mapped_and_labelled():
    """A new subtype or type must be placed by hand, never fall into Sonstiges unseen."""
    assert set(SECTION_BY_SUBTYPE) == set(DocumentSubtype)
    assert set(SECTION_BY_TYPE) == set(DocumentType)
    assert set(SUBTYPE_LABELS) == set(DocumentSubtype)
    assert set(TYPE_LABELS) == set(DocumentType)


def test_priority_order_is_the_enum_order():
    assert list(DocumentSection) == [S.PROPERTY, S.TAX, S.ACCOUNTS, S.INVESTMENTS, S.OTHER]


@pytest.mark.parametrize(
    ("doc_type", "subtype", "section"),
    [
        (None, None, S.OTHER),  # untyped
        (T.STATEMENT, None, S.ACCOUNTS),  # a bank or card statement
        (T.STATEMENT, ST.LOAN_STATEMENT, S.PROPERTY),  # the flat's loan, not a bank account
        (T.STATEMENT, ST.DEPOT_STATEMENT, S.INVESTMENTS),  # a depot's, not a bank's
        (T.WIRTSCHAFTSPLAN, None, S.PROPERTY),
        (T.HAUSGELDABRECHNUNG, None, S.PROPERTY),
        (T.ZINSBESCHEINIGUNG, None, S.PROPERTY),
        (T.LOHNSTEUERBESCHEINIGUNG, None, S.TAX),
        (T.GEHALTSABRECHNUNG, None, S.TAX),
        (T.BESCHEID, ST.EINKOMMENSTEUERBESCHEID, S.TAX),
        (T.BESCHEID, ST.GRUNDSTEUERBESCHEID, S.PROPERTY),
        (T.OTHER, ST.PAYMENT_PROOF, S.PROPERTY),
        (T.INVOICE, ST.FINANCING_INVOICE, S.PROPERTY),
        (T.INVOICE, ST.HOUSEHOLD_SERVICES_INVOICE, S.TAX),
        (T.OTHER, ST.EXCHANGE_EXPORT, S.INVESTMENTS),
        (T.LETTER, ST.TRADE_CONFIRMATION, S.INVESTMENTS),
        (T.CONTRACT, ST.LEASE, S.PROPERTY),
        (T.INVOICE, None, S.OTHER),  # a plain invoice: only its links place it
        (T.LETTER, None, S.OTHER),
        (T.BESCHEID, None, S.OTHER),
    ],
)
def test_what_a_document_is(doc_type, subtype, section):
    assert document_section(doc_type, subtype) is section


def test_links_place_a_plain_document():
    assert document_section(T.INVOICE, None, signals=[SectionSignal.PROPERTY_RECORD]) is S.PROPERTY
    assert document_section(T.INVOICE, None, signals=[SectionSignal.TAX_RECORD]) is S.TAX
    assert document_section(None, None, signals=[SectionSignal.BANK_STATEMENT]) is S.ACCOUNTS
    assert document_section(None, None, signals=[SectionSignal.HOLDING_RECORD]) is S.INVESTMENTS
    assert document_section(T.OTHER, None, depot_kind="finanzreport") is S.INVESTMENTS


def test_two_claims_take_the_first_section():
    """A Grunderwerbsteuerbescheid that is a Nebenkosten row's Beleg is Immobilie, though a
    Bescheid linked to an owner alone would be Steuern."""
    both = [SectionSignal.TAX_RECORD, SectionSignal.PROPERTY_RECORD]
    assert document_section(T.BESCHEID, None, signals=both) is S.PROPERTY
    assert document_section(T.BESCHEID, None, signals=[SectionSignal.TAX_RECORD]) is S.TAX
    assert (
        document_section(T.GEHALTSABRECHNUNG, None, signals=[SectionSignal.PROPERTY_RECORD])
        is S.PROPERTY
    )
    assert (
        document_section(T.STATEMENT, ST.DEPOT_STATEMENT, signals=[SectionSignal.BANK_STATEMENT])
        is S.ACCOUNTS
    )


def test_a_depot_cash_statement_is_anlagen():
    """A depot's cash statement is typed a plain `statement` (its retention keys on the subtype);
    a holding record relying on it, and no bank import, places it under Anlagen, not Konten."""
    holding = [SectionSignal.HOLDING_RECORD]
    assert (
        document_section(T.STATEMENT, None, depot_kind="cash_statement", signals=holding)
        is S.INVESTMENTS
    )
    assert document_section(T.STATEMENT, None, signals=holding) is S.INVESTMENTS
    # Uploaded with a depot's heading, before its import onto the depot.
    assert document_section(T.STATEMENT, None, depot_kind="cash_statement") is S.INVESTMENTS
    # Imported onto a bank account too: Konten stays first.
    both = [SectionSignal.HOLDING_RECORD, SectionSignal.BANK_STATEMENT]
    assert document_section(T.STATEMENT, None, signals=both) is S.ACCOUNTS
    # A plain statement nothing relies on yet is still a bank or card statement.
    assert document_section(T.STATEMENT, None) is S.ACCOUNTS


def _texts(**overrides):
    base = {
        "title": "Übergabeprotokoll WE 3",
        "doc_type": T.CONTRACT,
        "subtype": ST.HANDOVER_PROTOCOL,
        "original_filename": "scan_0042.pdf",
        "issuer": "Hausverwaltung Müller GmbH",
        "document_date": date(2026, 3, 14),
        "received_on": date(2026, 3, 20),
        "tax_year": 2026,
        "amount": Decimal("1234.50"),
    }
    return search_texts(**(base | overrides))


@pytest.mark.parametrize(
    "q",
    [
        "übergabe",
        "UBERGABE",
        "uebergabe",
        "mueller",
        "müller",
        "muller",
        "handover",  # the English subtype label
        "Vertrag",  # the German type label
        "14.03.2026",
        "2026-03-14",
        "03.2026",
        "1.234,50",
        "1234,50",
        "1234.50",
        "scan_0042",
        "übergabe 2026",  # every word, anywhere
    ],
)
def test_the_search_finds(q):
    assert matches(query_words(q), _texts())


@pytest.mark.parametrize("q", ["kaufvertrag", "übergabe 2025", "999,00"])
def test_the_search_misses(q):
    assert not matches(query_words(q), _texts())


def test_no_search_matches_everything():
    assert query_words(None) == []
    assert query_words("   ") == []
    assert matches([], [])
