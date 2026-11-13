"""A depot document in plain words (phase 6 D177, rules/depot_explain.yaml).

Every text below is invented: headings in the depot bank's shape, made-up figures, funds and
account names. Nothing comes from a real document.
"""

from datetime import date
from decimal import Decimal

import pytest

from hausbuch.rules import depot_explain as explain
from hausbuch.schemas.enums import DepotDocumentKind as K

BANK = "\nQuirin Privatbank AG\n"  # the letterhead every depot document prints

# One invented text per kind: its heading as the depot bank prints it, plus the letterhead.
TEXTS = {
    K.EINBUCHUNG: "Einbuchung von Wertpapieren\nHinweistext",
    K.AUSBUCHUNG: "Ausbuchung von Wertpapieren\nHinweistext",
    K.FINANZREPORT: "Finanzreport\nKontoauszug\nDepotauszug",
    K.JAHRESSTEUERBESCHEINIGUNG: "Jahressteuerbescheinigung 2026\nErträgnisaufstellung",
    K.PM_REPORTING: "Detailreport\nWertpapierumsätze\nKauf\nVerkauf",
    K.ERTRAEGNISABRECHNUNG: "Erträgnisabrechnung\nAusschüttung",
    K.WERTPAPIERABRECHNUNG_KAUF: "Wertpapierabrechnung\nKauf",
    K.WERTPAPIERABRECHNUNG_VERKAUF: "Wertpapierabrechnung Verkauf",
    K.WERTPAPIERABRECHNUNG: "Wertpapierabrechnung\nFestpreisgeschäft",
    K.ERTRAGSUEBERSICHT: "Erträgnisaufstellung 2026\nVorabpauschale",
    K.KOSTENINFORMATION: "Kosteninformation\nVermögensverwaltung",
    K.KOSTENAUSWEIS: "Kostenübersicht 2026\nDie Kosten je Kauf können Sie der entsprechenden "
    "Wertpapierabrechnung entnehmen.",
    K.RECHNUNGSABSCHLUSS: "Rechnungsabschluss für den Zeitraum",
    K.KONTOAUSZUG: "Kontoauszug\nDer Rechnungsabschluss liegt bei.",
    K.DEPOTAUSZUG: "Depotauszug zum Stichtag",
    K.QUARTALSBERICHT: "Vermögensverwaltung\nBericht für das 2. Quartal 2026",
    K.REBALANCING: "Rebalancing Ihres Portfolios",
    K.ZINSAENDERUNG: "Zinsanpassung auf dem Verrechnungskonto",
}

# Invented figures for every placeholder, formatted as the service passes them.
FACTS = {
    "konto": "Beispiel-Depot A",
    "datum": "14.05.2026",
    "stichtag": "30.06.2026",
    "fonds": "Beispiel Welt UCITS ETF",
    "anteile": "4,8",
    "betrag": "1.234,56 €",
    "brutto": "12,34 €",
    "netto": "9,08 €",
    "gebuehren": "1,00 €",
    "steuern": "3,26 €",
    "positionen": "3",
    "gesamtwert": "45.678,90 €",
    "umsaetze": "2",
}

INFO = "Nur zur Information, ändert nichts am Konto; wird abgelegt."


def test_the_catalogue_has_one_entry_per_kind():
    assert {entry.kind for entry in explain.catalogue().kinds} == set(K)


@pytest.mark.parametrize("kind", list(K))
def test_each_kind_is_found_by_its_heading(kind):
    assert explain.document_kind(TEXTS[kind] + BANK) is kind


@pytest.mark.parametrize(
    "text",
    [
        "Ihre Wertpapierabrechnung liegt bei.",  # names no depot, no heading
        "Kontoauszug Sparkasse\nIBAN",
        "Quirin Privatbank AG\nIhr Schreiben vom",  # a depot letter no entry knows
        "",
    ],
)
def test_a_document_no_entry_knows_has_no_kind(text):
    assert explain.document_kind(text) is None


def test_a_trade_note_without_a_side_in_its_heading_takes_its_readings_side():
    assert explain.with_side(K.WERTPAPIERABRECHNUNG, "Kauf") is K.WERTPAPIERABRECHNUNG_KAUF
    assert explain.with_side(K.WERTPAPIERABRECHNUNG, "Verkauf") is K.WERTPAPIERABRECHNUNG_VERKAUF
    assert explain.with_side(K.WERTPAPIERABRECHNUNG, None) is K.WERTPAPIERABRECHNUNG
    assert explain.with_side(K.EINBUCHUNG, "Kauf") is K.EINBUCHUNG


@pytest.mark.parametrize("kind", list(K))
def test_each_kind_has_all_three_parts_with_every_figure_filled(kind):
    result = explain.explain(kind, FACTS)
    assert result is not None
    assert result.was and result.aktion
    assert result.bedeutung is not None
    assert "{" not in result.bedeutung and "}" not in result.bedeutung


# What each kind says with every figure read: the sentences in the catalogue's order.
EXPECTED = {
    K.WERTPAPIERABRECHNUNG_KAUF: (
        "4,8 Anteile des Fonds Beispiel Welt UCITS ETF wurden gekauft. Handelstag: 14.05.2026. "
        "Bezahlt (Ausmachender Betrag): 1.234,56 €. Darin Gebühren: 1,00 €. "
        "Depot: Beispiel-Depot A."
    ),
    K.WERTPAPIERABRECHNUNG_VERKAUF: (
        "4,8 Anteile des Fonds Beispiel Welt UCITS ETF wurden verkauft. Handelstag: 14.05.2026. "
        "Gutgeschrieben (Ausmachender Betrag): 1.234,56 €. Abgezogene Gebühren: 1,00 €. "
        "Abgezogene Steuern: 3,26 €. Depot: Beispiel-Depot A."
    ),
    K.ERTRAEGNISABRECHNUNG: (
        "Der Fonds Beispiel Welt UCITS ETF hat ausgeschüttet. Zahltag: 14.05.2026. "
        "Für 4,8 Anteile im Depot. Vor Steuern: 12,34 €. Abgezogene Steuern: 3,26 €. "
        "Gutgeschrieben (Ausmachender Betrag): 9,08 €. Depot: Beispiel-Depot A."
    ),
    K.PM_REPORTING: (
        "Stichtag: 30.06.2026. Laut Bericht liegen 3 Fonds im Depot, zusammen 45.678,90 € wert. "
        "Er listet 2 Käufe und Verkäufe. Depot: Beispiel-Depot A."
    ),
    K.KONTOAUSZUG: "Datum: 14.05.2026. Konto: Beispiel-Depot A.",
    K.REBALANCING: "Datum: 14.05.2026.",
}


@pytest.mark.parametrize("kind", list(EXPECTED))
def test_the_figures_stand_in_their_sentences(kind):
    assert explain.explain(kind, FACTS).bedeutung == EXPECTED[kind]


def test_a_transfer_names_one_fund_or_counts_several():
    one = {"anteile": "4,8", "fonds": "Beispiel Welt UCITS ETF", "datum": "14.05.2026"}
    assert explain.explain(K.EINBUCHUNG, one).bedeutung == (
        "4,8 Anteile des Fonds Beispiel Welt UCITS ETF sind hinzugekommen. "
        "Tag der Einbuchung: 14.05.2026."
    )
    several = {"positionen": "3", "datum": "14.05.2026", "konto": "Beispiel-Depot B"}
    assert explain.explain(K.AUSBUCHUNG, several).bedeutung == (
        "Anteile von 3 Fonds sind abgegangen. Tag der Ausbuchung: 14.05.2026. "
        "Depot: Beispiel-Depot B."
    )


def test_a_sentence_whose_figure_was_not_read_is_left_out():
    facts = {"fonds": "Beispiel Welt UCITS ETF", "datum": "14.05.2026", "betrag": None, "konto": ""}
    # No units: the first sentence goes; no amount, fees or account: theirs go too.
    assert explain.explain(K.WERTPAPIERABRECHNUNG_KAUF, facts).bedeutung == (
        "Handelstag: 14.05.2026."
    )


def test_with_no_figure_at_all_only_what_it_is_and_what_happens_remain():
    result = explain.explain(K.KOSTENINFORMATION, {})
    assert result.bedeutung is None
    assert result.was.startswith("Kosteninformation")
    assert result.aktion.startswith(INFO)


@pytest.mark.parametrize(
    "kind",
    [
        K.FINANZREPORT,
        K.JAHRESSTEUERBESCHEINIGUNG,
        K.ERTRAGSUEBERSICHT,
        K.KOSTENINFORMATION,
        K.KOSTENAUSWEIS,
        K.RECHNUNGSABSCHLUSS,
        K.DEPOTAUSZUG,
        K.QUARTALSBERICHT,
        K.REBALANCING,
        K.ZINSAENDERUNG,
    ],
)
def test_a_document_that_books_nothing_says_so(kind):
    assert explain.explain(kind, FACTS).aktion.startswith(INFO)


@pytest.mark.parametrize(
    "kind",
    [
        K.WERTPAPIERABRECHNUNG_KAUF,
        K.WERTPAPIERABRECHNUNG_VERKAUF,
        K.WERTPAPIERABRECHNUNG,
        K.ERTRAEGNISABRECHNUNG,
        K.PM_REPORTING,
        K.EINBUCHUNG,
        K.AUSBUCHUNG,
    ],
)
def test_a_document_that_books_names_the_button(kind):
    assert "„Übernehmen“" in explain.explain(kind, FACTS).aktion


def test_the_terms_the_documents_use_are_explained():
    assert "Ausmachende Betrag" in explain.explain(K.WERTPAPIERABRECHNUNG_KAUF, {}).was
    assert "Vorabpauschale" in explain.explain(K.ERTRAGSUEBERSICHT, {}).was
    assert "kein Geld" in explain.explain(K.EINBUCHUNG, {}).was
    assert "Zinsen" in explain.explain(K.RECHNUNGSABSCHLUSS, {}).was


def test_german_formatting():
    assert explain.money(Decimal("1234.5")) == "1.234,50 €"
    assert explain.money(Decimal("-0.5")) == "0,50 €"
    assert explain.money(Decimal("1234567.891")) == "1.234.567,89 €"
    assert explain.day(date(2026, 5, 14)) == "14.05.2026"
    assert explain.units(Decimal("4.8000000000")) == "4,8"
    assert explain.units(Decimal("1234.123456")) == "1.234,123456"
    assert explain.units(Decimal("-12")) == "12"
    assert explain.units(Decimal("1E+3")) == "1.000"
