"""The Dokumente search over a stored reading that no longer validates (phase 10 D282 review):
that document is searched by its title, type and file name only, a warning names its id and
never its content, and the rest of the search is unaffected."""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from hausbuch.rules.document_section import query_words
from hausbuch.schemas.enums import DocumentType
from hausbuch.services.documents import _search


class _Row(SimpleNamespace):
    @property
    def _mapping(self):  # what conn.execute's rows offer to dict(row._mapping)
        return self.mapping


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _query):
        return iter(self.rows)


def _document(title: str, filename: str):
    typing = SimpleNamespace(
        title=title,
        type=DocumentType.INVOICE,
        subtype=None,
        document_date=None,
        tax_year=None,
    )
    return SimpleNamespace(
        id=uuid4(),
        typing=typing,
        original_filename=filename,
        received_at=datetime(2026, 3, 20, 9, tzinfo=UTC),
    )


def test_an_invalid_reading_is_skipped_not_a_500(caplog):
    broken = _document("Rechnung Heizung", "heizung.pdf")
    other = _document("Rechnung Fenster", "fenster.pdf")
    secret = "Geheimer Inhalt 4711"
    rows = [_Row(document_id=broken.id, mapping={"document_id": broken.id, "fields": secret})]

    with caplog.at_level(logging.WARNING, logger="hausbuch.services.documents"):
        found = _search(_Conn(rows), [broken, other], query_words("heizung"))

    assert found == [broken]  # still found by its title
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"document search: skipped the reading of document {broken.id} (not valid)"]
    assert secret not in caplog.text
    assert _search(_Conn(rows), [broken, other], query_words("rechnung")) == [broken, other]
