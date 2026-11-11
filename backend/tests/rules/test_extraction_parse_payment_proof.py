"""The payment proof's scrub in rules.extraction_parse (docs/phases/phase-3.md D143): the
payer's side of the transfer goes, whether its value stands after its label on the same line
or on the line below a label alone; the payee, the amount, the dates and the purpose stay.

All names and numbers are invented."""

import pytest

from hausbuch.rules.extraction_parse import SCRUBBED, scrub_payment_proof

pytestmark = pytest.mark.rule


@pytest.mark.parametrize(
    "label", ["Auftraggeber", "Auftraggeber:", "  Kontoinhaber :", "Kd.-Nr.", "BIC"]
)
def test_a_label_alone_takes_the_next_non_blank_line(label):
    text = f"{label}\n\nAna Testkauf\nEmpfänger: Notar Dr. Max Beispiel"
    scrubbed = scrub_payment_proof(text)
    assert "Ana Testkauf" not in scrubbed
    assert scrubbed.split("\n") == [label, "", SCRUBBED, "Empfänger: Notar Dr. Max Beispiel"]


def test_the_next_lines_indent_is_kept():
    assert scrub_payment_proof("Auftraggeber\n    Ana Testkauf") == f"Auftraggeber\n    {SCRUBBED}"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "Auftraggeber: Ana Testkauf  Empfänger: Notar Dr. Max Beispiel",
            f"Auftraggeber: {SCRUBBED}  Empfänger: Notar Dr. Max Beispiel",
        ),
        ("Kundennummer: 7788123", f"Kundennummer: {SCRUBBED}"),
        ("Kd.-Nr. 7788123", f"Kd.-Nr. {SCRUBBED}"),
        ("Kd-Nr: 7788123", f"Kd-Nr: {SCRUBBED}"),
    ],
)
def test_a_value_on_its_labels_line_goes_as_before(text, want):
    assert scrub_payment_proof(text) == want


@pytest.mark.parametrize(
    "text",
    [
        "Kontoauszug\nNr. 12/2027",
        "Kontostand\n1.234,56 EUR",
        "Kontoauszug vom 08.01.2027",
        "Betrag -1.309,00 EUR\nVerwendungszweck: Kostenrechnung 2026-0456",
        "Von: 01.01.2027\nAn: Notar Dr. Max Beispiel\nfür Rechnung 2026-0456",
    ],
)
def test_other_lines_are_untouched(text):
    assert scrub_payment_proof(text) == text


def test_the_scrub_is_idempotent():
    text = "Auftraggeber\nAna Testkauf\nKundennummer: 7788123\nEmpfänger: Notar Dr. Max Beispiel"
    once = scrub_payment_proof(text)
    assert scrub_payment_proof(once) == once


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Account holder: Ana Testkauf", f"Account holder: {SCRUBBED}"),
        (
            "Sender: Ana Testkauf  Recipient: Notar Dr. Max Beispiel",
            f"Sender: {SCRUBBED}  Recipient: Notar Dr. Max Beispiel",
        ),
        (
            "Payer Ana Testkauf Payee Notar Dr. Max Beispiel",
            f"Payer {SCRUBBED}Payee Notar Dr. Max Beispiel",
        ),
        ("Customer number: 7788123", f"Customer number: {SCRUBBED}"),
        ("Account\nAna Testkauf", f"Account\n{SCRUBBED}"),
    ],
)
def test_the_payers_english_labels_count_too(text, want):
    """D149: a bank's one-transaction summary also comes in English ("Transaction Summary")."""
    assert scrub_payment_proof(text) == want


@pytest.mark.parametrize(
    "text",
    [
        "Transaction Summary\nTransfer executed on 05.01.2027",
        "Recipient: Notar Dr. Max Beispiel\nAmount: -1,309.00 EUR\nReference: Invoice 2026-0456",
        "From 01.01.2027 to 31.01.2027",
    ],
)
def test_english_lines_without_a_payer_label_are_untouched(text):
    assert scrub_payment_proof(text) == text
