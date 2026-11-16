"""Etherscan API V2 as a ChainSource for Ethereum and Arbitrum (docs/phases/phase-7.md D181 (b),
D182 (1), (4), D186 (2), D192); step 3.

One base URL for every EVM chain, the chain picked by the `chainid` parameter (the registry's
`chain_id`), the key the `apikey` parameter. Read-only: the account module's five lists and
balance, and the proxy module's `eth_blockNumber` and `eth_call` (a token's `balanceOf`).

- `transactions(address, cursor)`: the tip by `eth_blockNumber`, the window of
  rules/chain/sync_window (from `cursor − 12`, to `tip − 12`), one end block pinned for all
  five lists; each list paged by block from its start (`sort=asc`, 1,000 rows a page): after
  a full page the next starts at the page's last block and that block's rows are read again
  whole, so no row is lost or doubled at a page edge; a page that is one block throughout
  moves by page number. The rows normalise through rules/chain/evm; `next_cursor` is the end
  block. The stored originals are the rows themselves (`ChainTx.raw`), so a replay needs no
  provider (D192).
- `balances(address, tokens=...)`: the native balance and the `balanceOf` of each registry
  token and of each token in `tokens` (the sync passes every non-spam token the address has
  movements in, stored or read in the same run, so a token the registry does not list,
  claimed or bought, is reconciled too; `reads_tokens = True` declares it), at `tip − 12`,
  the block the movements were read to (D186 (2)).

Paced at 2.5 calls a second (the free tier allows 3) with an injectable `sleep`; each call
first takes one request from the run's `RequestBudget` (services/chain/base, D182 (1)). When
the budget says no, `transactions` returns an empty page with the cursor it was given (a
window is read whole across the five lists or not at all, so a half-read window is never
returned) and `balances` stops where it is; the sync then ends the run deferred. A second
`transactions` call of the same run with the cursor its first call returned (the sync asks
again until the cursor stops moving) reads nothing: the window is already read to the pinned
end, and re-reading its overlap would only repeat rows.

`make_source(row, settings, budget)` is the factory services/chain/base finds: the key
`HAUSBUCH_ETHERSCAN_KEY` (unset → `wallet.key_missing` before any request, so the address
gets no `wallet_sync` row) and the base URL `HAUSBUCH_ETHERSCAN_URL`; `host` is that URL's
host name only.

Etherscan answers its errors with HTTP 200 and `status = "0"`: a rate limit is
`wallet.source_throttled`, a key it refuses (invalid, missing, a chain the plan does not
cover) `wallet.source_refused`; `status = "0"` with an empty list is no data. The key is
never in a log or an exception: a failure carries its code and HTTP status only
(services/chain/evm_http).
"""

import contextlib
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import JsonValue, SecretStr

from hausbuch.rules import errors as E
from hausbuch.rules.chain import evm
from hausbuch.rules.chain.registry import ChainRegistryRow, registry
from hausbuch.rules.chain.sync_window import window
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import AssetBalance, AssetKey, Chain, ChainSourceKind, ChainTx, Page
from hausbuch.services.chain.base import ChainSourceError, RequestBudget, host_of, setting
from hausbuch.services.chain.evm_http import SOURCE_FAILED, BudgetSpent, get_json
from hausbuch.services.prices.http import Fetch, urllib_fetch
from hausbuch.settings import Settings

DEFAULT_URL = "https://api.etherscan.io/v2/api"
MIN_INTERVAL_S = 0.4  # 2.5 calls a second
PAGE_ROWS = 1000
MAX_PAGES = 10  # Etherscan answers page × offset up to 10,000 rows
BALANCE_OF = "0x70a08231"  # balanceOf(address)

Row = Mapping[str, JsonValue]


class _InBand(ChainSourceError):
    """An answer Etherscan sent whole (HTTP 200) that holds no usable value: the proxy
    module's JSON-RPC error that is no rate limit or key refusal (a reverted `eth_call`), or a
    result that is no hex. Still `wallet.source_failed` wherever it is not caught."""


def _no_rows(message: str) -> bool:
    """Etherscan's words for an empty answer, the only `status "0"` that is no error: „No
    transactions found“, „No records found“ and their variants („No token transfers found“).
    A bare „NOTOK“ is never one."""
    text = message.strip().lower()
    return text.startswith("no ") and text.endswith(" found")


def _refusal(message: str) -> ChainSourceError:
    text = message.lower()
    if "rate limit" in text:
        return ChainSourceError(E.WALLET_SOURCE_THROTTLED)
    if "api key" in text or "apikey" in text or "not supported" in text or "access" in text:
        return ChainSourceError(E.WALLET_SOURCE_REFUSED)
    return ChainSourceError(SOURCE_FAILED)


@dataclass
class EtherscanSource:
    chain: Chain
    key: SecretStr | None
    base_url: str = DEFAULT_URL
    fetch: Fetch = field(default=urllib_fetch, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    budget: RequestBudget | None = field(default=None, repr=False)  # None: unbounded
    kind: ChainSourceKind = ChainSourceKind.EVM_ETHERSCAN
    # the sync passes `balances` the tokens the address has movements in (schemas/chain)
    reads_tokens: ClassVar[bool] = True
    # The tokens asked for by `tokens` (none of the registry's) whose `balanceOf` the last
    # `balances` call could not read: a reverted call or an answer with no 32-byte value (a
    # contract the classifier took for a token that is not one). The sync leaves them out
    # of the address's reconciliation and marks the run unreconciled with
    # `wallet.token_unreadable`; the rest of the run goes on.
    unreadable: set[AssetKey] = field(default_factory=set, repr=False)
    _calls: int = field(default=0, init=False, repr=False)
    _tip: int | None = field(default=None, init=False, repr=False)
    _read_to: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        row = registry().chain(self.chain)
        if row.chain_id is None:
            raise ValueError(f"{self.chain} has no EVM chain id")
        self._chain_id = row.chain_id
        self._final = row.confirmations_final

    @property
    def host(self) -> str:
        """The provider's host name, the only part of its URL a run records (D181)."""
        return host_of(self.base_url)

    # --- one call ----------------------------------------------------------------------

    def _call(self, params: Mapping[str, str | int]) -> JsonValue:
        if self.key is None or not self.key.get_secret_value():
            raise ChainSourceError(E.WALLET_KEY_MISSING)
        if self.budget is not None and not self.budget.take():
            raise BudgetSpent
        if self._calls:
            self.sleep(MIN_INTERVAL_S)
        self._calls += 1
        query = {"chainid": self._chain_id, **params, "apikey": self.key.get_secret_value()}
        answer = get_json(self.fetch, f"{self.base_url}?{urllib.parse.urlencode(query)}")
        if not isinstance(answer, dict):
            raise ChainSourceError(SOURCE_FAILED)
        if "error" in answer:  # the proxy module's JSON-RPC error
            error = answer["error"]
            message = error.get("message") if isinstance(error, dict) else error
            refused = _refusal(str(message or ""))
            if refused.code == SOURCE_FAILED:
                raise _InBand(SOURCE_FAILED)
            raise refused
        result = answer.get("result")
        if str(answer.get("status", "1")) == "0":
            message = str(answer.get("message") or "")
            if isinstance(result, list) and not result and _no_rows(message):
                return []  # "No transactions found": the window holds nothing
            text = result if isinstance(result, str) and result else message
            raise _refusal(text)  # NOTOK with [] is a refusal too: the cursor never advances
        if isinstance(result, str) and not result.startswith("0x") and "jsonrpc" in answer:
            raise _refusal(result)  # the proxy module's rate-limit text with no status
        return result

    def _hex(self, params: Mapping[str, str | int]) -> int:
        result = self._call(params)
        if not isinstance(result, str) or not result.startswith("0x"):
            raise _InBand(SOURCE_FAILED)
        try:
            return int(result, 16) if result != "0x" else 0
        except ValueError:
            raise _InBand(SOURCE_FAILED) from None

    def tip(self) -> int:
        """The chain's tip, read once per instance: the history, the balances and a deposit
        of one sync run are read to the same block (D186 (2)), so a sync builds one source
        per run."""
        if self._tip is None:
            self._tip = self._hex({"module": "proxy", "action": "eth_blockNumber"})
        return self._tip

    # --- history -----------------------------------------------------------------------

    def _rows(self, action: str, address: str, start: int, end: int) -> list[Row]:
        out: list[Row] = []
        page = 1
        while True:
            params = {
                "module": "account",
                "action": action,
                "address": address,
                "startblock": start,
                "endblock": end,
                "page": page,
                "offset": PAGE_ROWS,
                "sort": "asc",
            }
            rows = self._call(params)
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ChainSourceError(SOURCE_FAILED)
            if len(rows) < PAGE_ROWS:
                out.extend(rows)  # type: ignore[arg-type]
                return out
            last = int(str(rows[-1].get("blockNumber")))  # type: ignore[union-attr]
            if last > start:
                # the last block may continue on the next page: read it again whole
                out.extend(r for r in rows if int(str(r.get("blockNumber"))) < last)  # type: ignore[union-attr]
                start, page = last, 1
            else:  # a whole page inside one block
                out.extend(rows)  # type: ignore[arg-type]
                page += 1
                if page > MAX_PAGES:
                    raise ChainSourceError(SOURCE_FAILED)

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]:
        address = address.lower()
        if cursor is not None and cursor == self._read_to:
            return Page[ChainTx](items=[], next_cursor=cursor)  # read to the pinned end
        try:
            span = window(self.chain, int(cursor) if cursor else None, self.tip())
            if span is None:
                return Page[ChainTx](items=[], next_cursor=cursor)
            start, end = span
            records = {action: self._rows(action, address, start, end) for action in evm.ACTIONS}
        except BudgetSpent:
            return Page[ChainTx](items=[], next_cursor=cursor)  # the next run reads it whole
        self._read_to = str(end)
        return Page[ChainTx](
            items=evm.normalise(self.chain, records, address), next_cursor=str(end)
        )

    # --- balances ----------------------------------------------------------------------

    def balances(self, address: str, *, tokens: Sequence[AssetKey] = ()) -> list[AssetBalance]:
        out: list[AssetBalance] = []
        self.unreadable = set()
        # a spent budget stops the reading: the sync defers and reads the balances next run
        with contextlib.suppress(BudgetSpent):
            self._balances(address.lower(), out, self._contracts(tokens))
        return out

    def _contracts(self, tokens: Sequence[AssetKey]) -> list[str]:
        """The registry's tokens of this chain, then each other token asked for, once each,
        lower-case (an EVM contract is stored lower-case, D180 amended)."""
        out = [t.contract for t in asset_registry().tokens if t.chain is self.chain]
        seen = set(out)
        asked = {k.contract.lower() for k in tokens if k.chain is self.chain and k.contract}
        for contract in sorted(asked - seen):
            out.append(contract)
        return out

    def _balances(self, address: str, out: list[AssetBalance], contracts: list[str]) -> None:
        at = max(self.tip() - self._final, 0)
        tag = hex(at)
        native = self._call(
            {"module": "account", "action": "balance", "address": address, "tag": tag}
        )
        try:
            units = int(str(native))
        except ValueError:
            raise ChainSourceError(SOURCE_FAILED) from None
        out.append(AssetBalance(asset_key=evm.native(self.chain), units=units, at_block=at))
        data = BALANCE_OF + address[2:].rjust(64, "0")
        listed = {t.contract for t in asset_registry().tokens if t.chain is self.chain}
        for contract in contracts:
            key = AssetKey(chain=self.chain, contract=contract)
            params = {
                "module": "proxy",
                "action": "eth_call",
                "to": contract,
                "data": data,
                "tag": tag,
            }
            if contract in listed:  # a registry token answers, or the balance read fails
                held = self._hex(params)
            else:
                try:
                    result = self._call(params)
                except _InBand:
                    self.unreadable.add(key)
                    continue
                held = _word(result)
                if held is None:
                    self.unreadable.add(key)
                    continue
            out.append(AssetBalance(asset_key=key, units=held, at_block=at))


def _word(result: JsonValue) -> int | None:
    """A `balanceOf` answer: at most one 32-byte word of hex, else None (a longer answer is
    no uint256). An empty „0x“ (an address with no code) reads 0, as it did before, so a
    contract the classifier misread shows as a difference (`wallet.unreconciled`)."""
    if not isinstance(result, str) or not result.startswith("0x") or len(result) > 66:
        return None
    try:
        return int(result, 16) if result != "0x" else 0
    except ValueError:
        return None


def make_source(
    row: ChainRegistryRow,
    settings: Settings,
    budget: RequestBudget,
    *,
    fetch: Fetch = urllib_fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> EtherscanSource:
    """The chain's Etherscan source (services/chain/base.factory_for). No key →
    `wallet.key_missing` here, before any request; `fetch` and `sleep` are a test's."""
    key = setting(settings, "etherscan_key")
    if key is None:
        raise ChainSourceError(E.WALLET_KEY_MISSING)
    return EtherscanSource(
        row.code,
        SecretStr(key),
        base_url=setting(settings, "etherscan_url") or DEFAULT_URL,
        fetch=fetch,
        sleep=sleep,
        budget=budget,
    )
