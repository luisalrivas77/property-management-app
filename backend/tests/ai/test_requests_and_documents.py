"""Prompts, the classify and extract requests, the classify parser, and PDF handling."""

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from hausbuch.ai import documents
from hausbuch.ai.requests import (
    CLASSIFY_MAX_TOKENS,
    classify_request,
    classify_schema,
    extract_request,
    field_list,
    load_prompt,
    parse_classification,
)
from hausbuch.schemas.ai import AiJob
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS
from hausbuch.settings import Settings

KEY = "sk-ant-api03-SECRET-test-key"


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://u:p@localhost/db",
        "iban_hmac_key": bytes(range(32)),
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": Path("/nonexistent"),
        "anthropic_api_key": KEY,
    } | overrides
    return Settings(**values)


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _text_pdf(text: str = "Rechnung Nr. 42") -> bytes:
    """A one-page PDF with a text layer (Helvetica), as a digital invoice has."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>stream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def test_prompt_version_is_the_file_hash():
    prompt = load_prompt("classify")
    assert len(prompt.version) == 16
    assert prompt.version != load_prompt("extract").version
    assert "instructions" in prompt.text.lower()


def test_classify_request_shape_and_no_key():
    block = documents.document_block(_pdf(1), documents.PDF, citations=False)
    request = classify_request(_settings(), block)
    body = request.body()
    assert request.job is AiJob.CLASSIFY
    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == CLASSIFY_MAX_TOKENS == 300
    assert body["inference_geo"] == "us"
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"]["format"]["schema"] == classify_schema()
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "document" and "citations" not in content[0]
    assert KEY not in json.dumps(body)


def test_extract_request_has_citations_and_no_structured_output():
    block = documents.document_block(_text_pdf(), documents.PDF, citations=True)
    request = extract_request(_settings(), ExtractionKind.INVOICE, block)
    body = request.body()
    assert request.job is AiJob.EXTRACT
    assert body["model"] == "claude-sonnet-5-5"
    assert "format" not in body.get("output_config", {})
    assert "thinking" not in body
    assert body["messages"][0]["content"][0]["citations"] == {"enabled": True}
    assert KEY not in json.dumps(body)


@pytest.mark.parametrize("kind", list(EXTRACTION_MODELS))
def test_field_list_names_every_field(kind):
    text = field_list(kind)
    keys = [line.split(":", 1)[0] for line in text.splitlines()[1:]]
    assert keys == list(EXTRACTION_MODELS[kind].model_fields)
    assert "D5" not in text  # internal notes do not reach the prompt


def test_classify_schema_is_strict_json_schema():
    schema = classify_schema()
    assert schema["type"] == "object"
    assert schema.get("additionalProperties") is False
    assert set(schema["required"]) == {
        "type",
        "subtype",
        "contains_tenant_data",
        "language",
        "confidence",
    }


def test_parse_classification_accepts_case_and_null_subtype():
    out = parse_classification(
        json.dumps(
            {
                "type": "Contract",
                "subtype": "LEASE",
                "contains_tenant_data": True,
                "language": "DE",
                "confidence": "High",
            }
        )
    )
    assert out.type is DocumentType.CONTRACT
    assert out.subtype is DocumentSubtype.LEASE
    assert out.language == "de"
    none = parse_classification(
        '{"type":"invoice","subtype":"null","contains_tenant_data":false,'
        '"language":"de","confidence":"low"}'
    )
    assert none.subtype is None


@pytest.mark.parametrize(
    "text",
    ["not json", "[1, 2]", '{"type": "poem"}', '{"type":"invoice"}'],
)
def test_parse_classification_rejects(text):
    with pytest.raises(ValueError):
        parse_classification(text)


def test_media_types():
    documents.check_media_type("application/pdf")
    documents.check_media_type("image/jpeg")
    with pytest.raises(documents.DocumentInputError) as info:
        documents.check_media_type("text/plain")
    assert info.value.code == documents.UNSUPPORTED_MEDIA_TYPE


def test_page_count_and_split():
    data = _pdf(7)
    assert documents.page_count(data) == 7
    assert documents.chunks(7, 3) == [(0, 3), (3, 6), (6, 7)]
    assert documents.chunks(3, 3) == [(0, 3)]
    assert documents.pdf_pages(data, 0, 7) is data
    assert len(PdfReader(io.BytesIO(documents.pdf_pages(data, 3, 6))).pages) == 3


def test_unreadable_and_too_long_pdfs():
    with pytest.raises(documents.DocumentInputError) as info:
        documents.page_count(b"%PDF-1.7 garbage")
    assert info.value.code == documents.UNREADABLE
    with pytest.raises(documents.DocumentInputError) as info:
        documents.page_count(_pdf(documents.MAX_TOTAL_PAGES + 1))
    assert info.value.code == documents.TOO_MANY_PAGES


def test_a_pdf_without_a_text_layer_is_sent_without_citations():
    """Photos combined into a PDF (D30) or a scan: nothing to cite, sent as a photo is."""
    assert documents.has_text_layer(_text_pdf())
    assert not documents.has_text_layer(_pdf(2))
    block = documents.document_block(_pdf(2), documents.PDF, citations=True)
    assert block["type"] == "document" and "citations" not in block
    assert not documents.has_text_layer(b"not a pdf")


def test_image_block_has_no_citations():
    block = documents.document_block(b"\x89PNG", "image/png", citations=True)
    assert block["type"] == "image"
    assert "citations" not in block
