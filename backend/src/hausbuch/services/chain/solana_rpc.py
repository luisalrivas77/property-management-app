"""Solana over standard JSON-RPC (docs/phases/phase-7.md D181 (d), D182, D192, §5, Q-O93); built
in step 2.

Every provider that speaks the Solana JSON-RPC serves it (the public endpoint, Alchemy, Helius,
a node of one's own): the URL is `HAUSBUCH_SOLANA_RPC_URL` and the pace `HAUSBUCH_SOLANA_RPC_RPS`
(4 on the public endpoint's per-method cap, 40 on a paid plan). The caller passes both; nothing
here reads the environment.

Read-only methods only: `getSignaturesForAddress` (paged back with `before` to the address's
first signature, or to `until` the sync's cursor), `getTransaction` (encoding jsonParsed,
maxSupportedTransactionVersion 0, at `finalized`), `getBalance`, `getTokenAccountsByOwner` over
both token programs, `getInflationReward`, `getEpochInfo` and `getBlockTime`.

`getSignaturesForAddress` lists the transactions whose account keys hold the address, so a
token credited to an own token account names the token account, never the owner (solana_tx.yaml
`spl_in`). `token_accounts` gives every token account of an owner, and the sync lists each of
them too (a closed one from the stored originals), merged and deduplicated by signature.

The URL is a secret (a provider's key travels in its query or its path): it is never logged,
never in an exception, never in a repr; `source_host` gives the host alone (wallet_sync's
`source_host`, D182 (1)). A failure carries what may be logged: ChainSourceError
`wallet.source_throttled` (429), `wallet.source_refused` (401, 403), `wallet.sync_deferred`
(the run's request budget spent), or SolanaRpcError with an exception class, an HTTP status or
the JSON-RPC error number, never the provider's message. A listed signature whose transaction
the provider no longer serves (null, or a JSON-RPC error of the ledger) raises
HistoryUnavailable with the slot reached, so a sync stops there and says so (Q-O93), never
skipping a transaction silently.
"""

import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from pydantic import JsonValue, SecretStr

from hausbuch.rules import errors as E
from hausbuch.rules.chain import solana
from hausbuch.schemas.chain import (
    AssetBalance,
    AssetKey,
    Chain,
    ChainSourceKind,
    ChainTx,
    Page,
    RawTransfer,
)
from hausbuch.services.chain.base import ChainSourceError
from hausbuch.services.prices.http import MAX_BYTES, TIMEOUT_S, USER_AGENT, ResponseTooLarge

COMMITMENT = "finalized"
SIGNATURE_PAGE = 1000  # getSignaturesForAddress's largest page
TRANSACTION_PAGE = 100  # transactions per Page the sync writes
DEFAULT_RPS = 4.0
# JSON-RPC errors that say the provider holds no ledger for that slot (Q-O93): -32004 block not
# available, -32007 slot skipped or missing in long-term storage, -32009 missing in long-term
# storage, -32011 transaction history not available.
_HISTORY_ERRORS = frozenset({-32004, -32007, -32009, -32011})

# (url, headers, body, timeout, max_bytes) -> (HTTP status, body). An HTTP error status is
# returned, not raised; a body over max_bytes raises ResponseTooLarge.
Post = Callable[[str, dict[str, str], bytes, float, int], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect (as services/prices/http.py): the URL's key goes nowhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_post(
    url: str, headers: dict[str, str], body: bytes, timeout: float, max_bytes: int
) -> tuple[int, bytes]:
    """The one POST of the JSON-RPC: the same rules as prices/http.urllib_fetch (verifying TLS,
    a timeout, a size cap, no redirect), which is GET only."""
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ResponseTooLarge
            return response.status, data
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        return status, b""


class SolanaRpcError(Exception):
    """No usable answer. `kind` is an exception class name, `HTTPError`, `rpc` (a JSON-RPC error,
    `code` its number) or `parse`: the only things logged, never the provider's message."""

    def __init__(self, kind: str, code: int | None = None) -> None:
        super().__init__(kind if code is None else f"{kind} {code}")
        self.kind = kind
        self.code = code


class HistoryUnavailable(SolanaRpcError):
    """The provider listed a signature but serves no transaction for it (Q-O93): the sync stops
    at `slot` and the wallet page says how far the history reaches."""

    def __init__(self, slot: int | None, code: int | None = None) -> None:
        super().__init__("history", code)
        self.slot = slot


@dataclass(frozen=True)
class SignatureInfo:
    signature: str
    slot: int
    failed: bool
    block_time: datetime | None


@dataclass(frozen=True)
class TokenAccount:
    pubkey: str
    mint: str
    program: str
    units: int
    lamports: int


@dataclass
class SolanaRpc:
    """One provider's JSON-RPC. `post` and `sleep` are injectable (tests replay recorded
    answers); `budget` caps the requests of one run (D182 (4): RPS × 600)."""

    url: SecretStr = field(repr=False)
    rps: float = DEFAULT_RPS
    post: Post = field(default=urllib_post, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    budget: int | None = None
    signature_page: int = SIGNATURE_PAGE
    transaction_page: int = TRANSACTION_PAGE
    requests: int = 0
    kind: ChainSourceKind = ChainSourceKind.SOLANA_RPC
    _ids: int = field(default=0, repr=False)
    _signatures: dict[tuple[str, str | None], list[SignatureInfo]] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        if self.rps <= 0:
            raise ValueError("the pace is a positive number of requests per second")
        if not 1 <= self.signature_page <= SIGNATURE_PAGE:
            raise ValueError("a signature page holds 1 to 1000 entries")

    @property
    def source_host(self) -> str:
        """The provider's host name alone: what wallet_sync stores and a log may name."""
        return urlsplit(self.url.get_secret_value()).hostname or ""

    # --- the transport ------------------------------------------------------------------

    def call(self, method: str, params: Sequence[JsonValue]) -> JsonValue:
        """One request: its `result`, or the error a caller may log."""
        if self.budget is not None and self.requests >= self.budget:
            raise ChainSourceError(E.WALLET_SYNC_DEFERRED)
        if self.requests:
            self.sleep(1 / self.rps)
        self.requests += 1
        self._ids += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._ids, "method": method, "params": list(params)}
        ).encode()
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        failure: str | None = None
        try:
            status, answer = self.post(
                self.url.get_secret_value(), headers, body, TIMEOUT_S, MAX_BYTES
            )
        except Exception as exc:  # noqa: BLE001 - the class name only: a message can quote the URL
            failure = type(exc).__name__
        # Raised outside the handler, so neither __cause__ nor __context__ holds the original.
        if failure is not None:
            raise SolanaRpcError(failure)
        if status == 429:
            raise ChainSourceError(E.WALLET_SOURCE_THROTTLED, 429)
        if status in (401, 403):
            raise ChainSourceError(E.WALLET_SOURCE_REFUSED, status)
        if status != 200:
            raise SolanaRpcError("HTTPError", status)
        try:
            data = json.loads(answer)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise SolanaRpcError("parse")
        error = data.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            code = code if isinstance(code, int) else None
            if code == 429:
                raise ChainSourceError(E.WALLET_SOURCE_THROTTLED, 429)
            if code in _HISTORY_ERRORS:
                raise HistoryUnavailable(None, code)
            raise SolanaRpcError("rpc", code)
        if "result" not in data:
            raise SolanaRpcError("parse")
        return cast(JsonValue, data["result"])

    # --- signatures and transactions -----------------------------------------------------

    def signatures(self, address: str, until: str | None = None) -> list[SignatureInfo]:
        """Every signature of the address newer than `until` (all of them without one), paged
        back with `before` to the first; oldest first. Failed transactions included: their
        fee is a movement (D183 (2))."""
        key = (address, until)
        if key in self._signatures:
            return self._signatures[key]
        found: list[SignatureInfo] = []
        before: str | None = None
        while True:
            options: dict[str, JsonValue] = {"limit": self.signature_page, "commitment": COMMITMENT}
            if before is not None:
                options["before"] = before
            if until is not None:
                options["until"] = until
            page = self.call("getSignaturesForAddress", [address, options])
            if not isinstance(page, list):
                raise SolanaRpcError("parse")
            for raw in page:
                found.append(_signature_info(raw))
            if len(page) < self.signature_page:
                break
            before = found[-1].signature
        found.reverse()
        self._signatures[key] = found
        return found

    def transaction(self, signature: str, slot: int | None = None) -> dict[str, JsonValue]:
        """One finalized transaction as jsonParsed gives it (version 0 included)."""
        options: dict[str, JsonValue] = {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": COMMITMENT,
        }
        try:
            record = self.call("getTransaction", [signature, options])
        except HistoryUnavailable as exc:
            raise HistoryUnavailable(slot, exc.code) from None
        if record is None:
            raise HistoryUnavailable(slot)
        if not isinstance(record, dict):
            raise SolanaRpcError("parse")
        return record

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]:
        """The next page of the address's transactions after `cursor` (the last signature a
        sync wrote), oldest first. `next_cursor` is the page's last signature while more
        follow, else None; a sync keeps the last item's hash as its cursor either way."""
        listed = self.signatures(address, cursor)
        page = listed[: self.transaction_page]
        items = [self.chain_tx(self.transaction(s.signature, s.slot), {address}) for s in page]
        more = len(listed) > len(page)
        if more and page:
            self._signatures[(address, page[-1].signature)] = listed[len(page) :]
        return Page[ChainTx](items=items, next_cursor=page[-1].signature if more else None)

    @staticmethod
    def chain_tx(record: Mapping[str, JsonValue], own: set[str]) -> ChainTx:
        """The record as the chain-neutral ChainTx; `raw` keeps it whole for the stored original
        and the classifier (rules/chain/solana.classify_solana reads the raw record)."""
        keys = solana.account_keys(record)
        payer = bool(keys) and keys[0] in own
        fee = cast(int, cast(Mapping[str, JsonValue], record["meta"])["fee"])
        transfers = []
        for d in solana.solana_deltas(record, own):
            if d.units == 0:
                continue
            side = d.token_account or next(iter(sorted(own)))
            transfers.append(
                RawTransfer(
                    asset_key=d.asset_key,
                    from_addr=side if d.units < 0 else None,
                    to_addr=side if d.units > 0 else None,
                    units=abs(d.units),
                    index=d.asset_key.contract or "SOL",
                )
            )
        return ChainTx(
            hash=solana.signature(record),
            block=cast(int, record["slot"]),
            occurred_at=solana.block_time(record),
            status="failed" if solana.failed(record) else "ok",
            fee=(AssetKey(chain=Chain.SOLANA), fee) if payer else None,
            transfers=transfers,
            program_calls=solana.program_calls(record),
            raw=dict(record),
        )

    # --- balances ------------------------------------------------------------------------

    def lamports(self, address: str) -> tuple[int, int]:
        """getBalance: (lamports, the slot it was read at)."""
        result = _obj(self.call("getBalance", [address, {"commitment": COMMITMENT}]))
        return _int(result["value"]), _int(_obj(result["context"])["slot"])

    def token_accounts(self, owner: str) -> tuple[list[TokenAccount], int]:
        """Every token account of the owner over both token programs, and the lowest slot read."""
        accounts: list[TokenAccount] = []
        slots = []
        for program in (solana.TOKEN_PROGRAM, solana.TOKEN_2022_PROGRAM):
            options = {"encoding": "jsonParsed", "commitment": COMMITMENT}
            result = _obj(
                self.call("getTokenAccountsByOwner", [owner, {"programId": program}, options])
            )
            slots.append(_int(_obj(result["context"])["slot"]))
            for raw in _list(result["value"]):
                entry = _obj(raw)
                account = _obj(entry["account"])
                info = _obj(_obj(_obj(account["data"])["parsed"])["info"])
                accounts.append(
                    TokenAccount(
                        pubkey=str(entry["pubkey"]),
                        mint=str(info["mint"]),
                        program=program,
                        units=int(str(_obj(info["tokenAmount"])["amount"])),
                        lamports=_int(account["lamports"]),
                    )
                )
        return accounts, min(slots)

    def balance_parts(
        self, address: str, stake_accounts: Sequence[str] = ()
    ) -> tuple[dict[AssetKey, list[int]], int]:
        """D186 (2): the SOL of the main account, the rent in each token account (a wSOL
        account's wrapped amount counted as WSOL, not twice) and each own stake account's
        lamports; each mint's units over the owner's token accounts. With the lowest slot."""
        main, slot = self.lamports(address)
        sol = AssetKey(chain=Chain.SOLANA)
        parts: dict[AssetKey, list[int]] = {sol: [main]}
        accounts, token_slot = self.token_accounts(address)
        slots = [slot, token_slot]
        wrapped = solana.native_mint()
        for account in accounts:
            rent = account.lamports - (account.units if account.mint == wrapped else 0)
            parts[sol].append(rent)
            key = AssetKey(chain=Chain.SOLANA, contract=account.mint)
            parts.setdefault(key, []).append(account.units)
        for stake in stake_accounts:
            lamports, stake_slot = self.lamports(stake)
            parts[sol].append(lamports)
            slots.append(stake_slot)
        return parts, min(slots)

    def balances(self, address: str, stake_accounts: Sequence[str] = ()) -> list[AssetBalance]:
        parts, slot = self.balance_parts(address, stake_accounts)
        return [
            AssetBalance(asset_key=key, units=sum(units), at_block=slot)
            for key, units in parts.items()
            if key.contract is None or sum(units)
        ]

    # --- inflation rewards (§6 (10)) -------------------------------------------------------

    def epoch(self) -> int:
        """The current epoch (getEpochInfo); rewards are read up to the one before it."""
        return _int(_obj(self.call("getEpochInfo", [{"commitment": COMMITMENT}]))["epoch"])

    def inflation_rewards(
        self, stake_accounts: Sequence[str], epoch: int
    ) -> list[dict[str, JsonValue] | None]:
        """One getInflationReward entry per stake account for that epoch (None: no reward)."""
        result = self.call(
            "getInflationReward",
            [list(stake_accounts), {"epoch": epoch, "commitment": COMMITMENT}],
        )
        entries = _list(result)
        if len(entries) != len(stake_accounts):
            raise SolanaRpcError("parse")
        return [cast(dict[str, JsonValue], e) if isinstance(e, dict) else None for e in entries]

    def block_time(self, slot: int) -> datetime:
        """getBlockTime: the epoch's end is its reward's effective slot's time."""
        value = self.call("getBlockTime", [slot])
        if not isinstance(value, int):
            raise HistoryUnavailable(slot)
        return datetime.fromtimestamp(value, UTC)


def _obj(value: object) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise SolanaRpcError("parse")
    return cast(Mapping[str, JsonValue], value)


def _list(value: object) -> list[JsonValue]:
    if not isinstance(value, list):
        raise SolanaRpcError("parse")
    return value


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SolanaRpcError("parse")
    return value


def _signature_info(raw: object) -> SignatureInfo:
    entry = _obj(raw)
    when = entry.get("blockTime")
    return SignatureInfo(
        signature=str(entry["signature"]),
        slot=_int(entry["slot"]),
        failed=entry.get("err") is not None,
        block_time=datetime.fromtimestamp(when, UTC) if isinstance(when, int) else None,
    )
