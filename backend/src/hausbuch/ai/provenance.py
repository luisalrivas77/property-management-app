"""ai_call rows: one per model request, written before its result is used (D18, §5.3).

The row holds the model, the prompt version, the SHA-256 of the request body (never the key,
which is a header), the usage numbers and inference_geo from the response, the parsed output
and the raw text. It is immutable; tenant erasure may null output and raw_text (0005).
"""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, insert

from hausbuch.db.tables import ai_call

from .client import ModelCallError, ModelRequest, ModelResponse


def record_ai_call(
    conn: Connection,
    *,
    workspace_id: UUID,
    request: ModelRequest,
    document_id: UUID | None = None,
    transaction_id: UUID | None = None,
    response: ModelResponse | None = None,
    failure: ModelCallError | None = None,
    output: Any = None,
    error_code: str | None = None,
    keep_raw_text: bool = True,
) -> UUID:
    """Insert the row for one request (a response, or the failure that replaced it). A
    document job names its document_id, a matching job its transaction_id. `keep_raw_text`
    False stores a response without its text (the document job's proposal was erased while
    the call was out; the caller passes no output either)."""
    if (response is None) == (failure is None):
        raise ValueError("exactly one of response and failure")
    row: dict[str, Any] = {
        "workspace_id": workspace_id,
        "job": request.job.value,
        "document_id": document_id,
        "transaction_id": transaction_id,
        "model": request.model,
        "prompt_version": request.prompt_version,
        "input_sha256": request.sha256(),
        "output": output,
        "error_code": error_code if failure is None else (error_code or failure.error_code),
    }
    if response is not None:
        row |= {
            "model": response.model or request.model,
            "inference_geo": response.inference_geo,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cache_read_tokens": response.cache_read_tokens,
            "stop_reason": response.stop_reason,
            "raw_text": response.text if keep_raw_text else None,
            "duration_ms": response.duration_ms,
        }
    else:
        assert failure is not None
        row["duration_ms"] = failure.duration_ms
    return conn.execute(insert(ai_call).values(row).returning(ai_call.c.id)).scalar_one()
