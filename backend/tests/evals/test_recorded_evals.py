"""Recorded evals (phase-2.md §13, D29): replay recorded API responses for the synthetic
documents through the parser (and rules.prefill.build_proposal once it exists).

- Real recordings (tests/evals/recordings/*.json, written by record.py with Luis's evals key)
  are checked field by field against make_docs.DOCS. A recording whose prompt or request no
  longer matches the code fails: re-record it (a prompt change is gated on its evals).
- Until the first recordings are committed, the real-recording test is skipped with that
  reason. The harness itself is tested on SYNTHETIC recordings built from the documents'
  own text (recordings.synthetic_recording); they are never API output.
"""

import io
import json
import uuid

import pytest
from make_docs import (
    BESCHEID_IDENTIFIERS,
    DOCS,
    DOCS_BY_NAME,
    FAKE_IDENTIFIERS,
    Doc,
    build_pdf,
    doc_pdf,
)
from pypdf import PdfReader
from recordings import (
    RECORDINGS_DIR,
    build_requests,
    eval_settings,
    replay,
    stale_reasons,
    synthetic_recording,
)

from hausbuch.rules.extraction_parse import normalize_value
from hausbuch.rules.iban import normalize_iban
from hausbuch.schemas.enums import ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS, value_kind
from hausbuch.schemas.proposal import PrefillContext

SETTINGS = eval_settings()
# Documents about the owners only, which must never set contains_tenant_data (classify.md).
OWNERS_ONLY = (
    "lohnsteuerbescheinigung",
    "gehaltsabrechnung",
    "einkommensteuerbescheid",
    "vorauszahlungsbescheid",
    "depotauszug",
    "grundbuchauszug",
    "verwaltervertrag",
)


def _real_recordings() -> list:
    paths = sorted(RECORDINGS_DIR.glob("*.json")) if RECORDINGS_DIR.exists() else []
    return [pytest.param(path, id=path.stem) for path in paths]


def _check(doc: Doc, recording: dict) -> None:
    result = replay(recording)
    assert result.classification.type.value == doc.type
    assert (result.classification.subtype and result.classification.subtype.value) == doc.subtype
    assert result.kind == ExtractionKind(doc.kind)
    # The tenant gate's input: every tenancy-group document sets it (D131), and the owners'
    # own papers do not. A rental-account statement may set it (its rent lines are a tenant's
    # payments), so it is not checked either way.
    if doc.contains_tenant_data:
        assert result.classification.contains_tenant_data is True
    if doc.name in OWNERS_ONLY:
        assert result.classification.contains_tenant_data is False
    wrong = {
        name: (result.values[name].value, expected)
        for name, expected in doc.expected.items()
        if result.values[name].value != expected
    }
    assert wrong == {}, f"{doc.name}: fields read wrong (got, expected): {wrong}"
    stated = [name for name in doc.expected_missing if result.values[name].value is not None]
    assert stated == [], f"{doc.name}: fields not in the document came back: {stated}"
    uncited = [name for name in doc.expected if result.values[name].source is None]
    assert uncited == [], f"{doc.name}: values without a page and quote: {uncited}"


def _build_proposal(doc: Doc, result) -> None:
    prefill = pytest.importorskip("hausbuch.rules.prefill", reason="proposals chunk not merged")

    kind = ExtractionKind(doc.kind)
    extraction = EXTRACTION_MODELS[kind].model_validate(result.values)
    context = PrefillContext(document_id=uuid.uuid4(), classification=result.classification)
    records = prefill.build_proposal(kind, extraction, context)
    assert isinstance(records, list)


# --- real recordings ----------------------------------------------------------------


@pytest.mark.parametrize("path", _real_recordings() or [pytest.param(None, id="none-yet")])
def test_real_recording(path):
    if path is None:
        pytest.skip("no live recordings yet: run tests/evals/record.py with the evals key (D29)")
    recording = json.loads(path.read_text())
    assert recording["synthetic"] is False, f"{path.name}: synthetic files never go here"
    doc = DOCS_BY_NAME[recording["doc"]]
    reasons = stale_reasons(recording, build_requests(doc, SETTINGS))
    assert reasons == [], f"{path.name} is stale, re-record it: {reasons}"
    _check(doc, recording)
    _build_proposal(doc, replay(recording))
    if doc.name in SCRUBBED_DOCUMENTS:
        # D103, D129 on the model's real answer: no invented identifier in a stored value or
        # quote.
        stored = _stored(replay(recording))
        for fragment in SCRUBBED_DOCUMENTS[doc.name]:
            assert fragment not in stored, f"{path.name}: {fragment!r} stored"


PAY_DOCUMENTS = ("lohnsteuerbescheinigung", "gehaltsabrechnung")
# The documents whose values and quotes are scrubbed, with the identifiers printed on them.
# The general reading's two documents with a person's data on them (D131, decided 10 Nov
# 2026): the owners' Steuernummer and home address on a Vorauszahlungsbescheid, and the
# applicant's current address on a tenant's Selbstauskunft (the flat's address stays).
GENERAL_IDENTIFIERS = {
    "vorauszahlungsbescheid": ("045/815/08154", "Beispielallee", "34117", "Kassel"),
    "mieterselbstauskunft": ("Ahornweg", "50667"),
}
SCRUBBED_DOCUMENTS = (
    {name: FAKE_IDENTIFIERS for name in PAY_DOCUMENTS}
    | {"einkommensteuerbescheid": BESCHEID_IDENTIFIERS}
    | GENERAL_IDENTIFIERS
)


def _stored(result) -> str:
    """The replayed values and their quotes as the job stores them, as one JSON text."""
    return json.dumps(
        {k: v.model_dump(mode="json") for k, v in result.values.items()}, ensure_ascii=False
    )


# --- the harness, on synthetic recordings ---------------------------------------------


def _as_written(doc: Doc) -> dict[str, str]:
    """Each expected field as the document text writes it (the line fragment that
    normalizes to the expected value), found by trying the text's own tokens."""
    model = EXTRACTION_MODELS[ExtractionKind(doc.kind)]
    found: dict[str, str] = {}
    lines = [line for page in doc.pages for line in page]
    for name, expected in doc.expected.items():
        kind = value_kind(model, name)
        found[name] = _written(lines, expected, kind)
    for name in doc.expected_missing:
        found[name] = "unbekannt"
    return found


def _written(lines: list[str], expected, kind: str) -> str:
    if kind in ("text", "text_list"):
        return "; ".join(expected) if kind == "text_list" else expected
    for line in lines:
        words = line.split()
        for size in range(1, 7):
            for start in range(len(words) - size + 1):
                candidate = " ".join(words[start : start + size]).rstrip(",.:")
                try:
                    if normalize_value(candidate, kind) == expected and candidate in line:
                        return candidate
                except ValueError:
                    continue
    raise AssertionError(f"no text for {expected!r} ({kind})")


@pytest.mark.parametrize("doc", DOCS, ids=[doc.name for doc in DOCS])
def test_harness_replays_a_synthetic_recording(doc):
    requests = build_requests(doc, SETTINGS)
    recording = synthetic_recording(doc, requests, _as_written(doc))
    assert recording["synthetic"] is True
    assert stale_reasons(recording, requests) == []
    _check(doc, recording)


@pytest.mark.parametrize("doc", DOCS, ids=[doc.name for doc in DOCS])
def test_build_proposal_accepts_the_replayed_values(doc):
    """rules.prefill (the proposals chunk) on the replayed values; skipped until it exists."""
    requests = build_requests(doc, SETTINGS)
    _build_proposal(doc, replay(synthetic_recording(doc, requests, _as_written(doc))))


@pytest.mark.parametrize("name", PAY_DOCUMENTS)
def test_pay_documents_store_no_identifier(name):
    """Phase 4b §8.3, D103: the invented identifiers on the two pay documents never reach the
    values or quotes the job stores. Each value here cites its whole page, so every
    identifier on it passes through the scrub; the expected figures still check out."""
    doc = DOCS_BY_NAME[name]
    printed = _printed(doc, FAKE_IDENTIFIERS)
    # both print the IdNr, the birth date, the Personalnummer and the address
    assert {"345 678", "14.03.1988", "731942", "Musterweg", "60311"} <= set(printed)
    _assert_scrubbed(doc, printed)


def test_bescheid_stores_no_identifier():
    """Phase 4b D129: the Einkommensteuerbescheid's invented Steuernummer (both forms), the two
    Identifikationsnummern, the address and the IBAN never reach the values or quotes the
    job stores, while the year and the zvE still check out."""
    doc = DOCS_BY_NAME["einkommensteuerbescheid"]
    printed = _printed(doc, BESCHEID_IDENTIFIERS)
    assert set(printed) == set(BESCHEID_IDENTIFIERS)  # every one is on the page
    _assert_scrubbed(doc, printed)


@pytest.mark.parametrize("name", sorted(GENERAL_IDENTIFIERS))
def test_the_general_reading_stores_no_identifier_or_home_address(name):
    """D131 (decided 10 Nov 2026): the Steuernummer and the owners' home address on a
    Vorauszahlungsbescheid, and the applicant's address on a Selbstauskunft, never reach the
    values or quotes the job stores, even with every value citing its whole page; the flat's
    address, which the reading links by, and the document's date still check out."""
    doc = DOCS_BY_NAME[name]
    printed = _printed(doc, GENERAL_IDENTIFIERS[name])
    assert set(printed) == set(GENERAL_IDENTIFIERS[name])  # every one is on the page
    _assert_scrubbed(doc, printed)


def _printed(doc: Doc, fragments: tuple[str, ...]) -> list[str]:
    text = "\n".join(line for page in doc.pages for line in page)
    return [fragment for fragment in fragments if fragment in text]


def _assert_scrubbed(doc: Doc, printed: list[str]) -> None:
    """Each value here cites its whole page, so every identifier on it passes through the
    scrub; the expected figures must still check out."""
    requests = build_requests(doc, SETTINGS)
    recording = synthetic_recording(doc, requests, _as_written(doc))
    for block in recording["extract"][0]["message"]["content"]:
        for citation in block["citations"] or []:
            page = doc.pages[citation["start_page_number"] - 1]
            citation["cited_text"] = "\n".join(page)
    stored = _stored(replay(recording))
    for fragment in printed:
        assert fragment not in stored, fragment
    _check(doc, recording)


def test_stale_recording_is_detected():
    doc = DOCS_BY_NAME["rechnung_notar"]
    requests = build_requests(doc, SETTINGS)
    recording = synthetic_recording(doc, requests, _as_written(doc))
    recording["extract"][0]["prompt_version"] = "0" * 16
    recording["classify"]["request_sha256"] = "0" * 64
    assert stale_reasons(recording, requests) == [
        "classify: request changed (document, model or settings)",
        "extract[0]: prompt changed",
    ]


# --- the documents ----------------------------------------------------------------------


def test_documents_are_deterministic_and_readable():
    for doc in DOCS:
        data = doc_pdf(doc)
        assert data == doc_pdf(doc)
        reader = PdfReader(io.BytesIO(data))
        assert len(reader.pages) == len(doc.pages)
        assert doc.pages[0][0] in reader.pages[0].extract_text()
    assert build_pdf([["a"]]) != build_pdf([["b"]])


def test_a_wrong_value_fails_the_check():
    doc = DOCS_BY_NAME["rechnung_notar"]
    requests = build_requests(doc, SETTINGS)
    written = _as_written(doc) | {"amount_total": "1.100,00"}  # the net, not the total
    with pytest.raises(AssertionError, match="amount_total"):
        _check(doc, synthetic_recording(doc, requests, written))


def test_recorder_needs_the_key(monkeypatch, capsys):
    import record

    monkeypatch.delenv("HAUSBUCH_ANTHROPIC_API_KEY", raising=False)
    assert record.main(["record.py"]) == 2
    assert "not set" in capsys.readouterr().err


def test_expected_values_are_valid_field_values():
    for doc in DOCS:
        model = EXTRACTION_MODELS[ExtractionKind(doc.kind)]
        assert set(doc.expected) <= set(model.model_fields), doc.name
        assert set(doc.expected_missing) <= set(model.model_fields), doc.name
        for name, value in doc.expected.items():
            if value_kind(model, name) == "iban":
                assert normalize_iban(value) == value


def test_verwaltervertrag_betrag_is_the_fee_with_vat():
    """The recorded reading's net fee (30,00 EUR zzgl. MwSt.) becomes Betrag 35.70, computed
    by code after the merge (Luis, 10 Nov 2026)."""
    path = RECORDINGS_DIR / "verwaltervertrag.json"
    result = replay(json.loads(path.read_text()))
    assert result.values["fee_net"].value == "30.00"
    assert result.values["amount_total"].value == "35.70"
    assert result.values["amount_total"].derived_from == ["fee_net", "monthly_fee"]
