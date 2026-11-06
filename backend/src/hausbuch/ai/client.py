"""The model client: one request in, one response out (phase-2.md §5, §11.2).

ModelClient is the seam an EU-hosted route (Claude on AWS Bedrock or Google Vertex, §11.2
option 2) would implement; this phase has one implementation, AnthropicModelClient, on the
official `anthropic` SDK. Everything above the client (prompts, gating, parsing, provenance)
is shared, so swapping the route changes this file only.

A request is the Messages API body itself (ModelRequest.body()), so its SHA-256 is what
ai_call.input_sha256 stores. The API key travels in a header, never in the body: the hash, the
ai_call row and every log line are free of it by construction. Errors are mapped to
ModelCallError carrying only the status, a retryable flag and the request id; the SDK's own
exception (whose message quotes the response body) is never chained, logged or stored.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from hausbuch.schemas.ai import AiJob, CitedTextBlock, PageCitation

# Error codes this layer produces (the job adds the gate and parse codes). Stable API
# contract, like rules.errors codes; the frontend maps them to German text.
API_ERROR = "ai.api_error"  # a 4xx the API will answer the same way again (not 429)
UNAVAILABLE = "ai.unavailable"  # 429 / 5xx / 529 / network, and the retries ran out


@dataclass(frozen=True)
class ModelRequest:
    """One Messages API request, plus what provenance needs to know about it."""

    job: AiJob
    model: str
    prompt_version: str
    system: str
    content: list[dict[str, Any]]  # the user turn's content blocks (document first)
    max_tokens: int
    inference_geo: str | None = None
    thinking: dict[str, Any] | None = None
    effort: str | None = None
    output_schema: dict[str, Any] | None = None  # JSON schema for structured output

    def body(self) -> dict[str, Any]:
        """The request body as the Messages API takes it (keyword arguments of
        messages.create). Never holds the API key."""
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system,
            "messages": [{"role": "user", "content": self.content}],
        }
        output_config: dict[str, Any] = {}
        if self.effort is not None:
            output_config["effort"] = self.effort
        if self.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": self.output_schema}
        if output_config:
            body["output_config"] = output_config
        if self.thinking is not None:
            body["thinking"] = self.thinking
        if self.inference_geo is not None:
            body["inference_geo"] = self.inference_geo
        return body

    def sha256(self) -> bytes:
        """SHA-256 of the canonical JSON of body() (sorted keys, compact): ai_call.input_sha256.
        Equal requests hash equal, so replays and evals can compare them."""
        canonical = json.dumps(
            self.body(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).digest()


@dataclass(frozen=True)
class ModelResponse:
    """What the job needs from a response: the text blocks with their page citations, why it
    stopped, and the usage numbers ai_call records."""

    model: str
    blocks: list[CitedTextBlock]
    stop_reason: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    inference_geo: str | None = None
    duration_ms: int = 0
    request_id: str | None = field(default=None, compare=False)

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.blocks)


class ModelCallError(Exception):
    """The request did not produce a response. The message is fixed text: never the SDK's
    exception message, which quotes the response body."""

    def __init__(
        self,
        *,
        retryable: bool,
        status: int | None,
        request_id: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        kind = "retryable" if retryable else "terminal"
        super().__init__(f"model call failed ({kind}, status {status})")
        self.retryable = retryable
        self.status = status
        self.request_id = request_id
        self.duration_ms = duration_ms

    @property
    def error_code(self) -> str:
        return UNAVAILABLE if self.retryable else API_ERROR


class ModelClient(Protocol):
    """Sends one request. Implementations do not retry: the job's retry table (§5.1) is the
    only retry policy, so attempts and their provenance rows stay countable."""

    def send(self, request: ModelRequest) -> ModelResponse: ...


class AnthropicModelClient:
    """ModelClient on the first-party Claude API through the official SDK."""

    def __init__(self, api_key: str, *, timeout_s: float) -> None:
        if not api_key:
            raise ValueError("an API key is required")
        # max_retries=0: the SDK would otherwise retry 408/409/429/5xx itself, stacking a
        # second, invisible retry policy under §5.1's.
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

    def __repr__(self) -> str:  # never show the client (it holds the key)
        return "AnthropicModelClient()"

    def send(self, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        try:
            message = self._client.messages.create(**request.body())
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            # 429, and every 5xx including 529 overloaded.
            raise ModelCallError(
                retryable=True,
                status=exc.status_code,
                request_id=exc.request_id,
                duration_ms=_elapsed_ms(started),
            ) from None
        except anthropic.APIStatusError as exc:
            # Any other 4xx: the same request gets the same answer, so never retried.
            raise ModelCallError(
                retryable=False,
                status=exc.status_code,
                request_id=exc.request_id,
                duration_ms=_elapsed_ms(started),
            ) from None
        except anthropic.APIConnectionError:  # includes APITimeoutError
            raise ModelCallError(
                retryable=True, status=None, duration_ms=_elapsed_ms(started)
            ) from None
        return message_to_response(message, _elapsed_ms(started))


def message_to_response(message: Any, duration_ms: int) -> ModelResponse:
    blocks: list[CitedTextBlock] = []
    for block in message.content:
        if block.type != "text":
            continue  # thinking blocks carry no values
        citations = [
            PageCitation(
                cited_text=c.cited_text,
                document_index=c.document_index,
                document_title=c.document_title,
                start_page_number=c.start_page_number,
                end_page_number=c.end_page_number,
            )
            for c in (block.citations or [])
            if c.type == "page_location"
        ]
        blocks.append(CitedTextBlock(text=block.text, citations=citations))
    usage = message.usage
    return ModelResponse(
        model=message.model,
        blocks=blocks,
        stop_reason=message.stop_reason,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        inference_geo=usage.inference_geo,
        duration_ms=duration_ms,
        request_id=getattr(message, "_request_id", None),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))
