"""A depot's cash statement read by code (phase 6 step 4, D165 (1) as amended; the import half
is in tests/db/test_services_depot_import.py).

The page text below is invented in the layout quirion's Kontoauszug has as pypdf extracts it
(values before their labels): account numbers, IBANs, references, ISINs, amounts and the
holders are made up. Each page is drawn into a PDF (reportlab), so the bytes path through pypdf
and the format sniff are replayed too.
"""

from datetime import date
from decimal import Decimal

import pytest
from test_card_pdf import draw

from hausbuch.rules import depot
from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.bank import StatementFormat
from hausbuch.services.bank import depot_pdf, detect_format, parse_statement

KONTO = "9900000123"
IBAN = "DE89370400440532013000"
ROWS = [
    "1.234,50 EUR16.03.202614.03.2026Wertpapier Verkauf, Ref.: 5500000001",
    "Beispiel MSCI World UCITS ETF Acc. o.N.",
    "IE00BEISPL01, ST -15,2500",
    "KEST: EUR -4,74",
    "-500,00 EUR17.03.202615.03.2026Wertpapier Kauf, Ref.: 5500000002",
    "Beispiel Euro Geldmarkt UCITS ETF o.N.",
    "IE00BEISPL02, ST 4,8000",
    f"-12,30 EUR01.04.202631.03.2026Vermögensverwaltungshonorar {KONTO}, 01.03.2026 - 31.03.2026",
    "Bemessungsgrundlage 12.345,67 EUR",
    "Betrag inkl. USt: -12,30 EUR (Netto: -10,34 EUR, USt: -1,96 EUR)",
    "Ref.: 5500000003",
]
HEADER = [
    "Max Mustermann oder",
    "Erika Mustermann",
    "Musterweg 1",
    "12345 Musterstadt",
    "Kontoauszug",
    f"{KONTO} EURKonto",
    f"{IBAN}IBAN",
    "Mustermann, Max oder Mustermann",
    "Mustermann, Erika",
    "Vermögensverwaltungskonto in EUR",
    "01.04.2026Datum",
    "3Auszug-Nr",
    "1 von 1Seite",
    "KTO_AUSZ / 000000000 / 20260401 08:00:00",
]


def page(rows=ROWS, opening="100,00", total="722,20", closing="822,20") -> str:
    return "\n".join(
        [
            "UmsatzValutaBuchungsdatumBuchungshinweise",
            *rows,
            f"{opening} EURalter Saldo01.03.2026letzter Auszug",
            f"{total} EURUmsatz",
            f"{closing} EURneuer Saldo0,00Limit (in Währung)",
            "0,00Sollzins in % p.a.",
            *HEADER,
        ]
    )


def test_a_cash_statement_is_sniffed_and_read():
    data = draw([page()])
    assert detect_format(data, "application/pdf") is StatementFormat.DEPOT_PDF
    outcome = parse_statement(data, "application/pdf")
    assert outcome.balance_error is None
    statement = outcome.statement
    assert statement.format is StatementFormat.DEPOT_PDF
    assert (statement.account_ref, statement.account_iban) == (KONTO, IBAN)
    assert (statement.opening_balance, statement.closing_balance) == (
        Decimal("100.00"),
        Decimal("822.20"),
    )
    sale, buy, fee = statement.lines
    assert (sale.amount, sale.value_date, sale.booking_date) == (
        Decimal("1234.50"),
        date(2026, 3, 16),
        date(2026, 3, 14),
    )
    assert sale.bank_reference == "5500000001"
    assert sale.raw == {"isin": "IE00BEISPL01", "units": "-15.2500", "tax_withheld": "4.74"}
    assert sale.purpose.startswith("Beispiel MSCI World")
    assert buy.raw == {"isin": "IE00BEISPL02", "units": "4.8000"}
    # The fee's headline names the account number: masked; its reference is a detail line.
    assert fee.booking_text == "Vermögensverwaltungshonorar **** 0123, 01.03.2026 - 31.03.2026"
    assert fee.bank_reference == "5500000003"
    assert KONTO not in fee.purpose
    assert [depot.categorise(line.booking_text) for line in statement.lines] == [
        C.SECURITY_SALE,
        C.SECURITY_PURCHASE,
        C.DEPOT_FEE,
    ]


def test_the_holders_and_the_address_never_reach_a_line():
    statement = depot_pdf.parse_depot_pages([page()]).statement
    text = " ".join(f"{line.booking_text} {line.purpose}" for line in statement.lines)
    for word in ("Mustermann", "Musterweg", "Musterstadt", IBAN):
        assert word not in text


def test_a_statement_continued_on_a_second_page_keeps_its_rows_apart_from_the_header():
    first = "\n".join(["UmsatzValutaBuchungsdatumBuchungshinweise", *ROWS[:4], *HEADER])
    second = page(rows=ROWS[4:], opening="100,00", total="722,20", closing="822,20")
    statement = depot_pdf.parse_depot_pages([first, second]).statement
    assert [line.bank_reference for line in statement.lines] == [
        "5500000001",
        "5500000002",
        "5500000003",
    ]
    assert "Mustermann" not in statement.lines[0].purpose


def test_a_file_that_does_not_add_up_is_unbalanced():
    assert depot_pdf.parse_depot_pages([page(closing="822,21")]).balance_error == (
        rules.REPORT_UNBALANCED
    )
    assert depot_pdf.parse_depot_pages([page(total="722,00")]).balance_error == (
        rules.REPORT_UNBALANCED
    )


def test_a_rechnungsabschluss_is_no_cash_statement():
    closing = "\n".join(
        [
            "Rechnungsabschluss für den Zeitraum vom 01.01.2026 bis 31.03.2026",
            "0,00 EURHabenzinsen:",
            "-0,00 EURAktueller Kontosaldo per 31.03.2026:",
            "Rechnungsabschluss",
            f"{KONTO} EURKonto",
            f"{IBAN}IBAN",
            "Vermögensverwaltungskonto in EUR",
        ]
    )
    assert not depot_pdf.looks_like([closing])
    assert detect_format(draw([closing]), "application/pdf") is StatementFormat.PDF


@pytest.mark.parametrize(
    ("pages", "code"),
    [
        ([page(rows=[], total="0,00", closing="100,00")], rules.EMPTY),
        ([page(rows=[], total="0,00", closing="90,00")], rules.UNPARSABLE),
        ([page(), page().replace(KONTO, "9900000999")], rules.UNPARSABLE),
    ],
    ids=["no_line", "balances_differ_without_lines", "two_accounts"],
)
def test_an_unreadable_statement_is_refused(pages, code):
    with pytest.raises(RuleError) as caught:
        depot_pdf.parse_depot_pages(pages)
    assert caught.value.code == code
