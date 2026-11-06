"""A scripted ModelClient for tests and the recorded-eval replay: no network, no key.

Each send() returns (or raises) the next scripted item and keeps the request, so a test can
assert what would have been sent. Responses are built by the helpers below; anything a test
builds is synthetic and says so by construction, never a recording of the real API.
"""

from collections.abc import Iterable

from hausbuch.schemas.ai import CitedTextBlock, PageCitation

from .client import ModelCallError, ModelRequest, ModelResponse


class FakeModelClient:
    def __init__(self, script: Iterable[ModelResponse | ModelCallError] = ()) -> None:
        self.script = list(script)
        self.requests: list[ModelRequest] = []

    def send(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("FakeModelClient: no scripted response left")
        item = self.script.pop(0)
        if isinstance(item, ModelCallError):
            raise item
        return item


def text_response(
    text: str,
    *,
    model: str = "fake-model",
    stop_reason: str = "end_turn",
    inference_geo: str | None = "us",
) -> ModelResponse:
    """A response with one uncited text block (a classify answer, or a failed extract)."""
    return ModelResponse(
        model=model,
        blocks=[CitedTextBlock(text=text)],
        stop_reason=stop_reason,
        input_tokens=100,
        output_tokens=20,
        cache_read_tokens=0,
        inference_geo=inference_geo,
        duration_ms=5,
    )


def cited_response(
    fields: dict[str, tuple[str, int, str] | str],
    *,
    model: str = "fake-model",
    stop_reason: str = "end_turn",
) -> ModelResponse:
    """An extract response in the shape the API returns with citations: per field, an
    uncited `key: ` block, then the value in a block citing (page, quote); a plain string
    value is written uncited (e.g. `unbekannt`)."""
    blocks: list[CitedTextBlock] = []
    for index, (key, spec) in enumerate(fields.items()):
        prefix = ("\n" if index else "") + f"{key}: "
        if isinstance(spec, str):
            blocks.append(CitedTextBlock(text=prefix + spec))
            continue
        value, page, quote = spec
        blocks.append(CitedTextBlock(text=prefix))
        citation = PageCitation(cited_text=quote, start_page_number=page, end_page_number=page + 1)
        blocks.append(CitedTextBlock(text=value, citations=[citation]))
    return ModelResponse(
        model=model,
        blocks=blocks,
        stop_reason=stop_reason,
        input_tokens=2000,
        output_tokens=200,
        cache_read_tokens=0,
        inference_geo="us",
        duration_ms=7,
    )
