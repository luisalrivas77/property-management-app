"""The model client: request body and hash, SDK error mapping, response conversion.

No network: the SDK client inside AnthropicModelClient is replaced by a stub that raises real
SDK exception instances or returns a real SDK Message.
"""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from hausbuch.ai.client import (
    API_ERROR,
    UNAVAILABLE,
    AnthropicModelClient,
    ModelCallError,
    ModelRequest,
    cached,
)
from hausbuch.schemas.ai import AiJob

KEY = "sk-ant-api03-SECRET-test-key-do-not-leak"


def _request(**overrides) -> ModelRequest:
    values = dict(
        job=AiJob.CLASSIFY,
        model="claude-haiku-5-5",
        prompt_version="abc",
        system="system text",
        content=[{"type": "text", "text": "hello"}],
        max_tokens=300,
    )
    values.update(overrides)
    return ModelRequest(**values)


def _client_raising(exc: Exception) -> AnthropicModelClient:
    client = AnthropicModelClient(KEY, timeout_s=5)

    def create(**_kwargs):
        raise exc

    client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return client


def _status_error(cls, status: int):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(
        status, request=request, headers={"request-id": "req_123"}, json={"error": {}}
    )
    return cls(f"Error code: {status} - body quoting {KEY}", response=response, body=None)


def test_body_has_the_optional_parts_only_when_set():
    plain = _request().body()
    assert set(plain) == {"model", "max_tokens", "system", "messages"}
    assert plain["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]

    full = _request(
        inference_geo="us",
        thinking={"type": "disabled"},
        effort="low",
        output_schema={"type": "object"},
    ).body()
    assert full["inference_geo"] == "us"
    assert full["thinking"] == {"type": "disabled"}
    assert full["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }


def test_sha256_is_stable_and_follows_the_body():
    assert _request().sha256() == _request().sha256()
    assert len(_request().sha256()) == 32
    assert _request().sha256() != _request(max_tokens=301).sha256()
    # The prompt version and job are provenance, not part of what is sent.
    assert _request().sha256() == _request(prompt_version="other").sha256()


def test_client_repr_and_errors_never_show_the_key():
    client = AnthropicModelClient(KEY, timeout_s=5)
    assert KEY not in repr(client)
    assert "SECRET" not in repr(client)


def test_client_needs_a_key():
    with pytest.raises(ValueError):
        AnthropicModelClient("", timeout_s=5)


@pytest.mark.parametrize(
    ("cls", "status", "retryable", "code"),
    [
        (anthropic.RateLimitError, 429, True, UNAVAILABLE),
        (anthropic.InternalServerError, 500, True, UNAVAILABLE),
        (anthropic.OverloadedError, 529, True, UNAVAILABLE),
        (anthropic.BadRequestError, 400, False, API_ERROR),
        (anthropic.AuthenticationError, 401, False, API_ERROR),
        (anthropic.ContentTooLargeError, 413, False, API_ERROR),
    ],
)
def test_status_errors_map_to_model_call_error(cls, status, retryable, code):
    client = _client_raising(_status_error(cls, status))
    with pytest.raises(ModelCallError) as info:
        client.send(_request())
    exc = info.value
    assert exc.retryable is retryable
    assert exc.status == status
    assert exc.request_id == "req_123"
    assert exc.error_code == code
    # The SDK's message (which quotes the response body) is neither kept nor chained.
    assert KEY not in str(exc)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


def test_connection_errors_are_retryable():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for error in (
        anthropic.APIConnectionError(request=request),
        anthropic.APITimeoutError(request),
    ):
        with pytest.raises(ModelCallError) as info:
            _client_raising(error).send(_request())
        assert info.value.retryable is True
        assert info.value.status is None
        assert info.value.error_code == UNAVAILABLE


def test_response_keeps_text_blocks_and_page_citations():
    message = Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                {"type": "text", "text": "price: ", "citations": None},
                {
                    "type": "text",
                    "text": "325.000,00 EUR",
                    "citations": [
                        {
                            "type": "page_location",
                            "cited_text": "Kaufpreis 325.000,00 EUR",
                            "document_index": 0,
                            "document_title": None,
                            "start_page_number": 3,
                            "end_page_number": 4,
                        }
                    ],
                },
            ],
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 80,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "inference_geo": "us",
            },
        }
    )
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return message

    client = AnthropicModelClient(KEY, timeout_s=5)
    client._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    response = client.send(_request(inference_geo="us"))

    body = _request(inference_geo="us").body()
    assert seen == {
        **body,
        "system": [
            {"type": "text", "text": body["system"], "cache_control": {"type": "ephemeral"}}
        ],
    }
    assert response.text == "price: 325.000,00 EUR"
    assert len(response.blocks) == 2
    citation = response.blocks[1].citations[0]
    assert (citation.start_page_number, citation.cited_text) == (3, "Kaufpreis 325.000,00 EUR")
    assert response.inference_geo == "us"
    assert (response.input_tokens, response.output_tokens, response.cache_read_tokens) == (
        1200,
        80,
        0,
    )
    assert response.stop_reason == "end_turn"


def test_the_system_prompt_is_sent_cached_and_the_hash_ignores_the_marker():
    """D194: the system prompt goes out as one block marked for prompt caching; the stored
    input hash is still over body(), so recorded evals keep matching."""
    request = _request()
    sent = cached(request.body())
    assert sent["system"] == [
        {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
    ]
    assert request.body()["system"] == request.system
    assert {k: v for k, v in sent.items() if k != "system"} == {
        k: v for k, v in request.body().items() if k != "system"
    }
