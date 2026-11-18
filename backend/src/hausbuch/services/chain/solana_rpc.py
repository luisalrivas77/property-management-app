"""Solana over standard JSON-RPC (docs/phases/phase-7.md D181 (d), D182, D192, §5, Q-O93); built
in step 2.

Every provider that speaks the Solana JSON-RPC serves it (the public endpoint, Alchemy, Helius,
a node of one's own): the URL is `HAUSBUCH_SOLANA_RPC_URL` and the pace `HAUSBUCH_SOLANA_RPC_RPS`
(4 on the public endpoint's per-method cap, 40 on a paid plan). The caller passes both; nothing
here reads the environment.

Read-only methods only: `getSlot` (the run's finalized height), `getSignaturesForAddress`
(paged back with `before` to the address's first signature, or to below the slot the cursor
names), `getTransaction` (encoding jsonParsed, maxSupportedTransactionVersion 0, at
`finalized`), `getBalance`, `getTokenAccountsByOwner` over both token programs,
`getInflationReward`, `getEpochInfo` and `getBlockTime`.

`getSignaturesForAddress` lists the transactions whose account keys hold the address, so a
token credited to an own token account names the token account, never the owner (solana_tx.yaml
`spl_in`). `token_accounts` gives every open token account of an owner; SolanaSource lists each
of them too, and a closed one when a record of the run names it or, after a run the budget
cut, when the address's stored originals do, merged and deduplicated by signature.

The URL is a secret (a provider's key travels in its query or its path): it is never logged,
never in an exception, never in a repr; `source_host` gives the host alone (wallet_sync's
`source_host`, D182 (1)). A failure carries what may be logged: ChainSourceError
`wallet.source_throttled` (429), `wallet.source_refused` (401, 403), `wallet.sync_deferred`
(the run's request budget spent), or SolanaRpcError with an exception class, an HTTP status or
the JSON-RPC error number, never the provider's message. A listed signature whose transaction
the provider no longer serves (null, or a JSON-RPC error of the ledger) raises
HistoryUnavailable, which SolanaSource turns into `wallet.source_failed`: the run fails and
its cursor stays, never skipping a transaction silently. The spec's Q-O93 asks for a run
that records `oldest_slot_reached` and stops there instead; that is not built (an accepted,
still open deviation, phase-7.md §15.1).
"""

import heapq
import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from pydantic import JsonValue, SecretStr

from hausbuch.rules import errors as E
from hausbuch.rules.chain import solana
from hausbuch.rules.chain.registry import ChainRegistryRow
from hausbuch.schemas.chain import (
    AssetBalance,
    AssetKey,
    Chain,
    ChainSourceKind,
    ChainTx,
    Page,
    RawTransfer,
)
from hausbuch.services.chain.base import ChainSourceError, RequestBudget, setting
from hausbuch.services.prices.http import MAX_BYTES, TIMEOUT_S, USER_AGENT, ResponseTooLarge
from hausbuch.settings import Settings

COMMITMENT = "finalized"
SIGNATURE_PAGE = 1000  # getSignaturesForAddress's largest page
TRANSACTION_PAGE = 100  # transactions per Page the sync writes
DEFAULT_RPS = 4.0
# JSON-RPC errors that say the provider holds no ledger for that slot (Q-O93): -32001 block
# cleaned up, -32004 block not available, -32007 slot skipped or missing in long-term storage,
# -32009 missing in long-term storage, -32011 transaction history not available.
_HISTORY_ERRORS = frozenset({-32001, -32004, -32007, -32009, -32011})

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
    # The sync's shared RequestBudget.take (D182 (1)): asked before every request, a „no“
    # raises wallet.sync_deferred as the int budget does.
    take: Callable[[], bool] | None = field(default=None, repr=False)
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
        if self.take is not None and not self.take():
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
        fee is a movement (D183 (2)). Paging ends on an empty page only: a provider may answer
        a page shorter than the limit while older signatures remain."""
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
            if not page:
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

    def transactions(
        self,
        address: str,
        cursor: str | None,
        *,
        own: Collection[str] | None = None,
        own_token_accounts: Collection[str] | None = None,
    ) -> Page[ChainTx]:
        """The next page of the address's transactions after `cursor` (the last signature a
        sync wrote), oldest first. `next_cursor` is the page's last signature while more
        follow, else None; a sync keeps the last item's hash as its cursor either way.

        Each item's `transfers` and `fee` are right only when `own` holds every own address
        of the wallet (the address alone without it: a transfer to its registered stake
        account would read as leaving) and `own_token_accounts` resolves token rows without
        an owner (chain_tx). The classifier reads `raw`, never these fields."""
        listed = self.signatures(address, cursor)
        page = listed[: self.transaction_page]
        mine = set(own) | {address} if own is not None else {address}
        items = [
            self.chain_tx(self.transaction(s.signature, s.slot), mine, own_token_accounts)
            for s in page
        ]
        more = len(listed) > len(page)
        if more and page:
            self._signatures[(address, page[-1].signature)] = listed[len(page) :]
        return Page[ChainTx](items=items, next_cursor=page[-1].signature if more else None)

    @staticmethod
    def chain_tx(
        record: Mapping[str, JsonValue],
        own: set[str],
        own_token_accounts: Collection[str] | None = None,
        *,
        strict: bool = True,
    ) -> ChainTx:
        """The record as the chain-neutral ChainTx; `raw` keeps it whole for the stored original
        and the classifier (rules/chain/solana.classify_solana reads the raw record, never the
        fields here). `transfers` and `fee` are right only when `own` holds every own address
        (a payment between two of them is no transfer out), and a token row without an owner
        needs `own_token_accounts`: a record whose token legs cannot be read is a
        ChainSourceError `wallet.record_unreadable`, as every source's error (its cause names
        the signature only). `strict=False` (the sync's SolanaSource): such a record gets no
        `transfers` instead, and the classifier makes it a card (solana.classify_tx), so one
        record never blocks its address."""
        keys = solana.account_keys(record)
        if not solana.readable_meta(record):  # an old slot's record with `meta: null`
            if strict:
                raise ChainSourceError(E.WALLET_RECORD_UNREADABLE)
            return ChainTx(
                hash=solana.signature(record),
                block=cast(int, record["slot"]),
                occurred_at=solana.block_time(record),
                status="ok",
                fee=None,
                transfers=[],
                program_calls=[],  # read with the inner calls, which live in the meta
                raw=dict(record),
            )
        payer = bool(keys) and keys[0] in own
        fee = cast(int, cast(Mapping[str, JsonValue], record["meta"])["fee"])
        try:
            deltas = solana.solana_deltas(record, own, own_token_accounts)
        except E.RuleError as exc:
            if exc.code != E.WALLET_RECORD_UNREADABLE:
                raise
            if strict:
                raise ChainSourceError(E.WALLET_RECORD_UNREADABLE) from exc
            deltas = []
        transfers = []
        for d in deltas:
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

    def finalized_slot(self) -> int:
        """getSlot at `finalized`: the height a run reads its history to."""
        return _int(self.call("getSlot", [{"commitment": COMMITMENT}]))

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
        """getBlockTime: the epoch's end is its reward's effective slot's time. A ledger error
        names the slot, as transaction() does."""
        try:
            value = self.call("getBlockTime", [slot])
        except HistoryUnavailable as exc:
            raise HistoryUnavailable(slot, exc.code) from None
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


# --- the sync's ChainSource (services/chain/base.py's protocol) ------------------------------


@dataclass(frozen=True)
class SourceCursor:
    """A SolanaSource cursor: `<slot>:<signature>`; after a run the budget cut
    `<slot>:<signature>;<closed>`, or `<slot>:<signature>;<closed>;<index>;<at>;<prefix>` while
    a sweep of the stored token accounts is under way (≤ 140 characters; never a token
    account's address, only an index into them, Q-O162).

    `slot` is where the next run lists the address and its open token accounts from,
    inclusive: every transaction of theirs below it is served. `signature` is the highest one
    served at that slot (empty while one there is still unserved), so the cursor changes
    while a run moves on and comes back unchanged at the tip. Without the rest, the own token
    accounts found in earlier runs (services/chain/sync passes the stored originals) are
    complete below `slot` and listed only when a record names them. With it, the run sweeps
    them one at a time in their fixed order (SolanaSource._order: by first appearance in the
    stored originals): those before `index` are complete below `prefix` (≥ `slot`), the one
    at `index` below `at`, the later ones below `closed`; empty means from their first. The
    three-part form is the same with `index` 0 and `at` equal to `closed`."""

    slot: int | None = None
    signature: str = ""
    closed: int | None = None
    split: bool = False  # the stored token accounts are swept from `index` at the start
    index: int = 0  # the stored token account the sweep stands on (Q-O162)
    at: int | None = -1  # where that one is complete to; -1 (the default) means `closed`
    prefix: int | None = None  # where those before `index` are complete to (index > 0 only)

    def __post_init__(self) -> None:
        if self.at == -1:
            object.__setattr__(self, "at", self.closed)

    @property
    def low(self) -> int | None:
        """Where the own token accounts found in earlier runs are complete to, before a sweep
        (None: nowhere)."""
        return self.closed if self.split else self.slot

    @classmethod
    def parse(cls, text: str | None) -> "SourceCursor":
        """A cursor this source did not write starts over (None): the history is then read
        again and the movement hashes keep it once."""
        head, *extra = (text or "").split(";")
        slot, _, signature = head.partition(":")
        if not slot.isdigit() or len(extra) not in (0, 1, 4):
            return cls()
        if not extra:
            return cls(int(slot), signature)
        if any(part and not part.isdigit() for part in extra):
            return cls()
        closed, *sweep = (int(part) if part else None for part in extra)
        if not sweep:
            if closed is not None and closed >= int(slot):
                return cls(int(slot), signature)
            return cls(int(slot), signature, closed, True)
        index, at, prefix = sweep
        if index is None or (index > 0) != (prefix is not None):
            return cls()
        return cls(int(slot), signature, closed, True, index, at, prefix)

    def text(self) -> str:
        head = f"{self.slot}:{self.signature}"
        if not self.split:
            return head
        closed = "" if self.closed is None else str(self.closed)
        if self.index == 0 and self.at == self.closed:
            return f"{head};{closed}"
        at = "" if self.at is None else str(self.at)
        prefix = "" if self.prefix is None else str(self.prefix)
        return f"{head};{closed};{self.index};{at};{prefix}"


def owned_token_accounts(record: Mapping[str, JsonValue], owner: str) -> set[str]:
    """The token accounts the record's balance rows name with `owner` as their owner."""
    keys = solana.account_keys(record)
    meta = record.get("meta")
    found: set[str] = set()
    if not isinstance(meta, Mapping):
        return found
    for key in ("preTokenBalances", "postTokenBalances"):
        for row in _list(meta.get(key) or []):
            if isinstance(row, Mapping) and row.get("owner") == owner:
                index = row.get("accountIndex")
                if isinstance(index, int) and 0 <= index < len(keys):
                    found.add(keys[index])
    return found


def _covers(have: int | None, want: int | None) -> bool:
    """Whether a listing from slot `have` holds everything from `want` (None: the first)."""
    return have is None or (want is not None and have <= want)


def _lowest(*bounds: int | None) -> int | None:
    """The lowest of listing bounds, None (from the first) below every slot."""
    return None if None in bounds else min(cast(int, b) for b in bounds)


# Records the core read from the address's stored originals (sync passes them on demand).
StoredRecords = Callable[[], Iterable[Mapping[str, JsonValue]]]


@dataclass
class _Run:
    """One address's listing inside one source (a source is built per address run)."""

    address: str
    start: SourceCursor
    loader: StoredRecords | None = None  # the address's stored records (services/chain/sync)
    height: int = 0  # the finalized slot fixed at the start: nothing above it is served
    ready: bool = False  # the run's start listings are done
    accounts: set[str] = field(default_factory=set)  # own token accounts known so far
    stored: set[str] | None = None  # those found in earlier runs, read on demand
    order: list[str] | None = None  # the same in their fixed order (Q-O162)
    index: dict[str, int] = field(default_factory=dict)  # account -> its place in `order`
    sweep: int = 0  # the place in `order` the sweep stands on (a split start, Q-O162)
    listed: dict[str, int | None] = field(default_factory=dict)  # account -> listed from
    by_account: dict[str, set[str]] = field(default_factory=dict)  # account -> its listed sigs
    cut_from: dict[str, int | None] = field(default_factory=dict)  # a cut listing's bound
    closed_side: set[str] = field(default_factory=set)  # accounts listed as closed ones
    pending: dict[str, SignatureInfo] = field(default_factory=dict)  # listed, not served
    queue: list[tuple[int, str]] = field(default_factory=list)  # heap of pending positions
    open_side: set[str] = field(default_factory=set)  # listed via the address or an open one
    served: set[str] = field(default_factory=set)
    last: tuple[int, str] | None = None  # the highest (slot, signature) served
    cuts: list[tuple[bool, int | None]] = field(default_factory=list)  # (open side, from)

    @property
    def complete(self) -> bool:
        return not self.pending and not self.cuts


@dataclass
class SolanaSource:
    """The ChainSource the sync reads Solana through (services/chain/base.py; D181 (d), D182).

    **What is listed.** `getSignaturesForAddress` lists only the transactions whose account
    keys hold the address, so a token credited to an own token account names the token
    account alone (solana_tx.yaml `spl_in`). A run therefore lists the address, each token
    account `getTokenAccountsByOwner` gives it now, and each token account a record read in
    the run names as the address's (a closed one: its opening and closing transactions hold
    the owner), merged by signature and read oldest first by (slot, signature). A token
    account found that no earlier run stored is listed from its first transaction; one an
    earlier run stored (`stored`: the sync passes the address's stored originals, read only
    when needed) from where the cursor says its transactions are complete.

    **One height.** The run fixes the finalized slot first (`getSlot`) and serves nothing
    above it, so listings read moments apart agree on where the history ends.

    **The cursor** (SourceCursor) stands on the lowest slot something listed is still unserved
    at, else on the highest transaction served; the next run lists again from that slot,
    inclusive, so the transactions of that slot are read once more and the movement hashes
    store them once. A page never splits a slot, and the slot it starts on does not count
    toward its size. At the tip it comes back unchanged, which ends the sync's loop; a page is
    never handed back with the cursor it was given while something listed is unserved and the
    budget has not refused.

    **The budget.** Every request asks the run's RequestBudget first; a „no“ ends the page
    where it stood (the sync then marks the run deferred), never an error, and nothing taken
    from the listing is dropped before its record and block time are in hand. A run the budget
    cut writes `closed`: the next runs sweep every token account an earlier run stored, so a
    closed one with transactions still unserved is found without the record that names it.
    The sweep (Q-O162) takes them one at a time in a fixed order (by first appearance in the
    stored originals, which only grow at their end): it lists one, serves its transactions,
    then the next, and only once all are swept does it serve the address's own listing. The
    cursor carries how far it got as an index into that order and where the account it
    stands on is complete to, never the account, so a daily budget that always ends at the
    same place still moves on as long as it covers the start (the height, the address's
    listing, both token programs and each open token account's listing), one more listing
    and the transactions of one slot of one account. Likewise a listing of 1,000 signatures
    a page needs its pages within one run's budget (§15.1).

    **Per address.** Each own address is its own run, read and reconciled alone (the
    `transfers` of a ChainTx are the address's own), as solana.classify_tx reads it. A stake
    account the wallet registers is an address of its own; its inflation rewards are not
    read yet (§15.1).

    **Stored as read.** `raw` is the provider's record; only a record with a token balance row
    without an `owner`, or one in which an own token account's lamports change with no token
    balance row naming it (a stranger's System transfer into it, D186 (2) amended 18 Nov
    2026), carries the own token accounts the run knew, under solana.SYNC_KEY, so the
    classifier reads the same legs from the stored original without a provider (D192). A
    record whose token legs cannot be read anyway is a card, never a stopped sync. No token
    account's address leaves the run: never in a cursor, an event or a log.

    **Errors.** A JSON-RPC or transport failure, or a transaction the provider no longer
    serves (Q-O93), is `wallet.source_failed` with the HTTP status at most; a 429 throttles
    and a 401/403 refuses (SolanaRpc.call). The URL is never in an error, a log or the repr;
    `host` is its host name only."""

    rpc: SolanaRpc
    kind: ChainSourceKind = ChainSourceKind.SOLANA_RPC
    reconcile_scope: str = "address"
    reads_stored: bool = True  # services/chain/sync passes `stored`
    page_size: int = TRANSACTION_PAGE
    _runs: dict[str, _Run] = field(default_factory=dict, repr=False)

    @property
    def host(self) -> str:
        return self.rpc.source_host.lower()

    def transactions(
        self, address: str, cursor: str | None, stored: StoredRecords | None = None
    ) -> Page[ChainTx]:
        run = self._runs.get(address)
        if run is None:
            run = self._runs[address] = _Run(address, SourceCursor.parse(cursor), stored)
        items: list[ChainTx] = []
        try:
            if not run.ready:
                self._begin(run)
                run.ready = True
            while True:
                self._serve(run, items)
                if not run.pending or self._next(run, cursor) != cursor:
                    break
        except ChainSourceError as exc:
            if exc.code != E.WALLET_SYNC_DEFERRED:
                raise
        except SolanaRpcError as exc:
            raise _failed(exc) from None
        return Page[ChainTx](items=items, next_cursor=self._next(run, cursor))

    def balances(self, address: str) -> list[AssetBalance]:
        """The address's SOL (its main account and the rent in its token accounts) and each
        mint over its token accounts (D186 (2)); [] when the budget refused a call, so the
        sync reads them on the next run."""
        try:
            return self.rpc.balances(address)
        except ChainSourceError as exc:
            if exc.code == E.WALLET_SYNC_DEFERRED:
                return []
            raise
        except SolanaRpcError as exc:
            raise _failed(exc) from None

    # --- the listing ---------------------------------------------------------------------

    def _begin(self, run: _Run) -> None:
        """The run's height, then the address and its open token accounts from the cursor's
        slot (an open one an earlier run stored from where the cursor says it is complete to).
        After a cut run the other stored token accounts are swept as the run serves (_sweep),
        one at a time, never all at the start (Q-O162)."""
        if not run.height:
            run.height = self.rpc.finalized_slot()
        start = run.start
        self._list(run.address, run, start.slot, True)
        accounts, _ = self.rpc.token_accounts(run.address)
        opened = {a.pubkey for a in accounts}
        run.accounts.update(opened)
        earlier = self._stored(run) if start.split else set()
        run.accounts.update(earlier)
        run.sweep = start.index
        for pubkey in sorted(opened):
            if pubkey in earlier:
                self._list(pubkey, run, self._bound(run, pubkey), False)
            else:
                self._list(pubkey, run, start.slot, True)

    def _stored(self, run: _Run) -> set[str]:
        """The own token accounts the address's stored originals name: by a row's owner, or
        as the accounts a run kept beside a record (solana.SYNC_KEY). Read once per run."""
        self._order(run)
        return cast(set[str], run.stored)

    def _order(self, run: _Run) -> list[str]:
        """The stored token accounts in a fixed order: by first appearance across the stored
        originals as the sync hands them over (oldest run first, each record in its order),
        sorted within one record. A later run's original only adds at the end, so an index
        into this order written by one run names the same account in the next (Q-O162)."""
        if run.order is None:
            order: list[str] = []
            seen: set[str] = set()
            for record in run.loader() if run.loader is not None else ():
                names = owned_token_accounts(record, run.address)
                names.update(solana.recorded_token_accounts(record) or ())
                names.discard(run.address)
                for name in sorted(names - seen):
                    seen.add(name)
                    order.append(name)
            run.order, run.stored = order, seen
            run.index = {name: i for i, name in enumerate(order)}
        return run.order

    def _bound(self, run: _Run, pubkey: str) -> int | None:
        """Where a token account an earlier run stored is complete to at the run's start."""
        start = run.start
        if not start.split:
            return start.slot
        place = run.index[pubkey]
        if place < start.index:
            return start.prefix
        return start.at if place == start.index else start.closed

    def _sweep(self, run: _Run) -> str | None:
        """Q-O162: the stored token account whose transactions the run serves now. After a
        cut run they are listed one at a time in their fixed order, from where the cursor
        says each is complete to, the next one only once the one before has nothing left
        unserved; None once every one is swept (or the start was not a cut run's)."""
        if not run.start.split:
            return None
        order = self._order(run)
        while run.sweep < len(order):
            pubkey = order[run.sweep]
            self._list(pubkey, run, self._bound(run, pubkey), False)
            if any(s in run.pending for s in run.by_account.get(pubkey, ())):
                return pubkey
            run.sweep += 1
        return None

    def _list(self, account: str, run: _Run, since: int | None, open_side: bool) -> None:
        """The account's signatures at or after `since` (all of them for None) up to the
        run's height, paged back with `before` until an empty page or one that reaches below
        `since`. A listing the budget cuts is noted with its bound for the cursor."""
        if account in run.listed and _covers(run.listed[account], since):
            return
        if not open_side:
            run.closed_side.add(account)
        mine = run.by_account.setdefault(account, set())
        before: str | None = None
        try:
            while True:
                options: dict[str, JsonValue] = {
                    "limit": self.rpc.signature_page,
                    "commitment": COMMITMENT,
                }
                if before is not None:
                    options["before"] = before
                page = self.rpc.call("getSignaturesForAddress", [account, options])
                if not isinstance(page, list):
                    raise SolanaRpcError("parse")
                infos = [_signature_info(raw) for raw in page]
                for info in infos:
                    if info.slot > run.height or (since is not None and info.slot < since):
                        continue
                    if info.signature in run.served:
                        continue
                    mine.add(info.signature)
                    if info.signature not in run.pending:
                        run.pending[info.signature] = info
                        heapq.heappush(run.queue, (info.slot, info.signature))
                    if open_side:
                        run.open_side.add(info.signature)
                if not infos or (since is not None and infos[-1].slot < since):
                    break
                before = infos[-1].signature
        except ChainSourceError as exc:
            if exc.code == E.WALLET_SYNC_DEFERRED:
                run.cuts.append((open_side, since))
                run.cut_from[account] = since
            raise
        run.listed[account] = since

    # --- the page ------------------------------------------------------------------------

    def _serve(self, run: _Run, items: list[ChainTx]) -> None:
        address = run.address
        base = run.last[0] if run.last is not None else run.start.slot
        later = 0
        previous: int | None = None
        while (head := self._head(run)) is not None:
            slot, sig = head
            info = run.pending[sig]
            if later >= self.page_size and slot != previous:
                break
            record = self.rpc.transaction(info.signature, info.slot)
            when = self._when(record, info)
            # Both answers are in hand: only now does the signature leave `pending`, so a
            # budget that refuses either leaves it for the next run (the queue keeps it until
            # it comes to its head, then drops it as served).
            del run.pending[sig]
            run.served.add(sig)
            found = owned_token_accounts(record, address)
            run.accounts.update(found)
            kept = solana.with_token_accounts(record, run.accounts, when)
            items.append(self.rpc.chain_tx(kept, {address}, run.accounts, strict=False))
            position = (slot, sig)
            if run.last is None or position > run.last:
                run.last = position
            if slot != base:
                later += 1
            previous = slot
            for pubkey in sorted(found):
                if pubkey in run.listed:
                    continue
                earlier = self._stored(run)
                run.accounts.update(earlier)
                self._list(
                    pubkey, run, self._bound(run, pubkey) if pubkey in earlier else None, False
                )

    def _head(self, run: _Run) -> tuple[int, str] | None:
        """The next transaction to serve: while a sweep is under way the lowest unserved one
        of the stored token account it stands on, else the lowest one listed (None: none)."""
        swept = self._sweep(run)
        if swept is not None:
            return min((run.pending[s].slot, s) for s in run.by_account[swept] if s in run.pending)
        while run.queue:
            slot, sig = run.queue[0]
            if sig in run.pending:
                return slot, sig
            heapq.heappop(run.queue)  # served already (listed twice, or by the sweep)
        return None

    def _when(self, record: Mapping[str, JsonValue], info: SignatureInfo) -> int | None:
        """The block time of a record that carries none (an old slot's): the listing's, else
        getBlockTime's (a slot with none fails the run as the ledger's end, Q-O93)."""
        if isinstance(record.get("blockTime"), int):
            return None
        when = info.block_time or self.rpc.block_time(info.slot)
        return int(when.timestamp())

    def _next(self, run: _Run, cursor: str | None) -> str | None:
        """The cursor after this page (see SourceCursor and the class docstring)."""
        if not run.ready and run.last is None:  # the budget refused the run's start
            return cursor
        open_cut = [since for side, since in run.cuts if side]
        closed_cut = [since for side, since in run.cuts if not side]
        open_pending = [i.slot for s, i in run.pending.items() if s in run.open_side]
        closed_pending = [i.slot for s, i in run.pending.items() if s not in run.open_side]
        slot: int | None
        if open_cut:  # the address's or an open account's listing is unfinished
            slot = run.start.slot
        elif open_pending:
            slot = min(open_pending)
        elif run.last is not None:
            slot = run.last[0]
        else:
            slot = run.start.slot
        if slot is None:  # a first run with nothing to stand on yet
            return cursor
        if run.start.split:
            return self._next_sweep(run, slot)
        closed = _lowest(slot, *closed_pending, *closed_cut)
        if not run.complete and self._stored(run) - run.listed.keys():
            # Token accounts earlier runs stored and this run has not listed: one closed since
            # then is found only through the record that names it, maybe not reached yet.
            closed = _lowest(closed, run.start.low)
        signature = _signature(run, slot)
        if closed == slot:
            return SourceCursor(slot, signature).text()
        return SourceCursor(slot, signature, closed, True).text()

    def _complete_to(self, run: _Run, account: str) -> int | None:
        """Where a closed-side token account is complete to after this page: a cut listing's
        bound, else its lowest listed transaction still unserved, else past the height when
        all are served; one not listed in the run keeps where its start said (_bound)."""
        if account in run.cut_from:
            return run.cut_from[account]
        if account in run.listed:
            slots = [run.pending[s].slot for s in run.by_account[account] if s in run.pending]
            return min(slots) if slots else run.height + 1
        return self._bound(run, account)

    def _next_sweep(self, run: _Run, slot: int) -> str:
        """The cursor after a page of a run that started from a cut run's cursor (Q-O162).

        The stored token accounts the sweep has finished (listed, nothing left unserved)
        join the prefix, which is complete to the lowest of theirs; the one it stands on
        keeps its own bound (`at`) and the later ones and any found in this run the lowest
        of theirs (`closed`). A complete run that swept them all stands on the plain cursor,
        as a run that is not a cut run's does. The prefix is never below `slot`: when the
        address has been served past it, the prefix joins the accounts swept again from where
        it is complete to, so no stored account is ever taken to be complete where it is not."""
        order = self._order(run)
        signature = _signature(run, slot)
        if run.complete and run.sweep >= len(order):
            return SourceCursor(slot, signature).text()
        ends = [self._complete_to(run, a) for a in order]
        index = min(run.start.index, len(order))
        while index < len(order) and order[index] in run.listed and ends[index] == run.height + 1:
            index += 1
        found = [self._complete_to(run, a) for a in run.closed_side if a not in run.index]
        closed = _lowest(slot, *ends[index + 1 :], *found)
        at = ends[index] if index < len(order) else closed
        prefix = _lowest(*ends[:index]) if index else None
        if index and (prefix is None or prefix < slot):  # the address was served past it
            closed = _lowest(closed, at, prefix)
            index, at, prefix = 0, closed, None
        if closed == slot and at is not None and at >= slot:
            return SourceCursor(slot, signature).text()
        return SourceCursor(slot, signature, closed, True, index, at, prefix).text()


def _signature(run: _Run, slot: int) -> str:
    """The highest signature served at the cursor's slot, empty while one there waits."""
    unserved = any(i.slot <= slot for i in run.pending.values())
    return run.last[1] if run.last and run.last[0] == slot and not unserved else ""


def _failed(exc: SolanaRpcError) -> ChainSourceError:
    """A provider's failure as the sync's code: its HTTP status at most, never its message."""
    status = exc.code if exc.kind == "HTTPError" else None
    return ChainSourceError(E.WALLET_SOURCE_FAILED, status)


def make_source(
    row: ChainRegistryRow,
    settings: Settings,
    budget: RequestBudget,
    *,
    post: Post = urllib_post,
    sleep: Callable[[float], None] = time.sleep,
) -> SolanaSource:
    """base.factory_for's factory: the configured URL (`HAUSBUCH_SOLANA_RPC_URL`, the public
    endpoint when unset) at `HAUSBUCH_SOLANA_RPC_RPS`, every request asking the run's budget.
    An empty URL is `wallet.key_missing`: no row, the other addresses of the run go on.
    `post` and `sleep` are a test's fake transport."""
    url = setting(settings, "solana_rpc_url")
    if url is None:
        raise ChainSourceError(E.WALLET_KEY_MISSING)
    rps = setting(settings, "solana_rpc_rps")
    rpc = SolanaRpc(
        url=SecretStr(url),
        rps=float(rps or DEFAULT_RPS),
        post=post,
        sleep=sleep,
        take=budget.take,
    )
    return SolanaSource(rpc=rpc)
