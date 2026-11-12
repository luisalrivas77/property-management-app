"""DKB's Kundenreferenz is the counterparty's, so a DKB line hashes by its content and its
position among identical lines (phase 6 D173 (3), D22's fallback), never by that reference:
two different lines of one day, amount and customer reference are both kept (review of PR
#221, F3). The reference stays on the parsed line (fixture dkb_csv.yaml pins it)."""

from uuid import UUID

from hausbuch.services.bank import parse_statement
from hausbuch.services.statements import _line_hashes

KEY = b"k" * 32
ACCOUNT = UUID("00000000-0000-0000-0000-000000000001")
HEAD = (
    '﻿"Girokonto";"DE61 1000 0000 0000 0030 04"\n""\n'
    '"Kontostand vom 05.11.2026:";"1.234,56 €"\n""\n'
    '"Buchungsdatum";"Wertstellung";"Status";"Zahlungspflichtige*r";"Zahlungsempfänger*in";'
    '"Verwendungszweck";"Umsatztyp";"IBAN";"Betrag (€)";"Gläubiger-ID";"Mandatsreferenz";'
    '"Kundenreferenz"\n'
)


def _row(payee: str, purpose: str, ref: str) -> str:
    return (
        f'"01.11.26";"01.11.26";"Gebucht";"Owner A";"{payee}";"{purpose}";"Ausgang";"";'
        f'"-35,00";"";"";"{ref}"\n'
    )


def _hashes(text: str) -> list[str]:
    statement = parse_statement(text.encode("utf-8"), "text/csv").statement
    return _line_hashes(ACCOUNT, statement, KEY)


def test_two_different_lines_with_one_customer_reference_hash_apart():
    text = HEAD + _row("Verein Beispiel", "Beitrag", "KREF-1") + _row("Shop", "Kauf", "KREF-1")
    statement = parse_statement(text.encode("utf-8"), "text/csv").statement
    assert [line.end_to_end_ref for line in statement.lines] == ["KREF-1", "KREF-1"]
    first, second = _hashes(text)
    assert first != second


def test_identical_lines_are_kept_by_position_and_a_reupload_hashes_the_same():
    text = HEAD + _row("Shop", "Kauf", "KREF-1") + _row("Shop", "Kauf", "KREF-1")
    first, second = _hashes(text)
    assert first != second
    assert _hashes(text) == [first, second]
