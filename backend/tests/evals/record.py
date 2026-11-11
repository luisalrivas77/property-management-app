"""Record real API responses for the synthetic eval documents (D29; needs a key, costs ~$0.10).

Run once per prompt or model change, from Luis's computer, with the `hausbuch-evals` key:

    cd backend
    HAUSBUCH_ANTHROPIC_API_KEY=sk-ant-... uv run python tests/evals/record.py [doc ...]

It builds each document's requests exactly as the document_read job does, sends them with the
official SDK (first-party API, inference_geo from HAUSBUCH_AI_INFERENCE_GEO, default us),
and writes tests/evals/recordings/<doc>.json with `"synthetic": false`. Commit the files:
CI then replays them on every push (test_recorded_evals.py). Only the synthetic documents of
make_docs.py are sent, never real ones. The key is read from the environment and goes only
into the SDK client; it is never written to a recording or printed.

After recording, run `uv run pytest tests/evals -v` and read the per-field results: a field
that comes back wrong is a prompt problem to fix (then record again), not an expectation to
edit.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic  # noqa: E402
from make_docs import DOCS, DOCS_BY_NAME  # noqa: E402
from recordings import FORMAT, RECORDINGS_DIR, build_requests, eval_settings, pretyped  # noqa: E402

from hausbuch.ai.client import ModelRequest  # noqa: E402
from hausbuch.settings import DEFAULT_AI_INFERENCE_GEO  # noqa: E402


def _send(client: anthropic.Anthropic, request: ModelRequest) -> dict:
    message = client.messages.create(**request.body())
    print(
        f"  {request.job.value}: {message.model}, stop {message.stop_reason}, "
        f"{message.usage.input_tokens} in / {message.usage.output_tokens} out, "
        f"geo {message.usage.inference_geo}"
    )
    return {
        "prompt_version": request.prompt_version,
        "request_sha256": request.sha256().hex(),
        "message": message.model_dump(mode="json"),
    }


def main(argv: list[str]) -> int:
    key = os.environ.get("HAUSBUCH_ANTHROPIC_API_KEY")
    if not key:
        print("HAUSBUCH_ANTHROPIC_API_KEY is not set (use the hausbuch-evals key)", file=sys.stderr)
        return 2
    names = argv[1:] or [doc.name for doc in DOCS]
    unknown = [name for name in names if name not in DOCS_BY_NAME]
    if unknown:
        print(f"unknown documents: {', '.join(unknown)}", file=sys.stderr)
        return 2
    geo = os.environ.get("HAUSBUCH_AI_INFERENCE_GEO", DEFAULT_AI_INFERENCE_GEO)
    settings = eval_settings(ai_inference_geo=geo)
    client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=float(settings.ai_timeout_s))
    RECORDINGS_DIR.mkdir(exist_ok=True)
    for name in names:
        doc = DOCS_BY_NAME[name]
        print(name)
        requests = build_requests(doc, settings)
        extract = []
        for offset, request in requests.extract:
            extract.append(_send(client, request) | {"page_offset": offset})
        recording = {
            "format": FORMAT,
            "doc": name,
            "synthetic": False,
            "recorded_on": datetime.now(UTC).date().isoformat(),
            "sdk_version": anthropic.__version__,
            # A document typed by code (a payment proof, D143) is never classified.
            "classify": None if requests.classify is None else _send(client, requests.classify),
            **({"pretyped": typed} if (typed := pretyped(doc)) is not None else {}),
            "extract": extract,
        }
        path = RECORDINGS_DIR / f"{name}.json"
        path.write_text(json.dumps(recording, ensure_ascii=False, indent=1) + "\n")
        print(f"  -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
