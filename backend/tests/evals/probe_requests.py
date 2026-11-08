"""Send one request of each kind the app makes to the real API, with synthetic inputs (D50).

The fake model client in CI accepts any request body, so a body the real API refuses (as it
refused categorize's thinking {"type": "disabled"} on Sonnet 5.5 until 8 Nov 2026, D50) is
only found in production. This script builds each request with the app's own builders and
sends it, so such a refusal shows up on the operator's computer before a deploy:

    cd backend
    uv run --env-file ~/.hausbuch-evals.env python tests/evals/probe_requests.py

Four small calls (categorize on the normal and on the hard model, explain, classify of one
synthetic PDF from make_docs.py), about $0.02. Per request it prints the model, the HTTP
status of a refusal or the stop_reason, the output tokens, and whether the app's parser
accepts the answer. Exit code 0 only when every request was answered and parsed.

The key is read from HAUSBUCH_ANTHROPIC_API_KEY (the `hausbuch-evals` key) and goes only into
the SDK client; it is never printed. Every input is synthetic: no real line, document or name
is sent. Not run in CI.
"""

import os
import sys
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic  # noqa: E402
from make_docs import DOCS, doc_pdf  # noqa: E402
from recordings import eval_settings  # noqa: E402

from hausbuch.ai import documents  # noqa: E402
from hausbuch.ai.client import ModelRequest, message_to_response  # noqa: E402
from hausbuch.ai.requests import (  # noqa: E402
    categorize_request,
    explain_request,
    parse_classification,
    parse_explain,
    parse_match,
)
from hausbuch.evals.reading import classify_request_for  # noqa: E402
from hausbuch.jobs.matching import _payload  # noqa: E402
from hausbuch.rules.allocations import check_sum  # noqa: E402
from hausbuch.rules.errors import RuleError  # noqa: E402
from hausbuch.schemas.allocation import AllocationDraft  # noqa: E402
from hausbuch.services.matching import CategorizeInput, SimilarAnswer  # noqa: E402
from hausbuch.settings import DEFAULT_AI_INFERENCE_GEO  # noqa: E402

AMOUNT = Decimal("-312.40")


def categorize_data() -> dict[str, Any]:
    """A synthetic insurance debit, in the shape the match job sends (jobs.matching._payload
    over services.matching.CategorizeInput)."""
    line = CategorizeInput(
        transaction_id=None,  # type: ignore[arg-type]  # dropped by _payload
        booking_date=date(2026, 3, 3),
        value_date=date(2026, 3, 3),
        amount=AMOUNT,
        counterparty_name="Beispiel Versicherung AG",
        purpose="Wohngebaeudeversicherung Beitrag 2026 Vertrag 4711",
        booking_text="Lastschrift",
        iban_last4="1234",
        properties=["Wohnung Musterstraße"],
        tenancies=["Mietverhältnis WE 1: Kaltmiete 700.00 €, Nebenkostenvorauszahlung 150.00 €"],
        loans=["Darlehen Beispielbank: Rate 1200.00 €"],
        similar=[SimilarAnswer(date(2025, 3, 3), Decimal("-298.10"), ["insurance"])],
    )
    return _payload(line)


def explain_data() -> dict[str, Any]:
    """A rent matcher's set bound for review, in the shape jobs.matching._explain sends."""
    return {
        "buchung": {"betrag": "830.00"},
        "zuordnung": [
            {"kategorie": "rent_cold", "betrag": "680.00", "zeitraum": "2026-03-01 bis 2026-03-31"},
            {
                "kategorie": "operating_costs_prepayment",
                "betrag": "150.00",
                "zeitraum": "2026-03-01 bis 2026-03-31",
            },
        ],
        "methode": "matcher_rent",
        "vorlage_begruendung": "Die Zahlung kommt vom Konto des Mieters, ist aber 20,00 € "
        "niedriger als die Miete.",
        "vorlage_frage": "Mieter hat 830,00 € statt 850,00 € überwiesen. Mietminderung oder "
        "Teilzahlung?",
    }


def _check_match(text: str) -> str:
    out = parse_match(text)
    drafts = [AllocationDraft(amount=p.amount, category=p.category) for p in out.allocations]
    try:
        check_sum(AMOUNT, drafts)
    except RuleError:
        return f"parses ({out.confidence}), but the parts do not sum to {AMOUNT}"
    categories = ", ".join(p.category.value for p in out.allocations)
    return f"parses ({categories}, {out.confidence})"


def _check_explain(text: str) -> str:
    parse_explain(text)
    return "parses"


def _check_classify(text: str) -> str:
    out = parse_classification(text)
    return f"parses ({out.type.value}, {out.confidence})"


def _send(
    client: anthropic.Anthropic,
    label: str,
    request: ModelRequest,
    check: Callable[[str], str],
) -> bool:
    try:
        message = client.messages.create(**request.body())
    except anthropic.APIStatusError as exc:
        error = exc.body.get("error", {}) if isinstance(exc.body, dict) else {}
        detail = error.get("message", "") if isinstance(error, dict) else ""
        print(f"{label}: {request.model}, HTTP {exc.status_code} REFUSED  {detail}")
        return False
    except anthropic.APIConnectionError as exc:
        print(f"{label}: {request.model}, no connection ({type(exc).__name__})")
        return False
    response = message_to_response(message, 0)
    head = (
        f"{label}: {message.model}, stop {message.stop_reason}, {message.usage.output_tokens} out"
    )
    if message.stop_reason in ("refusal", "max_tokens"):
        print(f"{head}, NOT USABLE (the job would ask once more)")
        return False
    try:
        verdict = check(response.text)
    except ValueError as exc:  # includes pydantic's ValidationError and JSON errors
        print(f"{head}, DOES NOT PARSE ({type(exc).__name__})")
        return False
    print(f"{head}, {verdict}")
    return True


def main() -> int:
    key = os.environ.get("HAUSBUCH_ANTHROPIC_API_KEY")
    if not key:
        print("HAUSBUCH_ANTHROPIC_API_KEY is not set (use the hausbuch-evals key)", file=sys.stderr)
        return 2
    geo = os.environ.get("HAUSBUCH_AI_INFERENCE_GEO", DEFAULT_AI_INFERENCE_GEO)
    settings = eval_settings(ai_inference_geo=geo)
    client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=float(settings.ai_timeout_s))
    data = categorize_data()
    pdf = doc_pdf(DOCS[0])
    probes = [
        ("categorize", categorize_request(settings, data, hard=False), _check_match),
        ("categorize (hard)", categorize_request(settings, data, hard=True), _check_match),
        ("explain", explain_request(settings, explain_data()), _check_explain),
        ("classify", classify_request_for(settings, pdf, documents.PDF), _check_classify),
    ]
    results = [_send(client, label, request, check) for label, request, check in probes]
    failed = results.count(False)
    print("all requests answered and parsed" if not failed else f"{failed} request(s) failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
