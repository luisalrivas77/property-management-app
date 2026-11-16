"""Shapes of the phase 7 YAML rule fixtures (docs/phases/phase-7.md §15).

Same contract as fixture_models_phase6.py: loading a fixture through these models is itself a
test (tests/test_fixtures_valid.py), so a fixture that does not match the spec schemas fails
before any rule exists. Every rule of phase 7 is a stub in step 0 (NotImplementedError), so
each case a rule will replay carries `pending: "<the step that builds it>"`; the rule tests of
those steps drop the marker. Expectations are recomputed on load where the arithmetic is
small, by the rule written once more here:

- chains: the registry rows against rules/chain/registry, Binance's network names, the
  Bitcoin and Solana address formats (bech32, base58check, base58 of 32 bytes) and the mask;
- decoders: the rows through DecoderRow, the refused files through rules/chain/decoders.parse,
  the override order of D191;
- movement_hash: D182 (2)'s SHA-256;
- btc_utxo: D185's net movement, fee and change from the Esplora record, the balance;
- evm_tx: the fee as gasUsed × gasPrice, the units as the record's integer value;
- solana_tx: D181 (d)'s deltas from the pre/post balances (the own account plus its token
  accounts, the fee added back when the own address paid it);
- swap_group: D188 (4)'s valuation (the received asset's price first);
- spam: D187 (1)–(4);
- chain_transfers: D184's windows, amounts and the hash;
- wallet_reconcile: D186 (2), (3);
- lots: D188 (1)'s FIFO; holding_period: D188 (2), (3); holdings_statement: the Berlin day;
- sync_window_chain: D182 (1), (4).

Every interpretation step 0 had to make is in the fixture file's header, and listed in
docs/phases/phase-7.md §15.1.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo

import yaml
from fixture_models import Lenient
from pydantic import Field, JsonValue, ValidationError, model_validator

from hausbuch.rules import errors as E
from hausbuch.rules.chain import decoders as decoder_rules
from hausbuch.rules.chain.registry import registry
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.allocation import HMAC_PATTERN
from hausbuch.schemas.chain import (
    Chain,
    ChainFamily,
    ChainSourceKind,
    ContractId,
    DecoderKind,
    DecoderRow,
    GroupKind,
    GroupStatus,
    LotSource,
    TokenSymbol,
)
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.household import (
    AssetSymbol,
    MovementKind,
    PriceEur,
    TransferLinkKind,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phase7"
BERLIN = ZoneInfo("Europe/Berlin")
_CENT = Decimal("0.01")
_UNIT_COST = Decimal("0.00000001")


@cache
def raw(name: str) -> dict:
    with (FIXTURES / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class _Fixture(Schema):
    ids: dict[str, str] = Field(default_factory=dict)
    bases: dict[str, object] = Field(default_factory=dict)  # YAML anchors merged into cases


class _Case(Lenient):
    # The step that builds the rule this case replays; None for a case checked on load only.
    pending: str | None = None


Hmac = str  # checked against HMAC_PATTERN where it appears


def _hmac(value: str) -> str:
    import re

    if not re.fullmatch(HMAC_PATTERN, value):
        raise ValueError(f"{value!r} is no placeholder HMAC of 64 hex characters")
    return value


def mask(address: str) -> str:
    """D179: the first four and the last four characters (migration 0041's CHECK)."""
    return f"{address[:4]}…{address[-4:]}"


# --- address formats, written once more (BIP-173, base58check, Solana's 32 bytes) -----------

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _b58decode(text: str) -> bytes | None:
    number = 0
    for char in text:
        if char not in _B58:
            return None
        number = number * 58 + _B58.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def _base58check(text: str) -> bool:
    data = _b58decode(text)
    if data is None or len(data) != 25 or data[0] not in (0x00, 0x05):
        return False
    digest = hashlib.sha256(hashlib.sha256(data[:-4]).digest()).digest()
    return digest[:4] == data[-4:]


def _polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            check ^= gen[i] if (top >> i) & 1 else 0
    return check


def _bech32(text: str) -> bool:
    """A segwit v0 address (bech32, BIP-173) on mainnet."""
    if text != text.lower() or not text.startswith("bc1"):
        return False
    data = text[3:]
    if any(c not in _BECH32 for c in data) or len(data) < 7:
        return False
    values = [_BECH32.index(c) for c in data]
    hrp = [ord(c) >> 5 for c in "bc"] + [0] + [ord(c) & 31 for c in "bc"]
    return _polymod(hrp + values) == 1 and values[0] == 0


def address_valid(chain: Chain, address: str) -> bool | None:
    """True or False where step 0 can check the format; None for EVM, whose EIP-55 check
    needs Keccak-256, which no dependency of the backend provides (step 1 decides)."""
    if chain is Chain.BITCOIN:
        return _bech32(address) or _base58check(address)
    if chain is Chain.SOLANA:
        data = _b58decode(address)
        return data is not None and len(data) == 32
    return None


# --- chains.yaml: the registry, addresses, network names, the provider boundary -------------


class RegistryCase(Lenient):
    code: Chain
    family: ChainFamily
    chain_id: int | None
    native_symbol: AssetSymbol
    native_decimals: int
    source: ChainSourceKind
    confirmations_final: int
    explorer_host: str

    @model_validator(mode="after")
    def _registry(self) -> Self:
        row = registry().chain(self.code)
        got = (
            row.family,
            row.chain_id,
            row.native_symbol,
            row.native_decimals,
            row.source,
            row.confirmations_final,
        )
        want = (
            self.family,
            self.chain_id,
            self.native_symbol,
            self.native_decimals,
            self.source,
            self.confirmations_final,
        )
        if got != want or f"https://{self.explorer_host}/" not in row.explorer_tx_url:
            raise ValueError(f"{self.id}: chains.yaml says {got}, {row.explorer_tx_url}")
        return self


class AddressCase(_Case):
    chain: Chain
    address: str
    valid: bool
    normalised: str | None = None
    masked: str | None = None

    @model_validator(mode="after")
    def _format(self) -> Self:
        checked = address_valid(self.chain, self.address)
        if checked is not None and checked != self.valid:
            raise ValueError(f"{self.id}: the format check says {checked}")
        if self.valid != (self.normalised is not None) or self.valid != (self.masked is not None):
            raise ValueError(f"{self.id}: a valid address has its normalised and masked form")
        if self.normalised is not None:
            if self.normalised.lower() != self.address.lower():
                raise ValueError(f"{self.id}: normalising changes case only")
            if self.masked != mask(self.normalised):
                raise ValueError(f"{self.id}: the mask is {mask(self.normalised)}")
        return self


class NetworkCase(Lenient):
    name: str
    chain: Chain | None

    @model_validator(mode="after")
    def _mapped(self) -> Self:
        if registry().network(self.name) is not self.chain:
            raise ValueError(f"{self.id}: chains.yaml maps it to {registry().network(self.name)}")
        return self


class ProviderExpect(Schema):
    same_as_first: bool | None = None
    error: str | None = None
    history_untouched: bool | None = None


class ProviderCase(_Case):
    chain: Chain
    records_from: str  # "<fixture>.<case id>": the recorded records replayed
    urls: list[str] = Field(default_factory=list)  # empty: no source configured (a replay)
    status: int | None = None
    expect: ProviderExpect

    @model_validator(mode="after")
    def _records_exist(self) -> Self:
        name, case_id = self.records_from.split(".", 1)
        cases = [c for v in raw(name).values() if isinstance(v, list) for c in v]
        if not any(isinstance(c, dict) and c.get("id") == case_id for c in cases):
            raise ValueError(f"{self.id}: no case {self.records_from}")
        return self


class ChainsFixture(_Fixture):
    registry_rows: list[RegistryCase] = Field(min_length=5)
    addresses: list[AddressCase] = Field(min_length=1)
    networks: list[NetworkCase] = Field(min_length=1)
    providers: list[ProviderCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _five(self) -> Self:
        if {r.code for r in self.registry_rows} != set(Chain):
            raise ValueError("chains.yaml: a registry case per chain")
        return self


# --- decoders.yaml: the rows, the workspace override, the refused files ---------------------


class DecoderRowCase(Lenient):
    family: ChainFamily
    row: DecoderRow

    @model_validator(mode="after")
    def _family(self) -> Self:
        if registry().chain(self.row.chain).family is not self.family:
            raise ValueError(f"{self.id}: {self.row.chain} is no {self.family} chain")
        return self


class DecoderCall(Schema):
    chain: Chain
    program: str
    method: str | None = None


class OverrideExpect(Schema):
    kind: DecoderKind | None
    source: Literal["workspace", "public"] | None


class OverrideCase(_Case):
    public: list[DecoderRow]
    workspace: list[DecoderRow] = Field(default_factory=list)
    call: DecoderCall
    expect: OverrideExpect

    def recompute(self) -> OverrideExpect:
        """D191: a workspace row wins over a public one; a row for the call's method over a
        method-less row of the same program (step 0's reading, §15.1)."""
        methods = [self.call.method] if self.call.method is None else [self.call.method, None]
        for method in methods:
            key = (self.call.chain, self.call.program, method)
            for source, rows in (("workspace", self.workspace), ("public", self.public)):
                for row in rows:
                    if (row.chain, row.program, row.method) == key:
                        return OverrideExpect(kind=row.kind, source=source)
        return OverrideExpect(kind=None, source=None)

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: the rule gives {self.recompute()}")
        return self


class RefusedFileCase(Lenient):
    text: str
    error_match: str

    @model_validator(mode="after")
    def _refused(self) -> Self:
        try:
            decoder_rules.parse(self.text)
        except ValidationError as exc:
            if self.error_match not in str(exc):
                raise ValueError(f"{self.id}: refused, but for {exc}") from None
            return self
        raise ValueError(f"{self.id}: the loader accepts it")


class DecodersFixture(_Fixture):
    rows: list[DecoderRowCase] = Field(min_length=1)
    override_cases: list[OverrideCase] = Field(min_length=1)
    refused: list[RefusedFileCase] = Field(min_length=1)


# --- movement_hash.yaml (D182 (2)) ----------------------------------------------------------


def movement_hash(chain: str, tx_hash: str, address_hmac: str, index: str, role: str) -> str:
    text = "\x1f".join([chain, tx_hash, address_hmac, index, role])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class HashCase(_Case):
    chain: Chain
    tx_hash: str
    address_hmac: str
    index: str = Field(min_length=1, max_length=64)
    role: Literal["in", "out", "fee"]
    hash: str

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        _hmac(self.address_hmac)
        got = movement_hash(self.chain, self.tx_hash, self.address_hmac, self.index, self.role)
        if got != self.hash:
            raise ValueError(f"{self.id}: the hash is {got}")
        return self


class HashPair(Lenient):
    a: str
    b: str
    same: bool


class MovementHashFixture(_Fixture):
    cases: list[HashCase] = Field(min_length=1)
    pairs: list[HashPair] = Field(min_length=1)

    @model_validator(mode="after")
    def _pairs(self) -> Self:
        by_id = {c.id: c.hash for c in self.cases}
        for pair in self.pairs:
            if (by_id[pair.a] == by_id[pair.b]) != pair.same:
                raise ValueError(
                    f"{pair.id}: the two hashes are {'' if pair.same else 'not '}equal"
                )
        return self


# --- btc_utxo.yaml (D185) -------------------------------------------------------------------


class EsploraPrevout(Schema):
    scriptpubkey_address: str
    value: int = Field(ge=0)


class EsploraVin(Schema):
    txid: str
    vout: int = Field(ge=0)
    prevout: EsploraPrevout


class EsploraVout(Schema):
    scriptpubkey_address: str
    value: int = Field(ge=0)


class EsploraStatus(Schema):
    confirmed: bool
    block_height: int
    block_time: int


class EsploraTx(Schema):
    """The fields of Esplora's /tx and /address/<a>/txs records that D185 reads."""

    txid: str = Field(pattern=r"^[0-9a-f]{64}$")
    vin: list[EsploraVin] = Field(min_length=1)
    vout: list[EsploraVout] = Field(min_length=1)
    fee: int = Field(ge=0)
    status: EsploraStatus

    @model_validator(mode="after")
    def _balanced(self) -> Self:
        if sum(i.prevout.value for i in self.vin) != sum(o.value for o in self.vout) + self.fee:
            raise ValueError(f"{self.txid[:8]}: Σ inputs = Σ outputs + fee")
        return self


class BtcMovement(Schema):
    kind: MovementKind
    role: Literal["in", "out", "fee"]
    address: str  # the own address the movement touches (its HMAC in the hash)
    sats: int = Field(gt=0)
    index: str


class BtcExpect(Schema):
    group: GroupKind
    movements: list[BtcMovement]
    candidates: list[str] = Field(default_factory=list)  # outputs asked about (D185 (2))
    card: str | None = None


class BtcCase(_Case):
    own: list[str] = Field(min_length=1)  # registered addresses of the account
    answered_change: list[str] = Field(default_factory=list)  # „Ja, eigene Adresse“
    answered_payee: list[str] = Field(default_factory=list)  # „Nein, Empfänger“
    tx: EsploraTx
    expect: BtcExpect

    def recompute(self) -> BtcExpect:
        """D185 (1), (2), (2) amended 15 Nov 2026 (§15.1): a spend asks while two or more of
        its outputs go to unregistered addresses (one entry per address, counted against the
        registered set with the confirmed change) and any of them is unanswered, naming only
        the unanswered ones; a „Nein“ settles only its address. A spend with one such output
        has paid it and asks nothing. The net and fee movements sit on the first own input's
        address."""
        own = set(self.own) | set(self.answered_change)
        vin_own = [i.prevout for i in self.tx.vin if i.prevout.scriptpubkey_address in own]
        out_own = sum(p.value for p in vin_own)
        if out_own == 0:
            movements = [
                BtcMovement(
                    kind=MovementKind.UNCATEGORIZED,
                    role="in",
                    address=o.scriptpubkey_address,
                    sats=o.value,
                    index=str(n),
                )
                for n, o in enumerate(self.tx.vout)
                if o.scriptpubkey_address in own
            ]
            return BtcExpect(group=GroupKind.TRANSFER, movements=movements)
        if len(vin_own) != len(self.tx.vin):
            return BtcExpect(group=GroupKind.UNKNOWN, movements=[], card=E.GROUP_UNKNOWN)
        payer = vin_own[0].scriptpubkey_address
        in_own = sum(o.value for o in self.tx.vout if o.scriptpubkey_address in own)
        foreign = list(
            dict.fromkeys(
                o.scriptpubkey_address for o in self.tx.vout if o.scriptpubkey_address not in own
            )
        )
        candidates = [a for a in foreign if a not in self.answered_payee]
        if len(foreign) >= 2 and candidates:
            return BtcExpect(
                group=GroupKind.UNKNOWN,
                movements=[],
                candidates=candidates,
                card=E.WALLET_CHANGE_CANDIDATE,
            )
        fee = BtcMovement(
            kind=MovementKind.FEE, role="fee", address=payer, sats=self.tx.fee, index="fee"
        )
        net = out_own - in_own - self.tx.fee
        sent = BtcMovement(
            kind=MovementKind.UNCATEGORIZED, role="out", address=payer, sats=net, index="net"
        )
        return BtcExpect(group=GroupKind.TRANSFER, movements=[sent, fee] if net else [fee])

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: D185 gives {self.recompute()}")
        return self


class ChainStats(Schema):
    funded_txo_sum: int = Field(ge=0)
    spent_txo_sum: int = Field(ge=0)


class BtcBalanceCase(_Case):
    address: str
    chain_stats: ChainStats
    expect_sats: int

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.chain_stats.funded_txo_sum - self.chain_stats.spent_txo_sum != self.expect_sats:
            raise ValueError(f"{self.id}: funded − spent")
        return self


class SourceErrorCase(_Case):
    status: int
    expect_error: str

    @model_validator(mode="after")
    def _code(self) -> Self:
        codes = {429: E.WALLET_SOURCE_THROTTLED, 401: E.WALLET_SOURCE_REFUSED}
        codes[403] = E.WALLET_SOURCE_REFUSED
        if codes.get(self.status) != self.expect_error:
            raise ValueError(f"{self.id}: a {self.status} is {codes.get(self.status)}")
        return self


class BtcFixture(_Fixture):
    cases: list[BtcCase] = Field(min_length=1)
    balances: list[BtcBalanceCase] = Field(min_length=1)
    errors: list[SourceErrorCase] = Field(min_length=1)


# --- evm_tx.yaml ----------------------------------------------------------------------------


class EvmMovement(Schema):
    kind: MovementKind
    role: Literal["in", "out", "fee"]
    units: int = Field(gt=0)  # base units (wei, or the token's smallest unit)
    index: str
    contract: str | None = None  # None: the chain's native coin


class EvmExpect(Schema):
    group: GroupKind | None = None
    fee_wei: int | None = None
    movements: list[EvmMovement] = Field(default_factory=list)
    link: TransferLinkKind | None = None
    error: str | None = None
    card: str | None = None
    reconciled: bool | None = None
    precision_note: str | None = None


class EvmCase(_Case):
    chain: Chain
    own: list[str] = Field(default_factory=list)
    records: dict[str, JsonValue] = Field(default_factory=dict)  # Etherscan's result rows
    exchange_leg: dict[str, JsonValue] | None = None  # the phase 6 movement it may link
    expect: EvmExpect

    @model_validator(mode="after")
    def _checked(self) -> Self:
        if registry().chain(self.chain).family is not ChainFamily.EVM:
            raise ValueError(f"{self.id}: {self.chain} is no EVM chain")
        txs = self.records.get("txlist") or []
        signed = [t for t in txs if isinstance(t, dict) and t.get("from") in self.own]
        if signed and self.expect.fee_wei is not None:
            (tx,) = signed
            fee = int(tx["gasUsed"]) * int(tx["gasPrice"])  # type: ignore[arg-type]
            if fee != self.expect.fee_wei:
                raise ValueError(f"{self.id}: gasUsed × gasPrice = {fee}")
        rows = [
            t
            for key in ("txlist", "tokentx", "txlistinternal")
            for t in self.records.get(key) or []
            if isinstance(t, dict) and "value" in t
        ]
        values = {int(t["value"]) for t in rows}  # type: ignore[arg-type]
        # An RPC receipt (D181 (c)): the transaction's hex value and each log's hex data.
        transaction = self.records.get("transaction")
        if isinstance(transaction, dict):
            values.add(int(str(transaction["value"]), 16))
        receipt = self.records.get("receipt")
        if isinstance(receipt, dict):
            for log in receipt.get("logs") or []:  # type: ignore[union-attr]
                values.add(int(str(log["data"]), 16))  # type: ignore[index]
        for movement in self.expect.movements:
            if movement.kind is MovementKind.FEE and movement.units != self.expect.fee_wei:
                raise ValueError(f"{self.id}: the fee movement is fee_wei")
            if movement.kind is not MovementKind.FEE and movement.units not in values:
                raise ValueError(f"{self.id}: {movement.units} is no record's value")
        return self


class EvmFixture(_Fixture):
    cases: list[EvmCase] = Field(min_length=1)


# --- solana_tx.yaml (D181 (d)) --------------------------------------------------------------


class SolDelta(Schema):
    mint: str | None = None  # None: SOL
    units: int


class SolMovement(Schema):
    kind: MovementKind
    role: Literal["in", "out", "fee"]
    units: int = Field(gt=0)
    mint: str | None = None  # None: SOL
    index: str


class SolExpect(Schema):
    status: Literal["ok", "failed"]
    fee_lamports: int | None  # the fee the own address paid, else None
    deltas: list[SolDelta]
    group: GroupKind
    movements: list[SolMovement] = Field(default_factory=list)
    card: str | None = None
    label: str | None = None


def _keys(tx: dict) -> list[str]:
    """The account list the balance arrays index. jsonParsed lists every key, the lookup
    table's ones too (`source: lookupTable`); the plain `json` encoding lists the static keys
    and the loaded ones follow from meta.loadedAddresses (writable, then readonly)."""
    keys = tx["transaction"]["message"]["accountKeys"]
    if keys and isinstance(keys[0], dict):
        return [k["pubkey"] for k in keys]
    loaded = tx["meta"].get("loadedAddresses") or {}
    return list(keys) + list(loaded.get("writable", [])) + list(loaded.get("readonly", []))


def solana_deltas(tx: dict, own: str) -> tuple[int | None, list[SolDelta]]:
    """D181 (d): the own address's SOL delta over its main account and its token accounts (so
    a token account's rent is not a leak), without the fee it paid; one delta per mint over
    the token balances it owns."""
    meta = tx["meta"]
    keys = _keys(tx)
    token_accounts = {
        b["accountIndex"]
        for b in meta.get("preTokenBalances", []) + meta.get("postTokenBalances", [])
        if b.get("owner") == own
    }
    # The own main account is absent from a transaction that only credits its token account.
    indices = ({keys.index(own)} if own in keys else set()) | token_accounts
    if len(keys) != len(meta["preBalances"]) or len(keys) != len(meta["postBalances"]):
        raise ValueError("a balance per account key")
    sol = sum(meta["postBalances"][i] - meta["preBalances"][i] for i in indices)
    payer = keys[0] == own
    if payer:
        sol += meta["fee"]
    deltas = [SolDelta(units=sol)] if sol else []
    mints: dict[str, int] = {}
    for sign, rows in (
        (-1, meta.get("preTokenBalances", [])),
        (1, meta.get("postTokenBalances", [])),
    ):
        for b in rows:
            if b.get("owner") == own:
                mints[b["mint"]] = mints.get(b["mint"], 0) + sign * int(
                    b["uiTokenAmount"]["amount"]
                )
    deltas += [SolDelta(mint=m, units=u) for m, u in sorted(mints.items()) if u]
    return (meta["fee"] if payer else None), deltas


class SolanaCase(_Case):
    own: str
    tx: dict[str, JsonValue]
    # An optional Helius enrichment (D181 (d), D192): a label only, never a kind or amount.
    provider_label: dict[str, JsonValue] | None = None
    decoder_listed: bool = False  # a decoder row names the transaction's program
    expect: SolExpect

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.tx.get("version") != 0:
            raise ValueError(f"{self.id}: recorded with maxSupportedTransactionVersion 0")
        fee, deltas = solana_deltas(self.tx, self.own)  # type: ignore[arg-type]
        if (fee, deltas) != (self.expect.fee_lamports, self.expect.deltas):
            raise ValueError(f"{self.id}: the deltas are {fee}, {deltas}")
        status = "failed" if self.tx["meta"]["err"] is not None else "ok"  # type: ignore[index]
        if status != self.expect.status:
            raise ValueError(f"{self.id}: the status is {status}")
        if (self.provider_label or {}).get("description") != self.expect.label:
            raise ValueError(f"{self.id}: the label is the provider's description, or none")
        return self


class RewardExpect(Schema):
    kind: MovementKind
    units: int = Field(gt=0)
    on: date


class InflationRewardCase(_Case):
    stake_account: str
    result: dict[str, JsonValue]  # one entry of getInflationReward's result
    epoch_end: datetime
    expect: RewardExpect

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.expect.units != self.result["amount"]:
            raise ValueError(f"{self.id}: the reward is the result's amount")
        if self.expect.on != self.epoch_end.astimezone(BERLIN).date():
            raise ValueError(f"{self.id}: dated the epoch's end, Berlin day")
        return self


class SolanaFixture(_Fixture):
    cases: list[SolanaCase] = Field(min_length=1)
    inflation_rewards: list[InflationRewardCase] = Field(min_length=1)


# --- swap_group.yaml (D183, D188 (4)) ------------------------------------------------------


class Leg(Schema):
    asset: str
    units: Decimal = Field(gt=0)
    role: Literal["given", "received"]


class SwapExpect(Schema):
    group: GroupKind
    status: GroupStatus
    eur_amount: Money | None
    priced_by: str | None
    question: str | None = None
    learned: DecoderRow | None = None


class SwapCase(_Case):
    legs: list[Leg] = Field(min_length=1)
    fee: Leg | None = None
    prices: dict[str, PriceEur] = Field(default_factory=dict)  # the group's Berlin day
    decoder_listed: bool = False
    answer: GroupKind | None = None
    learn: bool = False
    expect: SwapExpect

    def valued(self) -> tuple[Decimal | None, str | None]:
        """Rn. 58: the received asset's value first, else the given asset's."""
        for role in ("received", "given"):
            for leg in self.legs:
                if leg.role == role and leg.asset in self.prices:
                    value = (leg.units * self.prices[leg.asset]).quantize(_CENT, ROUND_HALF_UP)
                    return value, leg.asset
        return None, None

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        is_swap = self.expect.group is GroupKind.SWAP and (self.answer or len(self.legs) == 2)
        if is_swap and (self.expect.eur_amount, self.expect.priced_by) != self.valued():
            raise ValueError(f"{self.id}: valued at {self.valued()}")
        if not is_swap and self.expect.eur_amount is not None:
            raise ValueError(f"{self.id}: only a swap of two legs is valued here")
        return self


class SwapFixture(_Fixture):
    cases: list[SwapCase] = Field(min_length=1)


# --- spam.yaml (D187) -----------------------------------------------------------------------


class Token(Schema):
    chain: Chain
    contract: ContractId = Field(min_length=20, max_length=128)
    symbol: TokenSymbol  # as the chain names it (attacker-controlled; D180 (2) amended)


class SpamExpect(Schema):
    group: GroupKind
    movement: MovementKind
    hidden: bool
    card: bool
    label: str
    price_source_needed: bool


class SpamCase(_Case):
    token: Token
    held: bool = False  # the workspace holds it with a confirmed lot
    fee_paid: bool = False  # the own address signed the transaction
    bought: bool = False  # a swap decoder's received leg
    action: Literal["promote", "demote"] | None = None
    expect: SpamExpect

    def recompute(self) -> SpamExpect:
        listed = asset_registry().token(self.token.chain, self.token.contract)
        spam = listed is None and not self.held and not self.fee_paid and not self.bought
        if self.action == "promote" and spam:
            spam = False
            kind, movement = GroupKind.AIRDROP, MovementKind.AIRDROP
        elif self.action == "demote":
            spam = True
            kind, movement = GroupKind.SPAM, MovementKind.SPAM
        elif spam:
            kind, movement = GroupKind.SPAM, MovementKind.SPAM
        elif self.bought:
            kind, movement = GroupKind.SWAP, MovementKind.ACQUISITION
        else:
            kind, movement = GroupKind.AIRDROP, MovementKind.AIRDROP
        label = (
            f"Token {mask(self.token.contract)} (nennt sich {self.token.symbol})"
            if spam or listed is None
            else listed.symbol
        )
        return SpamExpect(
            group=kind,
            movement=movement,
            hidden=spam,
            card=kind is GroupKind.AIRDROP,
            label=label,
            price_source_needed=not spam and listed is None,
        )

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: D187 gives {self.recompute()}")
        return self


class SpamFixture(_Fixture):
    cases: list[SpamCase] = Field(min_length=1)


# --- chain_transfers.yaml (D184) -----------------------------------------------------------

WINDOW = {
    TransferLinkKind.CHAIN: timedelta(hours=48),
    TransferLinkKind.BRIDGE: timedelta(hours=24),
    TransferLinkKind.WRAP: timedelta(minutes=1),
}


class OpenLeg(Schema):
    name: str
    account: str
    chain: Chain | None = None  # None: an exchange account
    asset: str  # the priced_as symbol
    units: Decimal = Field(gt=0)
    fee: Decimal = Decimal(0)
    at: datetime
    tx_hash: str | None = None
    network: str | None = None
    own: bool = True
    superseded: bool = False


class TransferExpect(Schema):
    result: Literal["linked", "ambiguous", "unmatched"]
    link: TransferLinkKind | None = None
    with_leg: str | None = None
    card: str | None = None
    lots_move: bool | None = None


class TransferCase(_Case):
    kind: TransferLinkKind
    leg: OpenLeg  # the sending side
    candidates: list[OpenLeg] = Field(default_factory=list)
    expect: TransferExpect

    def recompute(self) -> TransferExpect:
        fits = [
            c for c in self.candidates if c.own and not c.superseded and c.asset == self.leg.asset
        ]
        if self.leg.tx_hash and self.kind is TransferLinkKind.CHAIN:
            fits = [c for c in fits if c.tx_hash == self.leg.tx_hash]
        else:
            fits = [c for c in fits if timedelta(0) <= c.at - self.leg.at <= WINDOW[self.kind]]
            if self.kind is TransferLinkKind.BRIDGE:
                (pair,) = registry().bridge(self.leg.chain, fits[0].chain) if fits else (None,)
                share = pair.max_fee_share if pair else Decimal(0)
                fits = [
                    c for c in fits if self.leg.units * (1 - share) <= c.units <= self.leg.units
                ]
            else:
                fits = [c for c in fits if c.units == self.leg.units - self.leg.fee]
            if self.leg.network is not None:
                chain = registry().network(self.leg.network)
                fits = [c for c in fits if c.chain is not None and c.chain is chain]
        if len(fits) == 1:
            moves = fits[0].account != self.leg.account
            return TransferExpect(
                result="linked", link=self.kind, with_leg=fits[0].name, lots_move=moves
            )
        if fits:
            return TransferExpect(result="ambiguous", card=E.TRANSFER_AMBIGUOUS)
        return TransferExpect(result="unmatched", card=E.TRANSFER_UNMATCHED_LEG)

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: D184 gives {self.recompute()}")
        return self


class ChainTransfersFixture(_Fixture):
    cases: list[TransferCase] = Field(min_length=1)


# --- wallet_reconcile.yaml (D186 (2), (3)) ------------------------------------------------


class ReconcileMovement(Schema):
    asset: str
    units: int  # signed base units: in positive, out and fee negative
    status: Literal["confirmed", "proposed"]
    spam: bool = False


class ReconcileExpect(Schema):
    reconciled: bool
    difference: dict[str, int] = Field(default_factory=dict)
    card: str | None = None


class ReconcileCase(_Case):
    chain: Chain
    movements: list[ReconcileMovement] = Field(default_factory=list)
    # the source's balance per asset, as its parts (Solana: the main account and the rent in
    # the token accounts; Bitcoin: one sum per address)
    balance: dict[str, list[int]] = Field(default_factory=dict)
    balance_only: bool = False
    linked_deposits: dict[str, int] = Field(default_factory=dict)
    expect: ReconcileExpect

    def recompute(self) -> ReconcileExpect:
        have = {a: sum(parts) for a, parts in self.balance.items()}
        if self.balance_only:
            short = {
                a: have.get(a, 0) - units
                for a, units in self.linked_deposits.items()
                if have.get(a, 0) < units
            }
            if short:
                return ReconcileExpect(reconciled=False, difference=short, card=E.WALLET_COINS_LEFT)
            return ReconcileExpect(reconciled=True)
        sums: dict[str, int] = {}
        for m in self.movements:
            if not m.spam:
                sums[m.asset] = sums.get(m.asset, 0) + m.units
        assets = set(sums) | set(have)
        diff = {a: have.get(a, 0) - sums.get(a, 0) for a in sorted(assets)}
        diff = {a: d for a, d in diff.items() if d}
        if diff:
            return ReconcileExpect(reconciled=False, difference=diff, card=E.WALLET_UNRECONCILED)
        return ReconcileExpect(reconciled=True)

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: D186 gives {self.recompute()}")
        return self


class ReconcileFixture(_Fixture):
    cases: list[ReconcileCase] = Field(min_length=1)


# --- lots.yaml, holding_period.yaml (D188) -------------------------------------------------


def berlin_day(at: datetime) -> date:
    return at.astimezone(BERLIN).date()


def tax_free_from(acquired_on: date) -> date:
    """D188 (3): the day after the corresponding day of the next year; the last day of the
    month when it does not exist (§188 Abs. 3 BGB)."""
    try:
        end = acquired_on.replace(year=acquired_on.year + 1)
    except ValueError:  # 29 Feb
        end = date(acquired_on.year + 1, 3, 1) - timedelta(days=1)
    return end + timedelta(days=1)


class LedgerEvent(Schema):
    name: str
    at: datetime
    account: str
    # The asset, when a case holds two (a wrap's native coin and its wrapped token, a bridge's
    # asset on two chains); None: the case's one asset.
    asset: str | None = None
    kind: Literal[
        "acquisition",
        "reward",
        "swap_in",
        "transfer_out",
        "wrap",
        "bridge",
        "disposal",
        "fee",
        "lp_deposit",
        "lp_withdrawal",
        "trade_fee",
    ]
    units: Decimal = Field(gt=0)
    cost_eur: Money | None = None  # acquisition, reward, swap_in; None: unpriced
    fee_eur: Money = Decimal(0)
    to_account: str | None = None  # transfer_out
    to_asset: str | None = None  # wrap, bridge: the same account, the other asset
    superseded: bool = False
    # D188 amended (16 Nov 2026): a `trade_fee` is a fee movement of the trade named here (an
    # acquisition of the same account), paid in `asset`. In the coin bought it is valued at the
    # trade's own price (inside the EUR paid, nothing added); in another coin at `price_eur`
    # per unit on the day (None: no price, the trade's lot unpriced).
    trade: str | None = None
    price_eur: Decimal | None = Field(default=None, gt=0)
    # D188 amended (review of step 6): read from a chain (D182). A chain acquisition has no
    # EUR leg: its `cost_eur` is the day's price × units (a price row). A chain `trade_fee` in
    # the coin acquired is gas paid on top of the units received: valued at that coin's price
    # of the day (the acquisition's) and added to the lot, unlike an exchange's withheld fee.
    chain: bool = False

    @model_validator(mode="after")
    def _trade_fee(self) -> Self:
        if (self.kind == "trade_fee") != (self.trade is not None):
            raise ValueError(f"{self.name}: a trade fee, and only one, names its trade")
        if self.price_eur is not None and self.kind != "trade_fee":
            raise ValueError(f"{self.name}: only a trade fee carries a price")
        if self.chain and self.kind not in ("acquisition", "trade_fee", "disposal"):
            raise ValueError(f"{self.name}: only a trade's legs are marked as read from a chain")
        return self

    @model_validator(mode="after")
    def _moves(self) -> Self:
        if (self.kind == "transfer_out") != (self.to_account is not None):
            raise ValueError(f"{self.name}: a transfer out, and only one, names its account")
        if (self.kind in ("wrap", "bridge")) != (self.to_asset is not None):
            raise ValueError(f"{self.name}: a wrap or a bridge, and only one, names its asset")
        if self.to_asset is not None and (self.asset is None or self.to_asset == self.asset):
            raise ValueError(f"{self.name}: a wrap or a bridge moves to another asset")
        return self


class LotExpect(Schema):
    account: str
    asset: str | None = None  # as LedgerEvent.asset
    acquired_on: date
    units_open: Decimal
    cost_eur: Money | None  # of the open units
    source: LotSource
    tax_free_from: date
    moved: bool = False


class UseExpect(Schema):
    event: str
    acquired_on: date
    units: Decimal
    cost_eur: Money | None


class LotsCase(_Case):
    events: list[LedgerEvent] = Field(min_length=1)
    expect_lots: list[LotExpect]
    expect_uses: list[UseExpect]

    def recompute(self) -> tuple[list[LotExpect], list[UseExpect]]:
        books: dict[tuple[str, str | None], list[dict]] = {}
        uses: list[UseExpect] = []
        source_of = {
            "acquisition": LotSource.ACQUISITION,
            "reward": LotSource.REWARD,
            "swap_in": LotSource.ACQUISITION,  # a swap's received leg (LotSource has no swap)
        }
        # D188 amended (16 Nov 2026): a trade's fees in another coin join its lot's cost at
        # their day's price; a fee in the coin bought joins nothing (inside the EUR paid).
        live = [e for e in self.events if not e.superseded]
        by_name = {e.name: e for e in live}
        coin_fees: dict[str, Decimal | None] = {}
        for ev in live:
            if ev.kind != "trade_fee":
                continue
            trade = by_name.get(ev.trade or "")
            if trade is None or trade.kind != "acquisition" or trade.account != ev.account:
                raise ValueError(f"{self.id}: {ev.name} names no acquisition of its account")
            if ev.chain != trade.chain:
                raise ValueError(f"{self.id}: {ev.name} and its trade are read from one place")
            if ev.asset == trade.asset:
                if ev.price_eur is not None:
                    raise ValueError(f"{self.id}: {ev.name} in the coin bought has its price")
                if not ev.chain:
                    continue  # withheld from the units received: inside the EUR paid
                # Chain gas paid on top: at the coin's price of the day (the trade's own).
                before = coin_fees.get(trade.name, Decimal(0))
                coin_fees[trade.name] = (
                    None
                    if before is None or trade.cost_eur is None
                    else before + ev.units * trade.cost_eur / trade.units
                )
                continue
            before = coin_fees.get(trade.name, Decimal(0))
            coin_fees[trade.name] = (
                None if before is None or ev.price_eur is None else before + ev.units * ev.price_eur
            )
        for ev in sorted((e for e in self.events if not e.superseded), key=lambda e: e.at):
            lots = books.setdefault((ev.account, ev.asset), [])
            if ev.kind in ("lp_deposit", "lp_withdrawal"):
                continue  # D188 (1): lp_* consumes nothing (phase 8 decides), and creates none
            if ev.kind in source_of:
                extra = coin_fees.get(ev.name, Decimal(0))
                cost = (
                    None
                    if ev.cost_eur is None or extra is None
                    else ev.cost_eur + ev.fee_eur + extra
                )
                per = None if cost is None else cost / ev.units
                day = berlin_day(ev.at)
                lots.append(
                    {
                        "on": day,
                        "units": ev.units,
                        "per": per,
                        "src": source_of[ev.kind],
                        "moved": False,
                    }
                )
                continue
            left = ev.units
            for lot in sorted(lots, key=lambda x: x["on"]):
                if left == 0:
                    break
                take = min(left, lot["units"])
                if take == 0:
                    continue
                lot["units"] -= take
                left -= take
                if ev.kind in ("wrap", "bridge"):
                    # D184 (2)-(4): not a disposal; the lot is re-created on the other asset
                    # with its day, cost per unit and source, and no use is recorded.
                    books.setdefault((ev.account, ev.to_asset), []).append(
                        {**lot, "units": take, "moved": True}
                    )
                    continue
                cost = (
                    None
                    if lot["per"] is None
                    else (lot["per"] * take).quantize(_CENT, ROUND_HALF_UP)
                )
                uses.append(
                    UseExpect(event=ev.name, acquired_on=lot["on"], units=take, cost_eur=cost)
                )
                if ev.kind == "transfer_out":
                    books.setdefault((ev.to_account or "", ev.asset), []).append(
                        {**lot, "units": take, "moved": True}
                    )
            if left:
                raise ValueError(f"{self.id}: {ev.name} takes more than the account holds")
        out = [
            LotExpect(
                account=account,
                asset=asset,
                acquired_on=lot["on"],
                units_open=lot["units"],
                cost_eur=None
                if lot["per"] is None
                else (lot["per"] * lot["units"]).quantize(_CENT, ROUND_HALF_UP),
                source=lot["src"],
                tax_free_from=tax_free_from(lot["on"]),
                moved=lot["moved"],
            )
            for (account, asset), lots in sorted(
                books.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
            for lot in sorted(lots, key=lambda x: x["on"])
            if lot["units"]
        ]
        return out, uses

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        lots, uses = self.recompute()
        if lots != self.expect_lots:
            raise ValueError(f"{self.id}: FIFO gives {lots}")
        if uses != self.expect_uses:
            raise ValueError(f"{self.id}: FIFO uses {uses}")
        return self


class LotsFixture(_Fixture):
    cases: list[LotsCase] = Field(min_length=1)


class DayCase(_Case):
    acquired_at: datetime
    expect_acquired_on: date
    expect_tax_free_from: date

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        day = berlin_day(self.acquired_at)
        if (day, tax_free_from(day)) != (self.expect_acquired_on, self.expect_tax_free_from):
            raise ValueError(f"{self.id}: D188 gives {day}, {tax_free_from(day)}")
        return self


class SaleCase(_Case):
    acquired_on: date
    sold_on: date
    expect_taxable: bool

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if (self.sold_on < tax_free_from(self.acquired_on)) != self.expect_taxable:
            raise ValueError(f"{self.id}: tax-free from {tax_free_from(self.acquired_on)}")
        return self


class KeptDayCase(_Case):
    acquired_at: datetime
    moved_or_swapped_at: datetime
    kind: Literal["moved", "swap"]
    expect_acquired_on: date

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        day = berlin_day(self.acquired_at if self.kind == "moved" else self.moved_or_swapped_at)
        if day != self.expect_acquired_on:
            raise ValueError(f"{self.id}: the lot's day is {day}")
        return self


class HoldingPeriodFixture(_Fixture):
    days: list[DayCase] = Field(min_length=1)
    sales: list[SaleCase] = Field(min_length=1)
    kept: list[KeptDayCase] = Field(min_length=1)


# --- holdings_statement.yaml (D190) --------------------------------------------------------


class StatementMovement(Schema):
    name: str
    account: str
    address: str | None = None  # a masked address; None on an exchange
    asset: str
    units: Decimal
    at: datetime
    spam: bool = False


class StatementPosition(Schema):
    account: str
    address: str | None
    asset: str
    units: Decimal


class StatementExpect(Schema):
    positions: list[StatementPosition]
    excluded: list[str]
    unvalued: list[str]  # the spam tokens' assets, in the appendix
    unreconciled: list[str]  # addresses marked „nicht abgeglichen“


class StatementCase(_Case):
    on: date
    movements: list[StatementMovement]
    unreconciled: list[str] = Field(default_factory=list)
    expect: StatementExpect

    def recompute(self) -> StatementExpect:
        kept = [m for m in self.movements if berlin_day(m.at) <= self.on]
        sums: dict[tuple, Decimal] = {}
        for m in kept:
            if not m.spam:
                key = (m.account, m.address, m.asset)
                sums[key] = sums.get(key, Decimal(0)) + m.units
        return StatementExpect(
            positions=[
                StatementPosition(account=k[0], address=k[1], asset=k[2], units=u)
                for k, u in sorted(sums.items(), key=lambda kv: tuple(str(x) for x in kv[0]))
                if u
            ],
            excluded=[m.name for m in self.movements if m not in kept],
            unvalued=sorted({m.asset for m in kept if m.spam}),
            unreconciled=self.unreconciled,
        )

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        if self.recompute() != self.expect:
            raise ValueError(f"{self.id}: the statement is {self.recompute()}")
        return self


class StatementFixture(_Fixture):
    cases: list[StatementCase] = Field(min_length=1)


# --- sync_window_chain.yaml (D182 (1), (4)) ------------------------------------------------


class WindowExpect(Schema):
    from_block: int
    to_block: int


class WindowCase(_Case):
    chain: Chain
    cursor: int | None
    tip: int
    seen_hashes: list[str] = Field(default_factory=list)  # movements stored before
    read_hashes: list[str] = Field(default_factory=list)  # movements this window reads
    expect: WindowExpect | None
    expect_new: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _recomputed(self) -> Self:
        final = registry().chain(self.chain).confirmations_final
        if registry().chain(self.chain).family is ChainFamily.SOLANA:
            raise ValueError(f"{self.id}: Solana reads by signature at `finalized`, no window")
        start = 0 if self.cursor is None else max(self.cursor - final, 0)
        end = self.tip - final
        got = WindowExpect(from_block=start, to_block=end) if end >= start else None
        if got != self.expect:
            raise ValueError(f"{self.id}: the window is {got}")
        new = [h for h in self.read_hashes if h not in self.seen_hashes]
        if new != self.expect_new:
            raise ValueError(f"{self.id}: new movements {new}")
        return self


class SyncWindowFixture(_Fixture):
    cases: list[WindowCase] = Field(min_length=1)


MODELS: dict[str, type[Schema]] = {
    "chains": ChainsFixture,
    "decoders": DecodersFixture,
    "movement_hash": MovementHashFixture,
    "btc_utxo": BtcFixture,
    "evm_tx": EvmFixture,
    "solana_tx": SolanaFixture,
    "swap_group": SwapFixture,
    "spam": SpamFixture,
    "chain_transfers": ChainTransfersFixture,
    "wallet_reconcile": ReconcileFixture,
    "lots": LotsFixture,
    "holding_period": HoldingPeriodFixture,
    "holdings_statement": StatementFixture,
    "sync_window_chain": SyncWindowFixture,
}


@cache
def load(name: str) -> Schema:
    return MODELS[name].model_validate(raw(name))
