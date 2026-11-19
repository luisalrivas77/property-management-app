"""Record the household plan's explanation for the synthetic fact sets (D258 layer 3; needs a
key, about 52 Haiku calls).

Run once per prompt or model change, from Luis's computer, with the `hausbuch-evals` key in an
env file that is never printed or sourced:

    cd backend
    uv run --env-file ~/.hausbuch-evals.env python tests/evals/record_explain.py \
        [honest|redteam] [case ...]

Each request is built exactly as the explain_plan job builds it
(ai/requests.explain_plan_request), from explain_cases.py's invented facts; a red-team case
replaces the system prompt with explain_cases.REDTEAM_SYSTEM and its task. Only the first
answer is recorded (the job's one retry after a rejection is not part of layer 3). Each
output is written raw to tests/evals/recordings/explain/<kind>__<case>__<mode>.json: no pass
or fail is stored, since test_recorded_explain.py runs the current validator over the
outputs on every push. The key is read from the environment by the SDK client only; it is
never written or printed.
"""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic  # noqa: E402
from explain_cases import (
    REDTEAM,
    REDTEAM_SITUATION,
    REDTEAM_SYSTEM,
    SITUATIONS,
    SITUATIONS_BY_NAME,
    facts,
)  # noqa: E402
from recordings import FORMAT, RECORDINGS_DIR, eval_settings  # noqa: E402

from hausbuch.ai.client import ModelRequest  # noqa: E402
from hausbuch.ai.requests import Prompt, explain_plan_prompt, explain_plan_request  # noqa: E402
from hausbuch.schemas.advice import AdviceMode, ExplainFacts  # noqa: E402
from hausbuch.settings import DEFAULT_AI_INFERENCE_GEO  # noqa: E402

EXPLAIN_DIR = RECORDINGS_DIR / "explain"


def _send(client: anthropic.Anthropic, request: ModelRequest) -> dict:
    message = client.messages.create(**request.body())
    text = "".join(b.text for b in message.content if b.type == "text")
    print(
        f"  {message.model}, stop {message.stop_reason}, "
        f"{message.usage.input_tokens} in / {message.usage.output_tokens} out"
    )
    return {
        "model": message.model,
        "stop_reason": message.stop_reason,
        "text": text,
        "prompt_version": request.prompt_version,
        "request_sha256": request.sha256().hex(),
    }


def _write(kind: str, case: str, f: ExplainFacts, sent: dict, override: str | None) -> None:
    recording = {
        "format": FORMAT,
        "kind": kind,
        "case": case,
        "mode": f.mode.value,
        "synthetic": False,
        "recorded_on": datetime.now(UTC).date().isoformat(),
        "sdk_version": anthropic.__version__,
        "override": override,
        "facts": f.model_dump(mode="json"),
        "labels": f.labels.model_dump(mode="json"),
        **sent,
    }
    path = EXPLAIN_DIR / f"{kind}__{case}__{f.mode.value}.json"
    path.write_text(json.dumps(recording, ensure_ascii=False, indent=1) + "\n")
    print(f"  -> {path.name}")


def main(argv: list[str]) -> int:
    if not os.environ.get("HAUSBUCH_ANTHROPIC_API_KEY"):
        print(
            "HAUSBUCH_ANTHROPIC_API_KEY is not set (use the hausbuch-evals env file)",
            file=sys.stderr,
        )
        return 2
    known = {"honest", "redteam"}
    kinds = [a for a in argv[1:] if a in known] or sorted(known)
    only = {a for a in argv[1:] if a not in known}  # case names, to record a few again
    if only - set(SITUATIONS_BY_NAME) - set(REDTEAM):
        print("usage: record_explain.py [honest|redteam] [case ...]", file=sys.stderr)
        return 2
    geo = os.environ.get("HAUSBUCH_AI_INFERENCE_GEO", DEFAULT_AI_INFERENCE_GEO)
    settings = eval_settings(ai_inference_geo=geo)
    client = anthropic.Anthropic(
        api_key=os.environ["HAUSBUCH_ANTHROPIC_API_KEY"],
        max_retries=2,
        timeout=float(settings.ai_timeout_s),
    )
    EXPLAIN_DIR.mkdir(parents=True, exist_ok=True)
    base = explain_plan_prompt()
    for mode in AdviceMode:
        if "honest" in kinds:
            for situation in SITUATIONS:
                if only and situation.name not in only:
                    continue
                print(f"honest {situation.name} {mode.value}")
                f = facts(situation, mode)
                _write(
                    "honest",
                    situation.name,
                    f,
                    _send(client, explain_plan_request(settings, f)),
                    None,
                )
        if "redteam" in kinds:
            for variant, override in REDTEAM.items():
                if only and variant not in only:
                    continue
                print(f"redteam {variant} {mode.value}")
                f = facts(SITUATIONS_BY_NAME[REDTEAM_SITUATION[variant]], mode)
                text = REDTEAM_SYSTEM.format(task=override)
                prompt = Prompt(
                    name=base.name,
                    text=text,
                    version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                )
                request = explain_plan_request(settings, f, prompt=prompt)
                _write("redteam", variant, f, _send(client, request), variant)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
