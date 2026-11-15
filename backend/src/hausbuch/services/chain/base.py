"""The chain source protocol and its errors (docs/phases/phase-7.md D181, D192).

ChainSource (schemas/chain.py) is read-only: `balances(address)` and
`transactions(address, cursor)`. Every source takes its base URL from Settings, so a provider
is swapped by one variable and nothing already read depends on it (D192: the stored
originals replay through `hausbuch replay-sync`). A source's failure is a ChainSourceError
carrying what may be logged: the HTTP status and the error code it maps to
(`wallet.source_throttled` on a 429, `wallet.source_refused` on a 401 or 403,
`wallet.key_missing` when a key the chain needs is unset), never the URL or a key.
"""

from hausbuch.schemas.chain import Chain, ChainSource


class ChainSourceError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code if status is None else f"{code} {status}")
        self.code = code
        self.status = status


def source_for(chain: Chain) -> ChainSource:
    """The configured source of a chain (the registry's `source` kind with the settings' base
    URL and key), or ChainSourceError `wallet.key_missing`. Built in steps 1 to 4."""
    raise NotImplementedError("phase 7 steps 1 to 4")
