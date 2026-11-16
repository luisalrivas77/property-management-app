"""The model client of one workspace (docs/phases/phase-6.md D260).

The Anthropic API key may be typed under Einstellungen › Zugangsschlüssel; a typed key wins
over HAUSBUCH_ANTHROPIC_API_KEY. A job resolves the key of its workspace when it runs (never
from its payload) and gets a client built for that key. Clients are cached by the SHA-256 of
their key, so a key is never a dict key or compared in clear and one client serves every job
with the same key. A typed key that no longer decrypts gives no client, never the
container's: the job then ends at gate 1 (ai.disabled), like a server without a key.
"""

import hashlib
import threading
from collections.abc import Callable

from sqlalchemy import Connection

from hausbuch.schemas.app_secrets import AppSecretName
from hausbuch.services import app_secrets
from hausbuch.settings import Settings

from .client import AnthropicModelClient, ModelClient

MAX_CLIENTS = 8  # a replaced key leaves its client behind; the cache starts over past this


class WorkspaceModelClients:
    def __init__(
        self, settings: Settings, factory: Callable[[str], ModelClient] | None = None
    ) -> None:
        timeout_s = settings.ai_timeout_s
        self._factory = factory or (lambda key: AnthropicModelClient(key, timeout_s=timeout_s))
        self._lock = threading.Lock()
        self._by_digest: dict[str, ModelClient] = {}

    def __repr__(self) -> str:
        return "WorkspaceModelClients()"

    def client(self, conn: Connection, settings: Settings) -> ModelClient | None:
        """The client for the key `conn`'s workspace resolves to, or None when the AI layer is
        off, no key is set, or the typed key cannot be read."""
        if not settings.ai_enabled:
            return None
        resolved = app_secrets.workspace_settings(
            conn, settings, [AppSecretName.ANTHROPIC_API_KEY], unreadable="unset"
        )
        key = resolved.anthropic_api_key
        if not key:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        with self._lock:
            found = self._by_digest.get(digest)
            if found is None:
                if len(self._by_digest) >= MAX_CLIENTS:
                    self._by_digest.clear()
                found = self._by_digest[digest] = self._factory(key)
        return found
