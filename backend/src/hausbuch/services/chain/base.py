"""The chain source protocol, its errors, its registry and the per-run budget
(docs/phases/phase-7.md D181, D182, D192).

ChainSource (schemas/chain.py) is read-only: `balances(address)` and
`transactions(address, cursor)`. Every source takes its base URL from Settings, so a provider
is swapped by one variable and nothing already read depends on it (D192: the stored
originals replay through `hausbuch replay-sync`). A source's failure is a ChainSourceError
carrying what may be logged: the HTTP status and the error code it maps to
(`wallet.source_throttled` on a 429, `wallet.source_refused` on a 401 or 403,
`wallet.key_missing` when a key the chain needs is unset), never the URL or a key.

**The source registry (step 2 core).** A source kind of the chain registry
(`ChainSourceKind`) is built by a factory

    SourceFactory = Callable[[ChainRegistryRow, Settings, RequestBudget], ChainSource]

found as `make_source` in `services/chain/<kind>.py` (`solana_rpc.py`, `evm_etherscan.py`,
`evm_rpc_balance.py`, `bitcoin_esplora.py`, one per family task), or registered with
`register_source` (a test's fake). A source:

- sets `kind` and `host`, the provider's host name only (`wallet_sync.source_host`,
  `^[a-z0-9.-]{1,253}$`), never its path, query or key;
- raises `ChainSourceError(WALLET_KEY_MISSING)` from its factory when a key or URL it needs
  is unset (read the settings through `setting`, which tolerates a field step 1 has not
  added yet), and wraps every `services.prices.http.SourceError` with `error_from`;
- calls `budget.take()` before every request and, when it says no, ends the page there with
  the cursor at the last transaction it returned (the sync then stops: `wallet.sync_deferred`);
- reads only up to the chain's tip minus `confirmations_final` (D182 (4)), and returns from
  `balances(address)` the balance *at that same height*, the one the movements are
  reconciled against (Esplora's tip balance backed out of the last blocks, `utxo.balance_at`);
  the sync never reads a balance of its own;
- may set `reconcile_scope = "account"` (default `"address"`): the sync then reconciles Σ
  the account's movements on the chain against Σ `balances(a)` over every active own address
  `a` of the account on that chain (Bitcoin: a spend's movements sit on its first input's
  address while its change lands on another own address, fixture `bitcoin_two_addresses`);
- may set `reads_tokens = True`: the sync then calls `balances(a, tokens=...)` with every
  non-spam token `a` has movements in (stored, or read in the same run), and the source reads
  those balances besides its own list (EVM over Etherscan, so a token the registry does not
  list is reconciled); a source without it is called with the address alone;
- returns `Page.next_cursor` = the opaque position to continue from (≤ 200 characters); the
  sync calls again with it until it is None or the cursor the sync passed in (the tip
  reached), so a page with no item but a moved cursor (an empty block window) continues;
- is built once per address run (`source_for` in the run's start): it may cache the chain's
  tip so the history and the balances of that run read to the same block;
- keeps each `ChainTx.raw` as the provider's record as read: the sync stores the run's raws
  as the NDJSON original (D182 (3)).
"""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr

from hausbuch.rules.chain.registry import ChainRegistryRow, registry
from hausbuch.rules.errors import (
    WALLET_KEY_MISSING,
    WALLET_SOURCE_FAILED,
    WALLET_SOURCE_REFUSED,
    WALLET_SOURCE_THROTTLED,
)
from hausbuch.schemas.chain import Chain, ChainSource, ChainSourceKind
from hausbuch.services.prices.http import SourceError
from hausbuch.settings import Settings


class ChainSourceError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code if status is None else f"{code} {status}")
        self.code = code
        self.status = status


def error_from(exc: SourceError) -> ChainSourceError:
    """D181, D182 (1): a 429 throttles, a 401 or 403 refuses, anything else fails. Only the
    status travels on; the URL never does."""
    if exc.status == 429:
        return ChainSourceError(WALLET_SOURCE_THROTTLED, 429)
    if exc.status in (401, 403):
        return ChainSourceError(WALLET_SOURCE_REFUSED, exc.status)
    return ChainSourceError(WALLET_SOURCE_FAILED, exc.status)


@dataclass(slots=True)
class RequestBudget:
    """D182 (1)'s per-run budget of one source kind, shared by every address of a run."""

    limit: int
    used: int = 0

    def take(self, n: int = 1) -> bool:
        if self.used + n > self.limit:
            self.used = self.limit
            return False
        self.used += n
        return True

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


def setting(settings: Settings, name: str) -> str | None:
    """A Settings field as plain text, or None when unset or not defined yet (step 1 adds the
    chain fields of D181 (e); this reads them defensively until it lands)."""
    value = getattr(settings, name, None)
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value or None


def host_of(url: str) -> str:
    """The host name of a URL, the only part of it a run records (D181)."""
    return (urlsplit(url).hostname or "unknown").lower()


def budget_limit(kind: ChainSourceKind, settings: Settings) -> int:
    """D182 (1): Solana RPS × 600 requests, Etherscan 2,000 calls, Esplora 600 requests; BSC
    one balance call per registry token, bounded here at 1,000."""
    if kind is ChainSourceKind.SOLANA_RPC:
        rps = setting(settings, "solana_rpc_rps")
        return int(rps or 4) * 600
    if kind is ChainSourceKind.EVM_ETHERSCAN:
        return 2000
    if kind is ChainSourceKind.BITCOIN_ESPLORA:
        return 600
    return 1000


ReconcileScope = Literal["address", "account"]


def reconcile_scope(source: ChainSource) -> ReconcileScope:
    """The source's declared reconcile scope (see the module docstring), address by default."""
    return "account" if getattr(source, "reconcile_scope", "address") == "account" else "address"


SourceFactory = Callable[[ChainRegistryRow, Settings, RequestBudget], ChainSource]

_REGISTERED: dict[ChainSourceKind, SourceFactory] = {}


def register_source(kind: ChainSourceKind, factory: SourceFactory | None) -> None:
    """Use `factory` for a source kind instead of `services/chain/<kind>.make_source` (a
    test's fake); None restores the module's."""
    if factory is None:
        _REGISTERED.pop(kind, None)
    else:
        _REGISTERED[kind] = factory


def factory_for(kind: ChainSourceKind) -> SourceFactory:
    if kind in _REGISTERED:
        return _REGISTERED[kind]
    try:
        module = import_module(f"hausbuch.services.chain.{kind.value}")
    except ModuleNotFoundError:
        raise NotImplementedError(f"services/chain: no source {kind} yet") from None
    found = getattr(module, "make_source", None)
    if found is None:
        raise NotImplementedError(f"services/chain: no make_source in {kind}")
    return found


def source_for(
    chain: Chain, settings: Settings, budget: RequestBudget | None = None
) -> ChainSource:
    """The configured source of a chain (the registry's `source` kind with the settings' base
    URL and key), or ChainSourceError `wallet.key_missing`."""
    row = registry().chain(chain)
    if budget is None:
        budget = RequestBudget(budget_limit(row.source, settings))
    return factory_for(row.source)(row, settings, budget)


__all__ = [
    "WALLET_KEY_MISSING",
    "WALLET_SOURCE_FAILED",
    "ChainSourceError",
    "ReconcileScope",
    "RequestBudget",
    "SourceFactory",
    "budget_limit",
    "error_from",
    "factory_for",
    "host_of",
    "reconcile_scope",
    "register_source",
    "setting",
    "source_for",
]
