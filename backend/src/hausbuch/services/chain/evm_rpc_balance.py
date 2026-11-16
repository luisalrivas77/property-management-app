"""BNB Smart Chain balance-only, over plain JSON-RPC (docs/phases/phase-7.md D181 (c), D186 (2));
step 3.

No history: `transactions` returns an empty page and keeps the cursor. `balances(address)`
reads `eth_getBalance` and each registry token's `balanceOf` (`eth_call`) at
`tip − confirmations_final`. `deposit(tx_hash, address)` reads an exchange withdrawal named by
its hash (`eth_getTransactionByHash`, `eth_getTransactionReceipt`, the block's timestamp by
`eth_getBlockByNumber`) into the deposit legs rules/chain/evm.deposit_from_receipt finds
(None when the transaction is unknown, not yet final, failed or credited the address
nothing); step 5 links it to the exchange's leg.

The RPC URL comes from Settings (a public node by default; a provider's key often sits in the
URL's path), so it is never logged or put into an exception: a failure carries its code and
HTTP status only. A JSON-RPC error -32005 (a node's request limit) is
`wallet.source_throttled`. Paced like Etherscan, with an injectable `sleep`; each call first
takes one request from the run's `RequestBudget` (services/chain/base, D182 (1)), and when it
says no the balances stop where they are (the sync ends the run deferred and reads them the
next run) and a deposit reads as not there yet.

`balance_only = True` tells the sync (services/chain/sync) to reconcile this address by
rules/chain/evm.reconcile_balance_only: the balance against Σ the deposits linked to it, so a
balance above them is reconciled (`transfer.unmatched_leg`) and one below is the flag that
coins left the address (`wallet.coins_left`, D181 (c)).

`make_source(row, settings, budget)` is the factory services/chain/base finds: the URL is
`HAUSBUCH_BSC_RPC_URL` (a secret setting, BNB Chain's public node by default); `host` is its
host name only.
"""

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import JsonValue

from hausbuch.rules import errors as E
from hausbuch.rules.chain import evm
from hausbuch.rules.chain.registry import ChainRegistryRow, registry
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import AssetBalance, AssetKey, Chain, ChainSourceKind, ChainTx, Page
from hausbuch.services.chain.base import ChainSourceError, RequestBudget, host_of, setting
from hausbuch.services.chain.evm_http import (
    SOURCE_FAILED,
    BudgetSpent,
    Post,
    post_json,
    urllib_post,
)
from hausbuch.settings import DEFAULT_BSC_RPC_URL, Settings

MIN_INTERVAL_S = 0.4
BALANCE_OF = "0x70a08231"  # balanceOf(address)
LIMIT_EXCEEDED = -32005


@dataclass
class RpcBalanceSource:
    chain: Chain
    url: str = field(repr=False)  # a provider URL may carry its key
    post: Post = field(default=urllib_post, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    budget: RequestBudget | None = field(default=None, repr=False)  # None: unbounded
    kind: ChainSourceKind = ChainSourceKind.EVM_RPC_BALANCE
    balance_only: bool = True  # D181 (c): the sync reconciles against linked deposits
    _calls: int = field(default=0, init=False, repr=False)
    _tip: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        row = registry().chain(self.chain)
        if row.chain_id is None:
            raise ValueError(f"{self.chain} is no EVM chain")
        self._final = row.confirmations_final

    @property
    def host(self) -> str:
        """The node's host name, the only part of its URL a run records (D181)."""
        return host_of(self.url)

    def _rpc(self, method: str, params: list[JsonValue]) -> JsonValue:
        if self.budget is not None and not self.budget.take():
            raise BudgetSpent
        if self._calls:
            self.sleep(MIN_INTERVAL_S)
        self._calls += 1
        payload = {"jsonrpc": "2.0", "id": self._calls, "method": method, "params": params}
        answer = post_json(self.post, self.url, payload)
        if not isinstance(answer, dict):
            raise ChainSourceError(SOURCE_FAILED)
        error = answer.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            if code == LIMIT_EXCEEDED:
                raise ChainSourceError(E.WALLET_SOURCE_THROTTLED)
            raise ChainSourceError(SOURCE_FAILED)
        return answer.get("result")

    def _hex(self, method: str, params: list[JsonValue]) -> int:
        result = self._rpc(method, params)
        if not isinstance(result, str) or not result.startswith("0x"):
            raise ChainSourceError(SOURCE_FAILED)
        return int(result, 16) if result != "0x" else 0

    def tip(self) -> int:
        """The chain's tip, read once per instance: the history, the balances and a deposit
        of one sync run are read to the same block (D186 (2)), so a sync builds one source
        per run."""
        if self._tip is None:
            self._tip = self._hex("eth_blockNumber", [])
        return self._tip

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]:
        return Page[ChainTx](items=[], next_cursor=cursor)  # D181 (c): balance only

    def balances(self, address: str) -> list[AssetBalance]:
        out: list[AssetBalance] = []
        # a spent budget stops the reading: the sync defers and reads the balances next run
        with contextlib.suppress(BudgetSpent):
            self._balances(address.lower(), out)
        return out

    def _balances(self, address: str, out: list[AssetBalance]) -> None:
        at = max(self.tip() - self._final, 0)
        tag = hex(at)
        units = self._hex("eth_getBalance", [address, tag])
        out.append(AssetBalance(asset_key=evm.native(self.chain), units=units, at_block=at))
        data = BALANCE_OF + address[2:].rjust(64, "0")
        for token in asset_registry().tokens:
            if token.chain is not self.chain:
                continue
            held = self._hex("eth_call", [{"to": token.contract, "data": data}, tag])
            key = AssetKey(chain=self.chain, contract=token.contract)
            out.append(AssetBalance(asset_key=key, units=held, at_block=at))

    def deposit(self, tx_hash: str, address: str) -> ChainTx | None:
        try:
            return self._deposit(tx_hash, address)
        except BudgetSpent:
            return None  # the budget is spent: the next sync reads it

    def _deposit(self, tx_hash: str, address: str) -> ChainTx | None:
        transaction = self._rpc("eth_getTransactionByHash", [tx_hash])
        if not isinstance(transaction, dict) or transaction.get("blockNumber") is None:
            return None  # unknown or still pending
        block = int(str(transaction["blockNumber"]), 16)
        if block > self.tip() - self._final:
            return None  # not final yet: the next sync reads it
        receipt = self._rpc("eth_getTransactionReceipt", [tx_hash])
        if not isinstance(receipt, dict):
            return None
        header = self._rpc("eth_getBlockByNumber", [hex(block), False])
        if not isinstance(header, dict) or not isinstance(header.get("timestamp"), str):
            raise ChainSourceError(SOURCE_FAILED)
        at = datetime.fromtimestamp(int(str(header["timestamp"]), 16), tz=UTC)
        return evm.deposit_from_receipt(self.chain, transaction, receipt, at, address)


def make_source(
    row: ChainRegistryRow,
    settings: Settings,
    budget: RequestBudget,
    *,
    post: Post = urllib_post,
    sleep: Callable[[float], None] = time.sleep,
) -> RpcBalanceSource:
    """The chain's balance-only source (services/chain/base.factory_for); `post` and `sleep`
    are a test's."""
    url = setting(settings, "bsc_rpc_url") or DEFAULT_BSC_RPC_URL
    return RpcBalanceSource(row.code, url, post=post, sleep=sleep, budget=budget)
