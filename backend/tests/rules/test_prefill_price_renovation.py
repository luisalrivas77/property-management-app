"""Phase 3 D287 (6): a Kaufvertrag whose seller renovates after the handover, paid inside the
price, proposes tax_input:price_renovation in the contract's one pre-filled form (phase 2
D132): the gross as printed; the net as printed, else computed by code from the gross and a
printed USt. rate, else left to type; the period only as printed; the day the price was paid
and the status never proposed (the status is set when the card is saved, services.proposals).
No clause: no record. Every figure and name is invented."""

from uuid import uuid4

import pytest

from hausbuch.rules import prefill
from hausbuch.rules.extraction_parse import parse_lines
from hausbuch.schemas.ai import CitedTextBlock, ClassifyOutput, PageCitation
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS
from hausbuch.schemas.proposal import PrefillContext

KIND = ExtractionKind.PURCHASE_CONTRACT
CLAUSE = "Der Verkäufer renoviert die Wohnung nach der Übergabe auf eigene Kosten."
PAGE = "\n".join(
    [
        "Kaufvertrag über Wohnungseigentum",
        "Ahornallee 14, 01067 Dresden, Wohnung WE 5",
        "Der Kaufpreis beträgt 248.000,00 EUR.",
        "Besitz, Nutzen und Lasten gehen am 01.07.2026 auf den Käufer über.",
        CLAUSE,
        "Auf die Renovierung entfallen vom Kaufpreis 23.800,00 EUR (netto 20.000,00 EUR)",
        "einschließlich Umsatzsteuer von 19 %. Umsatzsteuer 7 %.",
        "Die Arbeiten beginnen am 06.07.2026 und enden spätestens am 30.09.2026.",
    ]
)
BASE = {
    "street": "Ahornallee",
    "house_number": "14",
    "postal_code": "01067",
    "city": "Dresden",
    "purchase_price": "248.000,00 EUR",
    "purchase_date": "01.07.2026",
}


def _records(**lines: str):
    blocks: list[CitedTextBlock] = []
    for index, (key, value) in enumerate((BASE | lines).items()):
        cited = PageCitation(cited_text=PAGE, start_page_number=1, end_page_number=2)
        blocks.append(CitedTextBlock(text=("\n" if index else "") + f"{key}: "))
        blocks.append(CitedTextBlock(text=value, citations=[cited]))
    values = parse_lines(blocks, KIND, subtype=DocumentSubtype.PURCHASE_CONTRACT)
    extraction = EXTRACTION_MODELS[KIND].model_validate(values)
    classification = ClassifyOutput(
        type=DocumentType.CONTRACT,
        subtype=DocumentSubtype.PURCHASE_CONTRACT,
        contains_tenant_data=False,
        language="de",
        confidence="high",
    )
    context = PrefillContext(document_id=uuid4(), classification=classification)
    return prefill.build_proposal(KIND, extraction, context)


def _claim(**lines: str):
    found = [r for r in _records(**lines) if r.key == "tax_input:price_renovation"]
    assert len(found) <= 1
    return found[0] if found else None


def test_no_clause_proposes_nothing():
    assert _claim() is None
    # An amount without the clause is not a claim either.
    assert _claim(renovation_amount="23.800,00 EUR") is None


def test_the_net_is_computed_from_the_gross_and_the_printed_rate():
    claim = _claim(
        renovation_clause=CLAUSE,
        renovation_amount="23.800,00 EUR",
        renovation_vat_rate="19 %",
        renovation_start="06.07.2026",
        renovation_end="30.09.2026",
    )
    assert claim is not None
    assert (claim.entity_type, claim.mode) == ("tax_input", "create")
    values = {name: v.value for name, v in claim.fields.items()}
    assert values == {
        "value.amount": "23800.00",
        "value.amount_net": "20000.00",
        "value.period_start": "2026-07-06",
        "value.period_end": "2026-09-30",
        "value.clause": CLAUSE,
    }
    net = claim.fields["value.amount_net"]
    assert net.source is None and "derived" in net.checks
    assert net.derived_from == ["renovation_amount", "renovation_vat_rate"]
    # The claim is made openly: the note names the ruling and what a refusal means.
    (note,) = claim.notes
    assert "IX R 34/03" in note and "„Übernehmen“" in note


def test_a_printed_net_wins_over_the_rate():
    claim = _claim(
        renovation_clause=CLAUSE,
        renovation_amount="23.800,00 EUR",
        renovation_amount_net="20.000,00 EUR",
        renovation_vat_rate="7 %",
    )
    assert claim is not None
    net = claim.fields["value.amount_net"]
    assert net.value == "20000.00"
    assert net.source is not None and net.derived_from == []


@pytest.mark.parametrize(
    "lines",
    [
        # no rate printed: the net is left to type
        {"renovation_amount": "23.800,00 EUR"},
        # a rate without the gross computes nothing
        {"renovation_vat_rate": "19 %"},
        # a rate of 0 % is no USt. to take off: the net is typed (or equals the gross)
        {"renovation_amount": "23.800,00 EUR", "renovation_vat_rate": "0 %"},
    ],
)
def test_without_a_gross_and_a_rate_the_net_is_left_to_type(lines):
    claim = _claim(renovation_clause=CLAUSE, **lines)
    assert claim is not None
    assert "value.amount_net" not in claim.fields


def test_only_what_is_printed_and_never_the_status_or_the_day_paid():
    claim = _claim(renovation_clause=CLAUSE)
    assert claim is not None
    assert set(claim.fields) == {"value.clause"}
