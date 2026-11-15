"""Bitcoin over the Esplora API (docs/phases/phase-7.md D181 (a), D185, D192).

Esplora is served by mempool.space and Blockstream alike and can be self-hosted, so the base
URL is a setting (`HAUSBUCH_ESPLORA_URL`, default mempool.space) and Blockstream is tried when
the configured provider fails; the same code reads either (D192). No key.

- `GET {base}/address/{a}`: the chain balance, `chain_stats.funded_txo_sum − spent_txo_sum`
  (D185 (3)), at the tip;
- `GET {base}/blocks/tip/height`: the tip, as plain text;
- `GET {base}/address/{a}/txs/chain[/{last_seen_txid}]`: the confirmed history, newest first,
  25 per page; the next page starts after the last txid of a full one;
- `GET {base}/tx/{txid}`: one transaction, with `vin[].prevout`, `vout[]`, `fee`, `status`.

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
from hausbuch.rules.chain.utxo import VIN
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
    `pages` keeps every record read, in the protocol's own shape, for the sync's stored
    original (D182 (3)); `host` names the provider that answered last (`wallet_sync.
    source_host`, never a path or key)."""

    base_url: str = MEMPOOL_URL
    fallback_url: str | None = BLOCKSTREAM_URL
    fetch: Fetch = field(default=urllib_fetch, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    kind: ChainSourceKind = ChainSourceKind.BITCOIN_ESPLORA
    host: str | None = None
    pages: list[JsonValue] = field(default_factory=list, repr=False)
    _calls: int = 0

    def _urls(self) -> list[str]:
        urls = [self.base_url.rstrip("/")]
        if self.fallback_url and self.fallback_url.rstrip("/") != urls[0]:
            urls.append(self.fallback_url.rstrip("/"))
        return urls

    def _get(self, path: str, parse: Callable[[bytes], object]) -> object:
        """One request, paced, on the configured URL and then on the fallback. A 429 stops
        at once; a refusal (401, 403) tries the next host and wins over other failures when
        no host answers."""
        failure: ChainSourceError | None = None
        refused: ChainSourceError | None = None
        for base in self._urls():
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


def from_settings(settings: object, fetch: Fetch = urllib_fetch) -> BitcoinEsplora:
    """The source with the configured base URL (`HAUSBUCH_ESPLORA_URL`, step 1's setting,
    read defensively until it is on main) and Blockstream as the fallback."""
    base = getattr(settings, "esplora_url", None) or MEMPOOL_URL
    return BitcoinEsplora(base_url=str(base), fetch=fetch)
