"""Phase 7 step 2: the optional Helius labels (docs/phases/phase-7.md D181 (d), D192): a label
per described signature, nothing on any failure, the key never outside the request, and the
group's kind unchanged by a label (solana_tx.yaml `helius_label_kind_unchanged`)."""

import json
from datetime import UTC, datetime

import fixture_models_phase7 as phase7
import pytest
from pydantic import SecretStr

from hausbuch.rules.chain import solana
from hausbuch.rules.chain.decoders import public_decoders
from hausbuch.services.chain.solana_helius_labels import HeliusLabels

KEY = "h3l1usK3yInv3nt3d"
AT = datetime(2026, 11, 15, 6, 45, tzinfo=UTC)
CASE = next(c for c in phase7.load("solana_tx").cases if c.id == "helius_label_kind_unchanged")
SIG = CASE.tx["transaction"]["signatures"][0]


def _labels(status=200, payload=None, error=None):
    sent = []

    def post(url, headers, body, timeout, max_bytes):
        sent.append((url, json.loads(body)))
        if error is not None:
            raise error
        return status, json.dumps(payload).encode()

    return HeliusLabels(key=SecretStr(KEY), post=post, now=lambda: AT), sent


def test_a_description_becomes_the_label_and_the_kind_stays():
    description = CASE.provider_label["description"]
    helius, sent = _labels(
        payload=[{"signature": SIG, "description": description, "type": "TRANSFER"}]
    )
    labels = helius.labels([SIG, "other"])
    assert list(labels) == [SIG] and labels[SIG].text == description
    assert labels[SIG].provider == "helius" and labels[SIG].fetched_at == AT
    assert sent[0][1] == {"transactions": [SIG, "other"]}
    assert sent[0][0].endswith(f"?api-key={KEY}")
    lookup = solana.rows_lookup(list(public_decoders()))
    plain = solana.classify_solana(CASE.tx, {CASE.own}, lookup)
    labelled = solana.classify_solana(CASE.tx, {CASE.own}, lookup, label=labels[SIG])
    assert (plain.kind, plain.legs, plain.card) == (labelled.kind, labelled.legs, labelled.card)
    assert labelled.label == labels[SIG]


@pytest.mark.parametrize(
    "helius_and_failure",
    [
        (dict(status=401), "HTTPError 401"),
        (dict(status=429), "HTTPError 429"),
        (dict(payload={"error": "bad"}), "parse"),
        (dict(error=OSError(f"https://api.helius.xyz/v0/transactions?api-key={KEY}")), "OSError"),
    ],
)
def test_any_failure_gives_no_label_and_logs_no_key(helius_and_failure):
    kwargs, failure = helius_and_failure
    helius, _ = _labels(**kwargs)
    assert helius.labels([SIG]) == {}
    assert helius.failures == [failure]
    assert KEY not in repr(helius)


def test_batches_of_a_hundred_and_empty_descriptions_dropped():
    helius, sent = _labels(payload=[{"signature": "s0", "description": "  "}])
    assert helius.labels([f"s{i}" for i in range(150)]) == {}
    assert [len(body["transactions"]) for _, body in sent] == [100, 50]


def test_a_malformed_entry_is_skipped_not_raised():
    helius, _ = _labels(
        payload=[
            {"signature": ["x"], "description": "d"},
            {"signature": {"a": 1}, "description": "d"},
            {"signature": SIG, "description": 7},
            "noise",
            {"signature": SIG, "description": "Transferred 1 SOL"},
        ]
    )
    assert {sig: label.text for sig, label in helius.labels([SIG]).items()} == {
        SIG: "Transferred 1 SOL"
    }
