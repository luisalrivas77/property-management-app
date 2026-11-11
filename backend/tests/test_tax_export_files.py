"""Phase 3 step 3: the export files rendered from the fixture's `export_roundtrip` case
(docs/phases/phase-3.md §12, §14): the lines CSV equals the expected field sheet, the draft
survives its JSON snapshot, both files are byte-for-byte reproducible, the sheet and the CSVs
carry no full IBAN, and the archive's README lists the documents that need a Finanzamt copy.
No database: services/tax_export_files.py is pure."""

import csv
import io
import re
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase3 import DraftFixture, load
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import BooleanObject, NameObject

from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.registry import rule_set
from hausbuch.schemas.tax import AnlageVDraft, TaxInputFact, WarningCode
from hausbuch.services.tax_export_files import (
    WARNING_TEXT,
    ArchiveDocument,
    ExportStamp,
    archive_document_ids,
    draft_json,
    finanzamt_copy,
    lines_csv,
    payments_csv,
    render_archive,
    render_field_sheet,
    safe_filename,
)

FIXTURE = load("anlage_v_2026")
assert isinstance(FIXTURE, DraftFixture)
CASE = next(c for c in FIXTURE.cases if c.id == "export_roundtrip")
RULES = rule_set(CASE.rule_set_year)
DRAFT = draft(CASE.facts, CASE.tax_year, RULES)
STAMP = ExportStamp(
    export_id=UUID("00000000-0000-4000-8000-000000000007"),
    created_at=datetime(2027, 1, 15, 9, 30, 12, tzinfo=UTC),
)
# A full IBAN: two letters, two digits, then at least ten more characters, spaces allowed.
FULL_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,}\b")


def _rows(data: bytes) -> list[list[str]]:
    text = data.decode("utf-8")
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:]), delimiter=";"))


def _pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _pdf(*, annotation: bool = False, xfa: bool = False) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if annotation:
        writer.add_annotation(0, FreeText(text="unterschrieben", rect=(10, 10, 100, 40)))
    if xfa:
        writer._root_object[NameObject("/NeedsRendering")] = BooleanObject(True)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _archive(documents=()) -> bytes:
    return render_archive(
        DRAFT,
        RULES,
        CASE.facts,
        STAMP,
        property_label="Wohnung Beispielweg",
        documents=list(documents),
    )


@pytest.mark.rule
def test_export_roundtrip_draft_survives_its_snapshot():
    assert CASE.expect.roundtrip
    assert AnlageVDraft.model_validate(draft_json(DRAFT)) == DRAFT


@pytest.mark.rule
def test_export_roundtrip_lines_csv_is_the_field_sheet():
    rows = _rows(lines_csv(DRAFT, RULES))
    assert rows[0] == ["Schluessel", "Zeile", "Kennzahl", "Betrag", "Eintrag_volle_Euro"]
    expected = CASE.expect.field_sheet
    assert expected is not None and len(expected) == 14
    got = [
        (key, int(zeile), kennzahl or None, Decimal(amount.replace(",", ".")), int(rounded))
        for key, zeile, kennzahl, amount, rounded in rows[1:]
    ]
    assert got == [(r.key, r.zeile, r.kennzahl, r.amount, r.amount_rounded) for r in expected]


def test_field_sheet_pdf_is_reproducible_and_complete():
    first = render_field_sheet(DRAFT, RULES, CASE.facts, STAMP)
    assert first == render_field_sheet(DRAFT, RULES, CASE.facts, STAMP)
    text = _pdf_text(first)
    assert "ELSTER-Ausfüllhilfe Anlage V 2026" in text
    assert "VORLÄUFIG" in text  # form 2025 under the 2026 rule set
    assert str(STAMP.export_id) in text
    assert "AfA" in text
    assert "Planungshilfe" in text or "keine Steuerberatung" in text


def test_field_sheet_explains_a_shorter_useful_life():
    """D79: under a Gutachten's useful life the AfA annex states the years, the first year and
    the book value at the start of the year instead of the statutory rate."""
    life = TaxInputFact(
        id=UUID("00000071-0000-4000-8000-000000000009"),
        property_id=CASE.facts.property.id,
        kind="useful_life",
        tax_year=CASE.tax_year,
        document_id=UUID("00000d0c-0000-4000-8000-000000000009"),
        value={"years": 20},
    )
    facts = CASE.facts.model_copy(update={"tax_inputs": [*CASE.facts.tax_inputs, life]})
    drafted = draft(facts, CASE.tax_year, RULES)
    assert drafted.afa is not None and drafted.afa.method == "useful_life"
    text = _pdf_text(render_field_sheet(drafted, RULES, facts, STAMP))
    flat = " ".join(text.split())
    assert "kürzerer tatsächlicher Nutzungsdauer" in flat
    assert f"20 Jahre ab {CASE.tax_year} laut Gutachten" in flat
    assert "Restwert zu Jahresbeginn" in flat
    assert "Zeile 33" in flat  # the afa.useful_life_report warning text


def test_a_private_repair_in_the_basis_is_listed_and_its_bill_archived():
    """D137: a repair paid from another account that crossed the 15 % limit is a row of the
    AfA annex (label and gross), and its bill, which no payment carries, is in the archive."""
    case = next(c for c in FIXTURE.cases if c.id == "private_repair_crossing_moves_to_basis")
    drafted = draft(case.facts, case.tax_year, rule_set(case.rule_set_year))
    (row,) = [r for r in case.facts.tax_inputs if r.kind == "private_cost"]
    flat = " ".join(_pdf_text(render_field_sheet(drafted, RULES, case.facts, STAMP)).split())
    assert "von einem anderen Konto bezahlt: Dachreparatur (Steuerjahr 2026)" in flat
    assert "41.650,00" in flat
    assert row.document_id in archive_document_ids(drafted, case.facts)


def test_every_warning_code_has_a_sheet_text():
    """A draft warning without a text would fail the export at render time."""
    assert set(WARNING_TEXT) == set(WarningCode)


def test_the_payslip_gap_text_names_the_missing_month():
    text = WARNING_TEXT[WarningCode.ANLAGE_N_PAYSLIP_GAP]
    gap = {"month": "2026-08", "previous": "2026-06", "missing": "2026-07", "employer": "X"}
    assert text(gap).startswith("Gehaltsabrechnung Juli 2026 fehlt")
    assert "X" not in text(gap)  # the sheet names no employer
    assert "August 2026 passt nicht" in text({"month": "2026-08", "previous": "2026-07"})
    assert text({}) == "Zwischen den Gehaltsabrechnungen fehlt ein Monat."


def test_field_sheet_shows_a_kitchen_line_and_takes_it_out_of_the_building():
    """D80: a kitchen in the price has its own row (Z42–45, Kz 60) with Z42's method, the
    building annex subtracts its basis, a kitchen annex explains its AfA, and its missing
    Beleg is a warning (the warning text must exist, or rendering fails)."""
    kitchen = TaxInputFact(
        id=UUID("00000080-0000-4000-8000-000000000001"),
        property_id=CASE.facts.property.id,
        kind="movable_asset",
        tax_year=CASE.facts.property.purchase_date.year,
        value={"label": "einbaukueche", "amount": "8500.00"},
    )
    facts = CASE.facts.model_copy(update={"tax_inputs": [*CASE.facts.tax_inputs, kitchen]})
    drafted = draft(facts, CASE.tax_year, RULES)
    (asset,) = drafted.movable_assets
    assert drafted.afa is not None and drafted.afa.movable_deducted == asset.basis
    sheet = render_field_sheet(drafted, RULES, facts, STAMP)
    assert sheet == render_field_sheet(drafted, RULES, facts, STAMP)
    flat = " ".join(_pdf_text(sheet).split())
    assert "Zeile 42: Methode" in flat
    assert "Einbauküche mit ihrem Anteil an den Nebenkosten" in flat
    assert "Erläuterung zur AfA Einbauküche (Zeile 42–45)" in flat
    assert f"Nutzungsdauer 10 Jahre ab {kitchen.tax_year}" in flat
    assert "ohne Beleg erfasst" in flat
    rows = {r[0]: r for r in _rows(lines_csv(drafted, RULES))[1:]}
    assert rows["wk.afa_movable"][1:3] == ["45", "60"]


def test_archive_is_reproducible():
    documents = [
        ArchiveDocument(document_id=UUID(int=1), number="001", filename="a.pdf", data=_pdf())
    ]
    assert _archive(documents) == _archive(documents)


def test_sheet_and_csvs_carry_no_full_iban():
    masked = {a.counterparty_iban_masked for a in CASE.facts.allocations} - {None}
    assert masked  # the fixture has counterparties, so the check below means something
    sheet = _pdf_text(render_field_sheet(DRAFT, RULES, CASE.facts, STAMP))
    payments = payments_csv(DRAFT, CASE.facts, {}).decode("utf-8")
    lines = lines_csv(DRAFT, RULES).decode("utf-8")
    for text in (sheet, payments, lines):
        assert FULL_IBAN.search(text) is None
    assert any(m in payments for m in masked)


def test_archive_holds_every_document_and_lists_the_copies():
    plain = _pdf()
    signed = _pdf(annotation=True)
    xfa = _pdf(xfa=True)
    image = b"\x89PNG\r\n\x1a\n not really"
    assert finanzamt_copy(plain, "application/pdf") is None
    assert finanzamt_copy(signed, "application/pdf") == "annotations"
    assert finanzamt_copy(xfa, "application/pdf") == "xfa"
    assert finanzamt_copy(image, "image/png") is None
    documents = [
        ArchiveDocument(UUID(int=1), "001", "Kaufvertrag.pdf", plain),
        ArchiveDocument(UUID(int=2), "002", "Mietvertrag.pdf", signed, "annotations"),
        ArchiveDocument(UUID(int=3), "003", "Darlehensvertrag.pdf", xfa, "xfa"),
        ArchiveDocument(UUID(int=4), "004", "Foto.png", image),
    ]
    archive = zipfile.ZipFile(io.BytesIO(_archive(documents)))
    assert archive.testzip() is None
    names = archive.namelist()
    assert names[:4] == ["ausfuellhilfe.pdf", "anlage-v-2026.csv", "zahlungen.csv", "README.txt"]
    for document in documents:
        assert archive.read(document.path) == document.data  # untouched
        assert archive.getinfo(document.path).compress_type == zipfile.ZIP_STORED
    readme = archive.read("README.txt").decode("utf-8")
    copies = readme.split("Für das Finanzamt eine Kopie nötig", 1)[1]
    assert "002 Mietvertrag.pdf" in copies and "003 Darlehensvertrag.pdf" in copies
    assert "001 Kaufvertrag.pdf" not in copies and "004 Foto.png" not in copies


def test_archive_document_ids_cover_the_cited_documents():
    ids = archive_document_ids(DRAFT, CASE.facts)
    assert len(ids) == len(set(ids))
    cited = {a.document_id for a in CASE.facts.allocations if a.document_id is not None}
    in_year = {
        a.document_id
        for a in CASE.facts.allocations
        if a.document_id is not None and a.booking_date.year == CASE.tax_year
    }
    assert in_year <= set(ids) and set(ids) & cited


def test_payments_csv_numbers_the_belege():
    ids = archive_document_ids(DRAFT, CASE.facts)
    numbers = {document_id: f"{n:03d}" for n, document_id in enumerate(ids, start=1)}
    rows = _rows(payments_csv(DRAFT, CASE.facts, numbers))
    assert rows[0][-2:] == ["Beleg_Nr", "Gegenkonto"]
    assert {r[-2] for r in rows[1:]} - {""} <= set(numbers.values())
    assert [r[0] for r in rows[1:]] == sorted(r[0] for r in rows[1:])


@pytest.mark.parametrize(
    ("name", "safe"),
    [
        ("Mietvertrag WE 1.pdf", "Mietvertrag WE 1.pdf"),
        ("a/b\\c:d*e?.pdf", "a b c d e .pdf"),
        ("../../etc/passwd", "etc passwd"),
        ("", "dokument"),
    ],
)
def test_safe_filename(name, safe):
    assert safe_filename(name) == safe
