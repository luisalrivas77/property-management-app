"""The OpenAPI contract the frontend's types are generated from (§5).

frontend/src/api/openapi.json is openapi_document(create_app(...)). The test fails when the
committed file is stale; regenerate it with

    HAUSBUCH_UPDATE_OPENAPI=1 uv run pytest tests/api/test_openapi.py

(the `hausbuch openapi` CLI, chunk 2, prints the same document). No database is needed.
"""

import os
from pathlib import Path

import pytest
from api_support import FakeResolver, make_settings
from sqlalchemy import create_engine

from hausbuch.api.app import API_PREFIX, create_app, openapi_document
from hausbuch.storage import LocalBlobStore

REPO = Path(__file__).resolve().parents[3]
OPENAPI_JSON = REPO / "frontend" / "src" / "api" / "openapi.json"


@pytest.fixture
def schema_app(tmp_path):
    engine = create_engine("postgresql+psycopg://")  # never connects
    yield create_app(
        make_settings(tmp_path), engine, LocalBlobStore(tmp_path / "blobs"), FakeResolver({})
    )
    engine.dispose()


def test_the_committed_openapi_document_is_current(schema_app):
    document = openapi_document(schema_app)
    if os.environ.get("HAUSBUCH_UPDATE_OPENAPI") == "1":
        OPENAPI_JSON.parent.mkdir(parents=True, exist_ok=True)
        OPENAPI_JSON.write_text(document, encoding="utf-8")
    assert OPENAPI_JSON.is_file(), f"{OPENAPI_JSON} is missing; see this module's docstring"
    assert OPENAPI_JSON.read_text(encoding="utf-8") == document, (
        "frontend/src/api/openapi.json is stale: regenerate it with "
        "HAUSBUCH_UPDATE_OPENAPI=1 uv run pytest tests/api/test_openapi.py"
    )


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_no_decimal_is_published_as_a_json_number(schema_app):
    """Spec amendment 2: money, rates, shares, areas and rooms are strings on the wire."""
    numbers = [n for n in _walk(schema_app.openapi()) if n.get("type") == "number"]
    assert numbers == []


def test_every_operation_documents_the_error_envelope(schema_app):
    document = schema_app.openapi()
    assert "HTTPValidationError" not in document["components"]["schemas"]
    for path, operations in document["paths"].items():
        assert path.startswith(f"{API_PREFIX}/"), path
        for method, operation in operations.items():
            default = operation["responses"]["default"]
            ref = default["content"]["application/json"]["schema"]["$ref"]
            assert ref == "#/components/schemas/ErrorResponse", (method, path)


def test_the_cli_prints_exactly_the_committed_document():
    """`hausbuch openapi` (chunk 2) and the committed file (chunk 1) must never differ, or the
    frontend's staleness check would flag a correct tree."""
    from hausbuch.cli import openapi_json

    committed = (Path(__file__).resolve().parents[3] / "frontend/src/api/openapi.json").read_text()
    assert openapi_json() == committed
