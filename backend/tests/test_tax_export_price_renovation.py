"""Phase 3 D287 on the ELSTER-Ausfüllhilfe: the AfA annex takes the seller's renovation out of
the building's share (and lists it again when it returned over the 15 % limit), and in the
year the price was paid the sheet prints the sentence for „Ergänzende Angaben zur
Steuererklärung“ that makes the claim openly. Declined: none of it. Invented figures
(fixture phase3/price_renovation.yaml)."""

import io
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fixture_models_phase3 import DraftFixture, load
from pypdf import PdfReader

from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.registry import rule_set
from hausbuch.services.tax_export_files import (
    ExportStamp,
    archive_document_ids,
    price_renovation_statement,
    render_field_sheet,
)

FIXTURE = load("price_renovation")
assert isinstance(FIXTURE, DraftFixture)
STAMP = ExportStamp(
    export_id=UUID("00000000-0000-4000-8000-000000000287"),
    created_at=datetime(2027, 3, 1, 9, 0, tzinfo=UTC),
)


def _sheet(case_id: str):
    case = next(c for c in FIXTURE.cases if c.id == case_id)
    rules = rule_set(case.rule_set_year)
    drafted = draft(case.facts, case.tax_year, rules)
    pdf = render_field_sheet(drafted, rules, case.facts, STAMP)
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    return case, drafted, " ".join(text.split())


def test_the_year_paid_within_the_limit_states_the_claim_openly():
    case, drafted, flat = _sheet("claimed_within_limit_year_paid")
    assert "− Renovierung des Verkäufers im Kaufpreis" in flat
    assert "23.800,00 €" in flat
    assert "Ergänzende Angaben zur Steuererklärung" in flat
    statement = price_renovation_statement(drafted, case.facts)
    assert statement is not None
    assert "23.800,00 € brutto, 20.000,00 € netto" in statement
    assert "als Erhaltungsaufwand im Jahr der Zahlung" in statement
    assert "IX R 34/03" in statement
    # The Kaufvertrag the claim cites goes into the archive with the sheet.
    (claim,) = [r for r in case.facts.tax_inputs if r.kind == "price_renovation"]
    assert claim.document_id in archive_document_ids(drafted, case.facts)


def test_over_the_limit_it_is_listed_back_in_the_basis():
    case, drafted, flat = _sheet("claimed_over_limit_returns_to_basis")
    assert "− Renovierung des Verkäufers im Kaufpreis" in flat
    assert "+ Renovierung des Verkäufers im Kaufpreis, über der 15 %-Grenze" in flat
    statement = price_renovation_statement(drafted, case.facts)
    assert statement is not None and "anschaffungsnaher Herstellungsaufwand" in statement


def test_a_later_year_keeps_the_reduced_basis_without_the_sentence():
    case, drafted, flat = _sheet("claimed_within_limit_next_year")
    assert "− Renovierung des Verkäufers im Kaufpreis" in flat
    assert "Ergänzende Angaben zur Steuererklärung" not in flat
    assert price_renovation_statement(drafted, case.facts) is None


@pytest.mark.parametrize(
    "case_id", ["declined_is_the_draft_without_it", "claimed_before_handover_not_applied"]
)
def test_a_claim_that_does_not_count_prints_nothing_of_it(case_id):
    case, drafted, flat = _sheet(case_id)
    assert "Renovierung des Verkäufers" not in flat
    assert "Ergänzende Angaben zur Steuererklärung" not in flat
    assert price_renovation_statement(drafted, case.facts) is None
