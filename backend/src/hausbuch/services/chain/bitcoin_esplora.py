"""Bitcoin over the Esplora API (docs/phases/phase-7.md D181 (a), D185, D192).

Esplora is served by mempool.space and Blockstream alike and can be self-hosted, so the base
URL is a setting (`HAUSBUCH_ESPLORA_URL`, default mempool.space) and Blockstream is tried when
the configured provider fails; the same code reads either (D192). No key.

- `GET {base}/address/{a}`: the chain balance, `chain_stats.funded_txo_sum − spent_txo_sum`
  (D185 (3)), at the tip;
- `GET {base}/blocks/tip/height`: the tip, as plain text;
- `GET {base}/address/{a}/txs/chain[/{last_seen_txid}]`: the confirmed history, newest first,
  25 per page; the next page starts after the last txid of a full one;
- `GET {base}/tx/{txid}`: one transaction, with `vin[].prevout`, `vout[]`, `fee`, `status`;
- `GET {base}/tx/{txid}/status`: whether the host holds the transaction as confirmed.

`to_chain_tx` turns one Esplora transaction record into the chain-neutral `ChainTx` (D181)
and is a module function, so a replay over the stored originals needs no source (D182 (3)).
The UTXO accounting is rules/chain/utxo (D185).

Requests are paced one a second through an injectable `sleep`. A failure is a
ChainSourceError carrying the code and the HTTP status only, never the URL (it names the
address) or the body. A 429 is `wallet.source_throttled` at once, without the fallback (the
sync resumes the next day from its cursor; another provider asked at once would only add
load). A 401 or 403 tries the fallback (D181 (a) amended 15 Nov 2026: with no key, a refusal
is an IP block at that host, which must not stop Bitcoin while the other answers); when no
host answers, it is `wallet.source_refused` if any host refused (the one a person can act
on: the runbook step for changing the URL), else `wallet.source_failed`. A transport
failure, any other status or an answer the parser cannot read tries the fallback too.
Everything a record's content can cause while it is read is an unreadable answer, never an
exception of its own (the record's content never reaches the error).
"""

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from pydantic import JsonValue

from hausbuch.rules import errors
from hausbuch.rules.chain.registry import ChainRegistryRow
from hausbuch.rules.chain.utxo import VIN, balance_at
from hausbuch.schemas.chain import (
    AssetBalance,
    AssetKey,
    Chain,
    ChainSourceKind,
    ChainTx,
    Page,
    RawTransfer,
)
from hausbuch.services.chain.base import ChainSourceError, RequestBudget
from hausbuch.services.prices.http import Fetch, SourceError, get, urllib_fetch

MEMPOOL_URL = "https://mempool.space/api"
BLOCKSTREAM_URL = "https://blockstream.info/api"
PAGE_SIZE = 25  # Esplora's page of /txs/chain
MIN_INTERVAL_S = 1.0  # one request a second (D181 (a): the limits are undisclosed)
# A failure that is neither a 429 nor a refusal: no code of its own in rules/errors.py yet
# (phase 7 step 4 report), so the sync can tell it apart from the two that are.
SOURCE_FAILED = "wallet.source_failed"
_THROTTLED = errors.WALLET_SOURCE_THROTTLED
_REFUSED = errors.WALLET_SOURCE_REFUSED

BTC = AssetKey(chain=Chain.BITCOIN)
_TXID = re.compile(r"[0-9a-f]{64}")  # Esplora's txid: 32 bytes, lower-case hex


class _Unreadable(Exception):
    """An answer the parser cannot read (never its content)."""


class BudgetSpent(Exception):
    """The run's request budget refused the next request (D182 (1)); nothing was sent."""


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _Unreadable
    return value


def _address(record: object) -> str | None:
    if not isinstance(record, Mapping):
        return None
    address = record.get("scriptpubkey_address")
    return address if isinstance(address, str) and address else None


def to_chain_tx(record: Mapping[str, JsonValue], address: str | None = None) -> ChainTx:
    """One Esplora transaction record as a ChainTx: an input per `vin` (index `vin:<n>`, from
    the spent output's address, None for a coinbase), an output per `vout` (index `<n>`, to
    its address, None without one); `fee` is the record's fee when `address` (the address the
    sync asked about) spent one of the inputs, else None (ChainTx.fee: paid by the own
    address; utxo_net computes the fee from the inputs and outputs itself). Raises
    _Unreadable for a record that is not a confirmed transaction whose inputs equal its
    outputs plus its fee, and for anything else its content can cause while it is read (a
    txid that is not 64 lower-case hex characters, a block time no date can hold, a value
    the ChainTx refuses)."""
    try:
        txid = record["txid"]
        status = record["status"]
        vin = record["vin"]
        vout = record["vout"]
        fee = _int(record.get("fee", 0))
        if not isinstance(txid, str) or not _TXID.fullmatch(txid):
            raise _Unreadable
        if not isinstance(status, Mapping):
            raise _Unreadable
        if status.get("confirmed") is not True:
            raise _Unreadable
        block = _int(status["block_height"])
        at = datetime.fromtimestamp(_int(status["block_time"]), tz=UTC)
        if not isinstance(vin, list) or not isinstance(vout, list) or not vout:
            raise _Unreadable
        transfers: list[RawTransfer] = []
        coinbase = False
        for n, item in enumerate(vin):
            if not isinstance(item, Mapping):
                raise _Unreadable
            if item.get("is_coinbase") is True:
                coinbase = True
                transfers.append(RawTransfer(asset_key=BTC, units=0, index=f"{VIN}{n}"))
                continue
            prevout = item["prevout"]
            if not isinstance(prevout, Mapping):
                raise _Unreadable
            transfers.append(
                RawTransfer(
                    asset_key=BTC,
                    from_addr=_address(prevout),
                    units=_int(prevout["value"]),
                    index=f"{VIN}{n}",
                )
            )
        for n, item in enumerate(vout):
            if not isinstance(item, Mapping):
                raise _Unreadable
            transfers.append(
                RawTransfer(
                    asset_key=BTC, to_addr=_address(item), units=_int(item["value"]), index=str(n)
                )
            )
        spent = sum(t.units for t in transfers if t.index.startswith(VIN))
        paid = sum(t.units for t in transfers if not t.index.startswith(VIN))
        if not coinbase and spent != paid + fee:
            raise _Unreadable
        paid_by_address = address is not None and any(
            t.from_addr == address for t in transfers if t.index.startswith(VIN)
        )
        return ChainTx(
            hash=txid,
            block=block,
            occurred_at=at,
            status="ok",
            fee=(BTC, fee) if paid_by_address and fee else None,
            transfers=transfers,
            raw=dict(record),
        )
    # ValueError covers pydantic's ValidationError; OSError and OverflowError a block time
    # beyond what a date holds.
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        raise _Unreadable from None


def balance_units(record: Mapping[str, JsonValue]) -> int:
    """D185 (3): `chain_stats.funded_txo_sum − spent_txo_sum` of an /address answer."""
    try:
        stats = record["chain_stats"]
        if not isinstance(stats, Mapping):
            raise _Unreadable
        units = _int(stats["funded_txo_sum"]) - _int(stats["spent_txo_sum"])
    except (KeyError, TypeError):
        raise _Unreadable from None
    if units < 0:
        raise _Unreadable
    return units


@dataclass
class BitcoinEsplora:
    """The `bitcoin_esplora` ChainSource. `base_url` is the setting's (ending in `/api`, no
    slash after it); `fallback_url` is tried when it fails, unless it is the same URL.
    `more_urls` are tried after it, in order (`HAUSBUCH_ESPLORA_URL` takes several).
    `pages` keeps every record read, in the protocol's own shape, for the sync's stored
    original (D182 (3)); `host` names the provider that answered last (`wallet_sync.
    source_host`, never a path or key). `budget`, when set, is asked before every request,
    a fallback's included; a refusal raises BudgetSpent before anything is sent.

    `pin` (the sync's source sets it): the first host that answers serves every later request
    of this client, so a run's tip and history come from one host's index (review of PR #387:
    a tip from one host and pages from a host behind it had read a block as empty for good).
    `drop_pinned` hands the run to the next host; nothing goes back to a dropped one."""

    base_url: str = MEMPOOL_URL
    fallback_url: str | None = BLOCKSTREAM_URL
    fetch: Fetch = field(default=urllib_fetch, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    kind: ChainSourceKind = ChainSourceKind.BITCOIN_ESPLORA
    host: str | None = None
    pages: list[JsonValue] = field(default_factory=list, repr=False)
    more_urls: tuple[str, ...] = ()
    budget: RequestBudget | None = field(default=None, repr=False)
    pin: bool = False
    _calls: int = 0
    _pinned: str | None = None
    _dropped: set[str] = field(default_factory=set, repr=False)

    def _all_urls(self) -> list[str]:
        urls = [self.base_url.rstrip("/")]
        for url in (self.fallback_url, *self.more_urls):
            if url and url.rstrip("/") not in urls:
                urls.append(url.rstrip("/"))
        return urls

    def _urls(self) -> list[str]:
        if self._pinned is not None:
            return [self._pinned]
        return [url for url in self._all_urls() if url not in self._dropped]

    def drop_pinned(self) -> bool:
        """Leave the pinned host for the next one not dropped yet; False (nothing changed)
        when no host is pinned or none is left."""
        if self._pinned is None:
            return False
        left = [u for u in self._all_urls() if u not in self._dropped and u != self._pinned]
        if not left:
            return False
        self._dropped.add(self._pinned)
        self._pinned = None
        return True

    def _get(self, path: str, parse: Callable[[bytes], object]) -> object:
        """One request, paced, on the configured URL and then on the fallback. A 429 stops
        at once; a refusal (401, 403) tries the next host and wins over other failures when
        no host answers."""
        failure: ChainSourceError | None = None
        refused: ChainSourceError | None = None
        for base in self._urls():
            if self.budget is not None and not self.budget.take():
                raise BudgetSpent
            if self._calls:
                self.sleep(MIN_INTERVAL_S)
            self._calls += 1
            try:
                body = get(self.fetch, base + path, {"Accept": "application/json"})
            except SourceError as exc:
                if exc.status == 429:
                    raise ChainSourceError(_THROTTLED, 429) from None
                if exc.status in (401, 403):
                    refused = refused or ChainSourceError(_REFUSED, exc.status)
                else:
                    failure = ChainSourceError(SOURCE_FAILED, exc.status)
                continue
            try:
                value = parse(body)
            except _Unreadable:
                failure = ChainSourceError(SOURCE_FAILED)
                continue
            self.host = urlsplit(base).hostname
            if self.pin:
                self._pinned = base
            return value
        error = refused or failure
        assert error is not None
        raise error

    def tip(self) -> int:
        """The chain's tip height (plain text)."""

        def parse(body: bytes) -> int:
            try:
                return _int(int(body.decode("ascii").strip()))
            except (UnicodeDecodeError, ValueError):
                raise _Unreadable from None

        value = self._get("/blocks/tip/height", parse)
        assert isinstance(value, int)
        return value

    def balances(self, address: str) -> list[AssetBalance]:
        """The address's BTC at the tip, and the tip (read first, so the balance is at least
        as new as the block it names)."""
        at_block = self.tip()

        def parse(body: bytes) -> tuple[Mapping[str, JsonValue], int]:
            record = _json_object(body)
            return record, balance_units(record)

        answer = self._get(f"/address/{address}", parse)
        assert isinstance(answer, tuple)
        record, units = answer
        self.pages.append(dict(record))
        return [AssetBalance(asset_key=BTC, units=units, at_block=at_block)]

    def address_units(self, address: str) -> int:
        """The address's BTC at the tip, in satoshi (one request, no tip read)."""

        def parse(body: bytes) -> int:
            return balance_units(_json_object(body))

        value = self._get(f"/address/{address}", parse)
        assert isinstance(value, int)
        return value

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]:
        """One page of the address's confirmed history, newest first: `cursor` is the last
        txid of the page before (None: the newest page); `next_cursor` is this page's last
        txid when the page is full, else None (the oldest page was read)."""
        path = f"/address/{address}/txs/chain"
        if cursor is not None:
            path += f"/{cursor}"

        def parse(body: bytes) -> list[ChainTx]:
            records = _json(body)
            if not isinstance(records, list):
                raise _Unreadable
            txs = []
            for record in records:
                if not isinstance(record, Mapping):
                    raise _Unreadable
                txs.append(to_chain_tx(record, address))
            return txs

        items = self._get(path, parse)
        assert isinstance(items, list)
        self.pages.extend(tx.raw for tx in items)
        full = len(items) >= PAGE_SIZE
        return Page[ChainTx](items=items, next_cursor=items[-1].hash if full else None)

    def confirmed(self, txid: str) -> bool:
        """Whether the host holds `txid` as a confirmed transaction: True only on a readable
        yes; a not-found, an unreadable answer or a failure is False (a 429 still raises)."""

        def parse(body: bytes) -> bool:
            return _json_object(body).get("confirmed") is True

        try:
            value = self._get(f"/tx/{txid}/status", parse)
        except ChainSourceError as exc:
            if exc.code == _THROTTLED:
                raise
            return False
        return value is True

    def transaction(self, txid: str, address: str | None = None) -> ChainTx:
        """One transaction by its id (a confirmed one; an unconfirmed one is unreadable)."""

        def parse(body: bytes) -> ChainTx:
            return to_chain_tx(_json_object(body), address)

        tx = self._get(f"/tx/{txid}", parse)
        assert isinstance(tx, ChainTx)
        self.pages.append(tx.raw)
        return tx


def _json(body: bytes) -> object:
    try:
        return json.loads(body)
    except ValueError:
        raise _Unreadable from None


def _json_object(body: bytes) -> Mapping[str, JsonValue]:
    value = _json(body)
    if not isinstance(value, Mapping):
        raise _Unreadable
    return value


def _configured_urls(settings: object) -> tuple[str, ...]:
    """The Esplora base URLs in order: `Settings.esplora_urls` (`HAUSBUCH_ESPLORA_URL`, one
    URL or several comma-separated, step 1), else a single `esplora_url` (this module's first
    reading of the setting, before step 1 named it), else mempool.space then Blockstream."""
    urls = getattr(settings, "esplora_urls", None)
    if urls:
        return tuple(str(u) for u in urls)
    single = getattr(settings, "esplora_url", None)
    if single:
        return (str(single), BLOCKSTREAM_URL)
    return (MEMPOOL_URL, BLOCKSTREAM_URL)


def from_settings(settings: object, fetch: Fetch = urllib_fetch) -> BitcoinEsplora:
    """The client over the configured base URLs: the first, then the others as fallbacks."""
    urls = _configured_urls(settings)
    return BitcoinEsplora(
        base_url=urls[0],
        fallback_url=urls[1] if len(urls) > 1 else None,
        more_urls=urls[2:],
        fetch=fetch,
    )


# --- The ChainSource the sync calls (step 4 wiring) -----------------------------------------


@dataclass(frozen=True, slots=True)
class _Cursor:
    """The sync's cursor (D182 (1): Bitcoin's is a block and a txid), under 200 characters.

    Esplora pages an address's history newest first, from the last txid of the page before,
    so its paging position only walks back in time and cannot be where the next run starts.
    The cursor holds instead `done`, the height through which every transaction of the
    address is read (-1: none yet), and while a walk is in progress the height it reads up to
    (`window`, a final height, D182 (4)) and the last txid of the page read last:

    - `"<done>"` between runs: the next run walks back from the newest page down to `done`
      and keeps the transactions in (done, its final height];
    - `"<done>:<window>:<txid>"` mid-walk (a run the budget deferred): the next run continues
      that window from the txid, then walks a new one up to its own final height."""

    done: int
    window: int | None = None
    txid: str | None = None

    @classmethod
    def parse(cls, text: str | None) -> "_Cursor":
        if text is None:
            return cls(-1)
        parts = text.split(":")
        try:
            if len(parts) == 1:
                return cls(int(parts[0]))
            if len(parts) == 3 and _TXID.fullmatch(parts[2]):
                return cls(int(parts[0]), int(parts[1]), parts[2])
        except ValueError:
            pass
        raise ChainSourceError(SOURCE_FAILED)  # not a cursor this source wrote

    def text(self) -> str:
        if self.window is None:
            return str(self.done)
        return f"{self.done}:{self.window}:{self.txid}"


@dataclass
class EsploraSource:
    """The `bitcoin_esplora` ChainSource of services/chain/base.py over a BitcoinEsplora
    client that holds the run's budget:

    - `reconcile_scope = "account"`: a spend's movements sit on its first input's address
      while its change lands on another own address (D185 (3), D186 (2));
    - the tip is read once (a source is built per address run), and no transaction above
      `tip − confirmations_final` (Bitcoin 6, D182 (4)) is returned;
    - `balances(a)` is the address's balance at that same height: Esplora's tip balance with
      the transactions of later blocks backed out (`utxo.balance_at`), so every own address
      of the account is read to the same block. /address is read before the newest history
      page; a block found between the two reads makes that run's figure wrong by that block's
      transaction, and the next run reads it right again;
    - one host serves the run (the client is pinned): the window and every page of a walk
      come from the index that answered the tip. A host whose final height is below what an
      earlier run read, or whose newest page fails after its tip, hands the run to the next
      host with the tip read again there; when no host has reached what is stored, the
      balance is refused (a failed run, never a false mismatch);
    - an anchored page that comes back empty is the end of the walk only when the same host
      confirms the anchor; a host that does not know it (one behind, or another than the one
      that wrote the cursor) restarts the walk from its newest page down to `done`, so no
      transaction between the anchor and `done` is skipped (review of PR #387). Movements
      already stored are read again and kept once by their hash (D182 (2));
    - a request the budget refuses ends the page where it stands (the sync then defers)."""

    client: BitcoinEsplora
    confirmations_final: int = 6
    kind: ChainSourceKind = ChainSourceKind.BITCOIN_ESPLORA
    reconcile_scope: str = "account"
    _final: int | None = None
    _floor: int = -1  # the highest height a cursor of this run said was read

    def _fresh(self, address: str, done: int) -> tuple[int, Page[ChainTx] | None]:
        """(the window, the newest page) of a new walk from one host; no page (and no
        request) when nothing new is final."""
        while True:
            window = self.final_height()
            if window < done and self.client.drop_pinned():
                self._final = None  # behind what is stored: the next host's tip
                continue
            if done >= window:
                return window, None
            try:
                return window, self.client.transactions(address, None)
            except ChainSourceError as exc:
                if exc.code == _THROTTLED or not self.client.drop_pinned():
                    raise
                self._final = None  # the history failed after the tip: the next host's pair

    @property
    def host(self) -> str | None:
        """The provider's host name: the one that answered last, else the configured one (the
        sync records it before the first request)."""
        return self.client.host or urlsplit(self.client.base_url).hostname

    def final_height(self) -> int:
        if self._final is None:
            self._final = max(self.client.tip() - self.confirmations_final, -1)
        return self._final

    def transactions(self, address: str, cursor: str | None) -> Page[ChainTx]:
        state = _Cursor.parse(cursor)
        self._floor = max(self._floor, state.done)
        try:
            if state.window is None:
                window, newest = self._fresh(address, state.done)
                if newest is None:  # nothing new is final: no request
                    return Page[ChainTx](items=[], next_cursor=cursor)
                page = newest
            else:
                window = state.window
                page = self.client.transactions(address, state.txid)
                if not page.items and not self.client.confirmed(state.txid):
                    # not the end: this host does not know the anchor; walk again from the top
                    return Page[ChainTx](items=[], next_cursor=_Cursor(state.done).text())
        except BudgetSpent:
            return Page[ChainTx](items=[], next_cursor=cursor)
        items = [tx for tx in page.items if state.done < tx.block <= window]
        reached = any(tx.block <= state.done for tx in page.items)
        if page.next_cursor is None or reached:
            following = _Cursor(window)
        else:
            following = _Cursor(state.done, window, page.next_cursor)
        return Page[ChainTx](items=items, next_cursor=following.text())

    def balances(self, address: str) -> list[AssetBalance]:
        try:
            final = self.final_height()
            if final < self._floor:  # no host reached what is stored: no figure, no mismatch
                raise ChainSourceError(SOURCE_FAILED)
            units = self.client.address_units(address)
            later: list[ChainTx] = []
            after: str | None = None
            while True:
                page = self.client.transactions(address, after)
                newer = [tx for tx in page.items if tx.block > final]
                later.extend(newer)
                if page.next_cursor is None or len(newer) < len(page.items):
                    break
                after = page.next_cursor
        except BudgetSpent:
            return []
        units = balance_at(units, later, address, final)
        return [AssetBalance(asset_key=BTC, units=units, at_block=max(final, 0))]


def make_source(
    row: ChainRegistryRow,
    settings: object,
    budget: RequestBudget,
    *,
    fetch: Fetch = urllib_fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> EsploraSource:
    """services/chain/base.factory_for's factory: the configured Esplora URLs (no key, so
    never `wallet.key_missing`), the registry's finality and the run's budget. `fetch` and
    `sleep` are a test's transport and clock."""
    client = from_settings(settings, fetch)
    client.sleep = sleep
    client.budget = budget
    client.pin = True
    return EsploraSource(client=client, confirmations_final=row.confirmations_final)
