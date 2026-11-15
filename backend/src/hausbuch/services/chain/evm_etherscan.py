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
- `balances(address)`: the native balance and each registry token's `balanceOf`, at
  `tip − 12`, the block the movements were read to (D186 (2)).

Paced at 2.5 calls a second (the free tier allows 3) with an injectable `sleep`. Etherscan
answers its errors with HTTP 200 and `status = "0"`: a rate limit is
`wallet.source_throttled`, a key it refuses (invalid, missing, a chain the plan does not
cover) `wallet.source_refused`; `status = "0"` with an empty list is no data. No key →
`wallet.key_missing` before any request. The key is never in a log or an exception: a
failure carries its code and HTTP status only (services/chain/evm_http).
"""

import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from pydantic import JsonValue, SecretStr

from hausbuch.rules import errors as E
from hausbuch.rules.chain import evm
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.chain.sync_window import window
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import AssetBalance, AssetKey, Chain, ChainSourceKind, ChainTx, Page
from hausbuch.services.chain.base import ChainSourceError
from hausbuch.services.chain.evm_http import SOURCE_FAILED, get_json
from hausbuch.services.prices.http import Fetch, urllib_fetch

DEFAULT_URL = "https://api.etherscan.io/v2/api"
MIN_INTERVAL_S = 0.4  # 2.5 calls a second
PAGE_ROWS = 1000
MAX_PAGES = 10  # Etherscan answers page × offset up to 10,000 rows
BALANCE_OF = "0x70a08231"  # balanceOf(address)

Row = Mapping[str, JsonValue]


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
    kind: ChainSourceKind = ChainSourceKind.EVM_ETHERSCAN
    _calls: int = field(default=0, init=False, repr=False)
    _tip: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        row = registry().chain(self.chain)
        if row.chain_id is None:
            raise ValueError(f"{self.chain} has no EVM chain id")
        self._chain_id = row.chain_id
        self._final = row.confirmations_final

    # --- one call ----------------------------------------------------------------------

    def _call(self, params: Mapping[str, str | int]) -> JsonValue:
        if self.key is None or not self.key.get_secret_value():
            raise ChainSourceError(E.WALLET_KEY_MISSING)
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
            raise _refusal(str(message or ""))
        result = answer.get("result")
        if str(answer.get("status", "1")) == "0":
            if isinstance(result, list):
                return []  # "No transactions found"
            raise _refusal(str(result or answer.get("message") or ""))
        if isinstance(result, str) and not result.startswith("0x") and "jsonrpc" in answer:
            raise _refusal(result)  # the proxy module's rate-limit text with no status
        return result

    def _hex(self, params: Mapping[str, str | int]) -> int:
        result = self._call(params)
        if not isinstance(result, str) or not result.startswith("0x"):
            raise ChainSourceError(SOURCE_FAILED)
        return int(result, 16) if result != "0x" else 0

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
        span = window(self.chain, int(cursor) if cursor else None, self.tip())
        if span is None:
            return Page[ChainTx](items=[], next_cursor=cursor)
        start, end = span
        records = {action: self._rows(action, address, start, end) for action in evm.ACTIONS}
        return Page[ChainTx](
            items=evm.normalise(self.chain, records, address), next_cursor=str(end)
        )

    # --- balances ----------------------------------------------------------------------

    def balances(self, address: str) -> list[AssetBalance]:
        address = address.lower()
        at = max(self.tip() - self._final, 0)
        tag = hex(at)
        native = self._call(
            {"module": "account", "action": "balance", "address": address, "tag": tag}
        )
        try:
            units = int(str(native))
        except ValueError:
            raise ChainSourceError(SOURCE_FAILED) from None
        out = [AssetBalance(asset_key=evm.native(self.chain), units=units, at_block=at)]
        data = BALANCE_OF + address[2:].rjust(64, "0")
        for token in asset_registry().tokens:
            if token.chain is not self.chain:
                continue
            held = self._hex(
                {
                    "module": "proxy",
                    "action": "eth_call",
                    "to": token.contract,
                    "data": data,
                    "tag": tag,
                }
            )
            key = AssetKey(chain=self.chain, contract=token.contract)
            out.append(AssetBalance(asset_key=key, units=held, at_block=at))
        return out
