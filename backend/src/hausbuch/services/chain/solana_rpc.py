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
from collections.abc import Callable, Collection, Mapping, Sequence
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
    """A SolanaSource cursor, `<slot>:<signature>[;<back>;<upto>]` (≤ 200 characters).

    `slot` is where the next run lists from, inclusive (listings of several accounts have no
    order inside one slot); `signature` the highest transaction served there, so the cursor
    changes while a run moves on and comes back unchanged at the tip. `back` and `upto` are
    an unfinished discovery the budget cut (see SolanaSource): a token account named by a
    record at a slot ≤ `upto` is listed from `back` (empty: from its first signature), not
    from `slot`."""

    slot: int | None = None
    signature: str = ""
    back: int | None = None
    upto: int | None = None

    @classmethod
    def parse(cls, text: str | None) -> "SourceCursor":
        """A cursor this source did not write starts over (None): the history is then read
        again and the movement hashes keep it once."""
        head, *extra = (text or "").split(";")
        slot, _, signature = head.partition(":")
        if not slot.isdigit():
            return cls()
        if len(extra) == 2 and extra[1].isdigit() and (not extra[0] or extra[0].isdigit()):
            back = int(extra[0]) if extra[0] else None
            return cls(int(slot), signature, back, int(extra[1]))
        return cls(int(slot), signature)

    def text(self) -> str:
        head = f"{self.slot}:{self.signature}"
        if self.upto is None:
            return head
        return f"{head};{'' if self.back is None else self.back};{self.upto}"


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


@dataclass
class _Run:
    """One address's listing inside one source (a source is built per address run)."""

    start: SourceCursor
    ready: bool = False  # the address and its open token accounts are listed
    accounts: set[str] = field(default_factory=set)  # own token accounts known so far
    listed: dict[str, int | None] = field(default_factory=dict)  # account -> listed from
    pending: dict[str, SignatureInfo] = field(default_factory=dict)  # listed, not served
    served: set[str] = field(default_factory=set)
    last: tuple[int, str] | None = None  # the highest (slot, signature) served
    cut: tuple[int, int | None] | None = None  # a discovery the budget cut: (slot, from)

    def bound(self, slot: int) -> int | None:
        """The slot a token account named by a record at `slot` is listed from."""
        upto = self.start.upto
        if upto is not None and slot <= upto:
            return self.start.back
        return self.start.slot


@dataclass
class SolanaSource:
    """The ChainSource the sync reads Solana through (services/chain/base.py; D181 (d), D182).

    **What is listed.** `getSignaturesForAddress` lists only the transactions whose account
    keys hold the address, so a token credited to an own token account names the token
    account alone (solana_tx.yaml `spl_in`). A run therefore lists the address, each token
    account `getTokenAccountsByOwner` gives it now, and each token account a record read in
    the run names as the address's (a closed one: its opening and closing transactions hold
    the owner), merged by signature and read oldest first by (slot, signature).

    **The cursor** (SourceCursor) stands on the highest transaction served; the next run lists
    again from that slot, inclusive, so the transactions of that slot are read once more and
    the movement hashes store them once. A page never splits a slot, and the slot it starts
    on does not count toward its size. When something listed is still unserved below the
    highest slot served (a token account found late, or the budget spent inside a slot), the
    cursor stands on that slot instead, so nothing listed is skipped. At the tip it comes
    back unchanged, which ends the sync's loop.

    **The budget.** Every request asks the run's RequestBudget first; a „no“ ends the page
    where it stood (the sync then marks the run deferred), never an error. When the budget
    cuts the listing of a token account just found, the cursor stands on the record that
    named it and carries `back`/`upto`, so the next run lists that account from where this
    run listed, not from the cursor: a daily budget that always ends at the same place still
    moves on, and no older transaction of the account is lost.

    **Per address.** Each own address is its own run, read and reconciled alone (the
    `transfers` of a ChainTx are the address's own), as solana.classify_tx reads it. A stake
    account the wallet registers is an address of its own; its inflation rewards are not
    read yet (§15.1).

    **Stored as read.** `raw` is the provider's record; only a record with a token balance row
    without an `owner` carries the own token accounts the run knew, under solana.SYNC_KEY, so
    the classifier reads the same legs from the stored original without a provider (D192). A
    record whose token legs cannot be read anyway is a card, never a stopped sync.

    **Errors.** A JSON-RPC or transport failure, or a transaction the provider no longer
    serves (Q-O93), is `wallet.source_failed` with the HTTP status at most; a 429 throttles
    and a 401/403 refuses (SolanaRpc.call). The URL is never in an error, a log or the repr;
    `host` is its host name only."""

    rpc: SolanaRpc
    kind: ChainSourceKind = ChainSourceKind.SOLANA_RPC
    reconcile_scope: str = "address"
    page_size: int = TRANSACTION_PAGE
    _runs: dict[str, _Run] = field(default_factory=dict, repr=False)

    @property
    def host(self) -> str:
        return self.rpc.source_host.lower()

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]:
        run = self._runs.get(address)
        if run is None:
            run = self._runs[address] = _Run(start=SourceCursor.parse(cursor))
        items: list[ChainTx] = []
        try:
            if not run.ready:
                self._list_open(address, run)
                run.ready = True
            self._serve(address, run, items)
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

    def _list_open(self, address: str, run: _Run) -> None:
        self._list(address, run, run.start.slot)
        accounts, _ = self.rpc.token_accounts(address)
        run.accounts.update(a.pubkey for a in accounts)
        for pubkey in sorted(run.accounts):
            self._list(pubkey, run, run.start.slot)

    def _list(self, account: str, run: _Run, since: int | None) -> None:
        """The account's signatures at or after `since` (all of them for None), paged back
        with `before` until an empty page or one that reaches below `since`."""
        if account in run.listed and _covers(run.listed[account], since):
            return
        before: str | None = None
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
                if since is not None and info.slot < since:
                    continue
                if info.signature not in run.served:
                    run.pending.setdefault(info.signature, info)
            if not infos or (since is not None and infos[-1].slot < since):
                break
            before = infos[-1].signature
        run.listed[account] = since

    # --- the page ------------------------------------------------------------------------

    def _serve(self, address: str, run: _Run, items: list[ChainTx]) -> None:
        order = sorted(run.pending.values(), key=lambda s: (s.slot, s.signature))
        base = run.last[0] if run.last is not None else run.start.slot
        later = 0
        for n, info in enumerate(order):
            if later >= self.page_size and info.slot != order[n - 1].slot:
                break
            record = self.rpc.transaction(info.signature, info.slot)
            run.pending.pop(info.signature, None)
            run.served.add(info.signature)
            found = owned_token_accounts(record, address)
            run.accounts.update(found)
            stored = solana.with_token_accounts(record, run.accounts)
            items.append(self.rpc.chain_tx(stored, {address}, run.accounts, strict=False))
            position = (info.slot, info.signature)
            if run.last is None or position > run.last:
                run.last = position
            if info.slot != base:
                later += 1
            since = run.bound(info.slot)
            run.cut = (info.slot, since)
            for pubkey in sorted(found):
                self._list(pubkey, run, since)
            run.cut = None

    def _next(self, run: _Run, cursor: str | None) -> str | None:
        """The cursor after this page (see the class docstring)."""
        if run.last is None:
            return cursor
        slot, signature = run.last
        unserved = [s.slot for s in run.pending.values()]
        if run.cut is not None:
            unserved.append(run.cut[0])
        low = min(unserved, default=None)
        at, sig = (low, "") if low is not None and low <= slot else (slot, signature)
        owed: list[tuple[int | None, int]] = []
        inherited = run.start.upto
        if inherited is not None and at <= inherited and (run.pending or run.cut is not None):
            owed.append((run.start.back, inherited))
        if run.cut is not None:
            owed.append((run.cut[1], run.cut[0]))
        if not owed:
            return SourceCursor(at, sig).text()
        backs = [b for b, _ in owed]
        back = None if None in backs else min(b for b in backs if b is not None)
        return SourceCursor(at, sig, back, max(u for _, u in owed)).text()


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
