"""Bearer tokens (session cookie, enrollment link): 32 random bytes, base64url, never stored.

The database holds only SHA-256 of the token's text, so a leaked table cannot be replayed.
"""

import hashlib
import re
import secrets

TOKEN_BYTES = 32
# secrets.token_urlsafe(32): 43 characters, base64url without padding.
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def new_token() -> tuple[str, bytes]:
    """A fresh token and the hash to store for it."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, token_hash(token)


def is_well_formed(token: str) -> bool:
    return isinstance(token, str) and _TOKEN.fullmatch(token) is not None


def token_hash(token: str) -> bytes:
    """SHA-256 of the token text (32 bytes, the token_hash columns' CHECK)."""
    return hashlib.sha256(token.encode("ascii")).digest()
