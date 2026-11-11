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
