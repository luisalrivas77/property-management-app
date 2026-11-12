"""The letter to a tenant (docs/phases/phase-4.md D155 (7)) as rendered: the PDF's text holds
the fixture's figures, the subject, the start and the enclosed documents, never electricity
and never an IBAN-shaped string, even when a document title or the sender's typed lines carry
one. No database: the renderer is driven with flat_rate_letter.yaml's figures."""

import io
import re
from uuid import UUID

import pytest
from fixture_models_phase4 import load
from pypdf import PdfReader

from hausbuch.rules.iban import _IBAN_LIKE
from hausbuch.schemas.performance import (
    FlatRateLetterFigures,
    FlatRateLetterRequest,
    FlatRateVerdict,
)
from hausbuch.schemas.property import Address
from hausbuch.services.flat_rate_letter import (
    LetterContent,
    _document_label,
    render_flat_rate_letter,
)
from hausbuch.services.tax_export_files import _date, _money

LETTER = load("flat_rate_letter")
CASES = [c for c in LETTER.cases if c.expect is not None]
IBAN = "DE89370400440532013000"  # the standard example IBAN: valid checksum
ADDRESS = Address(street="Beispielweg", house_number="7", postal_code="12345", city="Musterstadt")


def _content(figures: FlatRateLetterFigures, *, sender_lines=("Musterallee 1",)) -> LetterContent:
    refs = [s for r in figures.reasons for s in (*r.then_sources, *r.now_sources)]
    labels = {
        (s.kind, s.id): f"Wirtschaftsplan {i} vom 01.12.2026 (Konto {IBAN})"
        for i, s in enumerate(refs)
    }
    return LetterContent(
        figures=figures,
        request=FlatRateLetterRequest(
            sent_on=figures.sent_on,
            sender_name="Eigentümer A",
            sender_address=list(sender_lines),
        ),
        tenants=["Mieterin B"],
        address=ADDRESS,
        source_labels=labels,
        enclosed=frozenset(labels),
    )


def _text(data: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_the_letter_prints_the_figures(case):
    f = FlatRateLetterFigures.model_validate(case.expect.model_dump())
    data = render_flat_rate_letter(_content(f))
    assert data.startswith(b"%PDF")
    text = _flat(_text(data))
    for figure in (f.pauschale, f.new_pauschale, f.cold_rent, f.new_total, f.pauschalen_total):
        assert _money(figure) in text, (case.id, figure)
    assert _money(abs(f.rise)) in text
    assert f"ab dem {_date(f.starts_on)}" in text
    if f.kind is FlatRateVerdict.RAISE:
        assert "Erhöhung der Nebenkostenpauschale (§ 560 Abs. 1 BGB, Ziffer 4.1" in text
        assert "Zugang dieses Schreibens" in text
    else:
        assert "Herabsetzung der Nebenkostenpauschale (§ 560 Abs. 3 BGB)" in text
    assert "Mieterin B" in text and "Beispielweg 7" in text and f.unit_label in text
    assert "Wirtschaftsplan 0 vom 01.12.2026" in text  # an enclosure by title and date
    assert "Strom" not in text  # electricity is never a reason (D155 (3))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_no_iban_shaped_string_reaches_the_letter(case):
    f = FlatRateLetterFigures.model_validate(case.expect.model_dump())
    text = _text(render_flat_rate_letter(_content(f, sender_lines=(f"IBAN {IBAN}",))))
    assert IBAN not in text.replace(" ", "")
    assert _IBAN_LIKE.search(_flat(text)) is None
    assert "DE89 **** 3000" in _flat(text)  # masked, as everywhere else


def test_a_bank_line_is_named_in_the_table_but_not_enclosed():
    f = FlatRateLetterFigures.model_validate(CASES[0].expect.model_dump())
    content = _content(f)
    first = f.reasons[0].then_sources[0]
    bank = {**content.source_labels, (first.kind, first.id): "Kontoauszug, Zahlung vom 01.07.2026"}
    enclosed = content.enclosed - {(first.kind, first.id)}
    data = render_flat_rate_letter(
        LetterContent(
            figures=f,
            request=content.request,
            tenants=content.tenants,
            address=content.address,
            source_labels=bank,
            enclosed=enclosed,
        )
    )
    text = _flat(_text(data))
    table, enclosures = text.split("Kopien dieser Unterlagen bei:")
    assert "Kontoauszug, Zahlung vom 01.07.2026" in table
    assert "Kontoauszug" not in enclosures


def test_the_same_content_gives_the_same_bytes():
    f = FlatRateLetterFigures.model_validate(CASES[0].expect.model_dump())
    assert render_flat_rate_letter(_content(f)) == render_flat_rate_letter(_content(f))


def test_a_document_is_named_by_title_and_date_never_its_file_name():
    from datetime import UTC, date, datetime

    from hausbuch.schemas.document import Document, DocumentTyping

    document = Document(
        id=UUID(int=1),
        workspace_id=UUID(int=2),
        created_at=datetime(2026, 12, 1, tzinfo=UTC),
        file_sha256="0" * 64,
        source="upload",
        original_filename="Kontoauszug Mieter Name.pdf",
        received_at=datetime(2026, 12, 1, tzinfo=UTC),
        updated_at=datetime(2026, 12, 1, tzinfo=UTC),
        typing=DocumentTyping(
            type="wirtschaftsplan", title="Wirtschaftsplan 2027", document_date=date(2026, 12, 1)
        ),
    )
    assert _document_label(document, "Beleg") == "Wirtschaftsplan 2027 vom 01.12.2026"
    untyped = document.model_copy(update={"typing": None})
    assert _document_label(untyped, "Kontoauszug, Zahlung vom 01.01.2027") == (
        "Kontoauszug, Zahlung vom 01.01.2027"
    )
