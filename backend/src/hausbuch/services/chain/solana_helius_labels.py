"""Optional Helius labels for Solana transactions (docs/phases/phase-7.md D181 (d), D192); built
in step 2.

Helius's Enhanced Transactions API (`POST /v0/transactions?api-key=…` with up to 100
signatures) describes a transaction in a sentence („Transferred 0.5 SOL“). The sentence is
shown on the group's card as a `ProviderLabel` and nothing else: it never decides a kind, an
amount, a link or a confirmation, so removing the key changes no movement (D192). Any failure
gives no labels: the sync goes on without them and nothing is logged but the exception class.

The key travels in the query string, so the URL is never logged, put in an exception or a repr.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import SecretStr

from hausbuch.schemas.chain import ProviderLabel
from hausbuch.services.chain.solana_rpc import Post, urllib_post
from hausbuch.services.prices.http import MAX_BYTES, TIMEOUT_S, USER_AGENT

API_URL = "https://api.helius.xyz/v0/transactions"
BATCH = 100  # the API's largest request
MAX_TEXT = 500  # ProviderLabel.text


@dataclass
class HeliusLabels:
    key: SecretStr = field(repr=False)
    post: Post = field(default=urllib_post, repr=False)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)
    failures: list[str] = field(default_factory=list)  # exception classes, the only thing logged

    def labels(self, signatures: Sequence[str]) -> dict[str, ProviderLabel]:
        """The description of each signature Helius describes; nothing for the rest."""
        found: dict[str, ProviderLabel] = {}
        for start in range(0, len(signatures), BATCH):
            found |= self._batch(list(signatures[start : start + BATCH]))
        return found

    def _batch(self, signatures: list[str]) -> dict[str, ProviderLabel]:
        url = f"{API_URL}?api-key={self.key.get_secret_value()}"
        body = json.dumps({"transactions": signatures}).encode()
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        try:
            status, answer = self.post(url, headers, body, TIMEOUT_S, MAX_BYTES)
            data = json.loads(answer) if status == 200 else None
        except Exception as exc:  # noqa: BLE001 - optional: no label, the class name only
            self.failures.append(type(exc).__name__)
            return {}
        if status != 200:
            self.failures.append(f"HTTPError {status}")
            return {}
        if not isinstance(data, list):
            self.failures.append("parse")
            return {}
        wanted = set(signatures)
        at = self.now()
        found = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            sig, text = entry.get("signature"), entry.get("description")
            if not (isinstance(sig, str) and sig in wanted and isinstance(text, str)):
                continue
            if not text.strip():
                continue
            try:
                label = ProviderLabel(
                    provider="helius", text=text.strip()[:MAX_TEXT], fetched_at=at
                )
            except Exception as exc:  # noqa: BLE001 - one odd entry costs its label only
                self.failures.append(type(exc).__name__)
                continue
            found[sig] = label
        return found
