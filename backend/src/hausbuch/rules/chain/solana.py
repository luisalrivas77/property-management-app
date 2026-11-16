"""Solana: a `getTransaction` record to its deltas and its movement group (docs/phases/phase-7.md
D181 (d), D183, D186, D187, §6); built in step 2.

- `solana_deltas`: the body of `rules/chain/normalise.solana_deltas`. One delta per asset of the
  own addresses (the main address, every token account it owns, a registered stake account),
  from the record's pre/post balances; the fee the own address paid is added back, so it is no
  part of the SOL delta.
- `classify_solana`: the deltas, the programs called and the decoder rows to a `SolanaGroup`
  (the group's kind, its legs in base units, the card and the question), by shape first and a
  decoder only where the shape allows it (D183 (3), (4)); never a model.
- `inflation_reward`: one `getInflationReward` entry to a `staking_reward` leg dated the
  epoch's end on the Berlin calendar (§6 (10)).
- `to_draft`: a `SolanaGroup` to the `MovementGroupDraft` the sync writes, once the caller has
  the account, the address and the asset rows.

Readings of step 2 (docs/phases/phase-7.md §15.1, reported with the step):
- wrapped SOL: a token account of the native mint holds its rent plus the wrapped amount as
  lamports; its token amount counts as WSOL and only the rest of its lamports as SOL, so a wrap
  or an unwrap is one SOL leg and one WSOL leg of the same units (a `wrap` by the registry's
  pair), and a temporary wSOL account opened and closed inside a swap nets to nothing;
- the rent of a third party's token account the own fee payer opened (a send that creates the
  recipient's account) is read from the record: the new account's own lamports, folded into the
  `fee` movement (D181 (d) „the rent as fee“), never fetched from the RPC and with no condition
  that a token leg moves beside it;
  a new foreign account of the native mint counts only its lamports beyond the wrapped amount
  (the amount is a SOL leg that was sent, never fee);
- the rent a third party paid into an own token account it opened is a SOL `in` leg of its own
  (index `rent`, kind `airdrop`: received without anything given), never part of the shape: in
  a spam group a leg of that hidden group, beside a normal receive the same `airdrop` leg; the
  own address can close the account and keep it, and the reconciliation counts it (D186 (2));
- a token credited through the Token programs alone is a transfer; through any other program,
  without a decoder that reads it, an `airdrop` (review); spam by D187 (1) either way;
- a token balance row without an `owner` is resolved by its account's address against the own
  token accounts the caller knows; a row that stays unresolved, or a Token program call with no
  token balance rows at all, raises `wallet.record_unreadable` (never a leg dropped);
- a leg's index uses only the top-level instructions' positions and program ids (never parsed
  `type` or `info`): a token leg's is the first Token program call, a SOL leg's the first
  System program call, else the first call that is not a compute-budget, memo or
  account-creation call, else "0"; besides `fee` and `rent`; a second leg on the same index and
  role gets ".2", ".3".
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from hausbuch.rules import errors as E
from hausbuch.rules.chain.classify import ChainGroup, ChainLeg, ClassifyContext
from hausbuch.rules.chain.decoders import decoder_for
from hausbuch.rules.chain.registry import registry as chain_registry
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import (
    AssetKey,
    Chain,
    ChainTx,
    DecoderKind,
    DecoderRow,
    GroupKind,
    MovementGroupDraft,
    ProviderLabel,
    SolanaDelta,
)
from hausbuch.schemas.household import AssetMovementDraft, MovementKind, MovementSource

BERLIN = ZoneInfo("Europe/Berlin")

SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
STAKE_PROGRAM = "Stake11111111111111111111111111111111111111"
TOKEN_PROGRAMS = frozenset({TOKEN_PROGRAM, TOKEN_2022_PROGRAM})
# Programs that move value only as a plain transfer: a call to nothing else is no contract call.
PLAIN_PROGRAMS = frozenset(
    {
        SYSTEM_PROGRAM,
        TOKEN_PROGRAM,
        TOKEN_2022_PROGRAM,
        ATA_PROGRAM,
        COMPUTE_BUDGET_PROGRAM,
        MEMO_PROGRAM,
    }
)
# Calls that set a transaction up and never carry a leg (the second step of the index rule).
_SETUP_PROGRAMS = frozenset({COMPUTE_BUDGET_PROGRAM, MEMO_PROGRAM, ATA_PROGRAM})

QUESTION_UNKNOWN = "Was ist diese Transaktion?"
# A record whose token legs cannot be read (wallet.record_unreadable): a card for a person,
# never a stopped sync (classify_tx).
QUESTION_UNREADABLE = (
    "Hausbuch kann diese Transaktion nicht vollständig lesen. Welche Token hat sie bewegt?"
)
# The key under which the sync's source keeps, beside a stored record, the own token accounts
# it knew, only when a token balance row of the record has no `owner` (every other record is
# stored exactly as the provider served it). The classifier reads it from the stored
# original, so a replay needs no provider (D192).
SYNC_KEY = "_hausbuch"

Role = Literal["in", "out", "fee"]
DecoderLookup = Callable[[str, str | None], DecoderRow | None]


def native_mint() -> str:
    """The wrapped-SOL mint, the registry's wrap pair (rules/chain/chains.yaml `wraps`)."""
    (wrap,) = chain_registry().chain(Chain.SOLANA).wraps
    return wrap.wrapped


def mask(value: str) -> str:
    """The first four characters, „…“, the last four (§4; the question and the label)."""
    return f"{value[:4]}…{value[-4:]}"


def berlin_day(at: datetime) -> date:
    return at.astimezone(BERLIN).date()


# --- the record ---------------------------------------------------------------------------


def _obj(value: object) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError("a getTransaction record is a JSON object")
    return cast(Mapping[str, JsonValue], value)


def _list(value: object) -> list[JsonValue]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("a getTransaction field is a list")
    return value


def _message(tx: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return _obj(_obj(tx["transaction"])["message"])


def account_keys(tx: Mapping[str, JsonValue]) -> list[str]:
    """The account list the balance arrays index. jsonParsed lists every key, the lookup
    table's ones too (`source: lookupTable`); the plain `json` encoding lists the static keys,
    and the loaded ones follow from meta.loadedAddresses (writable, then readonly)."""
    keys = _list(_message(tx)["accountKeys"])
    if keys and isinstance(keys[0], Mapping):
        return [str(_obj(k)["pubkey"]) for k in keys]
    loaded = _obj(_obj(tx["meta"]).get("loadedAddresses") or {})
    return [str(k) for k in keys + _list(loaded.get("writable")) + _list(loaded.get("readonly"))]


def signers(tx: Mapping[str, JsonValue]) -> set[str]:
    keys = _list(_message(tx)["accountKeys"])
    if keys and isinstance(keys[0], Mapping):
        return {str(_obj(k)["pubkey"]) for k in keys if _obj(k).get("signer") is True}
    header = _obj(_message(tx).get("header") or {})
    required = header.get("numRequiredSignatures")
    count = required if isinstance(required, int) else 1
    return {str(k) for k in keys[:count]}


def signature(tx: Mapping[str, JsonValue]) -> str:
    return str(_list(_obj(tx["transaction"])["signatures"])[0])


def failed(tx: Mapping[str, JsonValue]) -> bool:
    return _obj(tx["meta"]).get("err") is not None


def block_time(tx: Mapping[str, JsonValue]) -> datetime:
    """The record's `blockTime`, else the time the sync's source kept beside it (SYNC_KEY)."""
    value = tx.get("blockTime")
    kept = tx.get(SYNC_KEY)
    if not isinstance(value, int) and isinstance(kept, Mapping):
        value = kept.get("block_time")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("a finalized transaction carries its blockTime")
    return datetime.fromtimestamp(value, UTC)


@dataclass(frozen=True)
class Instruction:
    """One instruction, top-level or inner, as jsonParsed gives it (its parsed `info` is never
    read: a provider may leave it out)."""

    position: int  # the top-level instruction it belongs to
    program: str
    type: str | None


def _instruction(position: int, raw: object, keys: Sequence[str]) -> Instruction:
    ins = _obj(raw)
    program = ins.get("programId")
    if program is None and isinstance(ins.get("programIdIndex"), int):
        program = keys[cast(int, ins["programIdIndex"])]
    parsed = ins.get("parsed")
    kind = _obj(parsed).get("type") if isinstance(parsed, Mapping) else None
    return Instruction(position, str(program), str(kind) if kind else None)


def instructions(tx: Mapping[str, JsonValue]) -> tuple[list[Instruction], list[Instruction]]:
    """The top-level instructions and the inner ones (each under its top-level position)."""
    keys = account_keys(tx)
    top = [_instruction(i, raw, keys) for i, raw in enumerate(_list(_message(tx)["instructions"]))]
    inner: list[Instruction] = []
    for group in _list(_obj(tx["meta"]).get("innerInstructions")):
        position = _obj(group)["index"]
        for raw in _list(_obj(group)["instructions"]):
            inner.append(_instruction(cast(int, position), raw, keys))
    return top, inner


def program_calls(tx: Mapping[str, JsonValue]) -> list[tuple[str, str | None]]:
    """The top-level calls as ChainTx.program_calls holds them: (program id, parsed type)."""
    top, _ = instructions(tx)
    return [(i.program, i.type) for i in top]


# --- deltas (D181 (d)) ----------------------------------------------------------------------


@dataclass(frozen=True)
class _TokenRow:
    index: int
    mint: str
    mine: bool  # the token account belongs to an own address
    amount: int


def _unreadable(tx: Mapping[str, JsonValue]) -> E.RuleError:
    """A record whose token legs cannot be read: never a group with a leg dropped. The message
    names the signature only (no address, no amount)."""
    return E.RuleError(
        E.WALLET_RECORD_UNREADABLE,
        f"Die Transaktion {signature(tx)} lässt sich nicht vollständig lesen.",
    )


def _token_rows(
    tx: Mapping[str, JsonValue],
    key: str,
    keys: Sequence[str],
    own: Collection[str],
    own_token_accounts: Collection[str] | None,
    opened: Callable[[int], bool],
) -> list[_TokenRow]:
    """The token balance rows, each resolved as own or not: by its `owner` when the record
    carries one; else by the token account's address against the own token accounts the caller
    knows (`own_token_accounts`, the sync's getTokenAccountsByOwner set and every token account
    of its stored groups). A row without an owner stays unresolved, and the record unreadable,
    when the caller passed no set, or when its account is opened in this transaction and is
    not in the set (a new account the set cannot know yet)."""
    rows = []
    for raw in _list(_obj(tx["meta"]).get(key)):
        row = _obj(raw)
        index = cast(int, row["accountIndex"])
        owner = row.get("owner")
        if isinstance(owner, str):
            mine = owner in own
        elif own_token_accounts is not None and keys[index] in own_token_accounts:
            mine = True
        elif own_token_accounts is not None and not opened(index):
            mine = False
        else:
            raise _unreadable(tx)
        rows.append(
            _TokenRow(
                index=index,
                mint=str(row["mint"]),
                mine=mine,
                amount=int(str(_obj(row["uiTokenAmount"])["amount"])),
            )
        )
    return rows


@dataclass(frozen=True)
class _Balances:
    keys: list[str]
    pre: list[int]
    post: list[int]
    pre_tokens: list[_TokenRow]
    post_tokens: list[_TokenRow]
    fee: int

    def lamports(self, i: int) -> int:
        return self.post[i] - self.pre[i]

    def token_amounts(self, i: int) -> tuple[int, int]:
        before = sum(r.amount for r in self.pre_tokens if r.index == i)
        after = sum(r.amount for r in self.post_tokens if r.index == i)
        return before, after


def _calls_a_token_program(tx: Mapping[str, JsonValue]) -> bool:
    top, inner = instructions(tx)
    return any(i.program in TOKEN_PROGRAMS for i in [*top, *inner])


def _balances(
    tx: Mapping[str, JsonValue],
    own: Collection[str],
    own_token_accounts: Collection[str] | None = None,
) -> _Balances:
    meta = _obj(tx["meta"])
    keys = account_keys(tx)
    pre = [int(cast(int, v)) for v in _list(meta["preBalances"])]
    post = [int(cast(int, v)) for v in _list(meta["postBalances"])]
    if len(keys) != len(pre) or len(keys) != len(post):
        raise ValueError("a balance per account key")
    if failed(tx):
        # A failed transaction reverts everything but its fee: no token moved, so its token
        # rows are not read (a record that lacks them is still readable).
        return _Balances(keys, pre, post, [], [], int(cast(int, meta["fee"])))
    raw_pre = _list(meta.get("preTokenBalances"))
    raw_post = _list(meta.get("postTokenBalances"))
    if not raw_pre and not raw_post and _calls_a_token_program(tx):
        # A Token program call leaves a balance row behind (the account it touched); a record
        # without either array cannot say which token moved.
        raise _unreadable(tx)
    before = {cast(int, _obj(r)["accountIndex"]) for r in raw_pre}

    def opened(i: int) -> bool:
        return i not in before and pre[i] == 0

    return _Balances(
        keys=keys,
        pre=pre,
        post=post,
        pre_tokens=_token_rows(tx, "preTokenBalances", keys, own, own_token_accounts, opened),
        post_tokens=_token_rows(tx, "postTokenBalances", keys, own, own_token_accounts, opened),
        fee=int(cast(int, meta["fee"])),
    )


def _own_token_accounts(b: _Balances) -> dict[int, str]:
    """Each token account an own address owns before or after the transaction, by its index,
    with its mint (a closed account appears only before, a new one only after)."""
    return {r.index: r.mint for r in b.pre_tokens + b.post_tokens if r.mine}


def _sol_of_token_account(b: _Balances, i: int, mint: str) -> int:
    """A token account's SOL: its lamports, less the wrapped amount of a native-mint account
    (that amount is WSOL, never SOL twice)."""
    delta = b.lamports(i)
    if mint == native_mint():
        before, after = b.token_amounts(i)
        delta -= after - before
    return delta


def solana_deltas(
    tx: Mapping[str, JsonValue],
    own: Collection[str],
    own_token_accounts: Collection[str] | None = None,
) -> list[SolanaDelta]:
    """D181 (d): the own addresses' SOL delta over their main accounts and their token accounts
    (so a token account's rent moving from the main account is no delta), without the fee they
    paid; one delta per mint over the token balances they own. SOL first, then by mint.
    `own_token_accounts` resolves a token balance row that carries no `owner` (_token_rows);
    a row it cannot resolve raises `wallet.record_unreadable`, never a dropped leg."""
    b = _balances(tx, own, own_token_accounts)
    tokens = _own_token_accounts(b)
    mains = {i for i, k in enumerate(b.keys) if k in own and i not in tokens}
    sol = sum(b.lamports(i) for i in mains)
    sol += sum(_sol_of_token_account(b, i, mint) for i, mint in tokens.items())
    if b.keys and b.keys[0] in own:
        sol += b.fee
    deltas: list[SolanaDelta] = []
    if sol:
        rent = sol == -_foreign_rent(b, own) or sol == _received_rent(b, own)
        deltas.append(SolanaDelta(asset_key=AssetKey(chain=Chain.SOLANA), units=sol, rent=rent))
    per_mint: dict[str, int] = {}
    per_account: dict[str, dict[int, int]] = {}
    for sign, rows in ((-1, b.pre_tokens), (1, b.post_tokens)):
        for r in rows:
            if r.mine:
                per_mint[r.mint] = per_mint.get(r.mint, 0) + sign * r.amount
                accounts = per_account.setdefault(r.mint, {})
                accounts[r.index] = accounts.get(r.index, 0) + sign * r.amount
    for mint, units in sorted(per_mint.items()):
        if units:
            moved = max(per_account[mint].items(), key=lambda kv: (abs(kv[1]), -kv[0]))
            deltas.append(
                SolanaDelta(
                    asset_key=AssetKey(chain=Chain.SOLANA, contract=mint),
                    units=units,
                    token_account=b.keys[moved[0]],
                )
            )
    return deltas


def foreign_rent(
    tx: Mapping[str, JsonValue],
    own: Collection[str],
    own_token_accounts: Collection[str] | None = None,
) -> int:
    """The lamports the own fee payer placed in token accounts of others it opened in this
    transaction (a send that creates the recipient's account): D181 (d)'s rent as fee."""
    return _foreign_rent(_balances(tx, own, own_token_accounts), own)


def _foreign_rent(b: _Balances, own: Collection[str]) -> int:
    """Each new foreign account's SOL as _sol_of_token_account counts it: its lamports, less
    the wrapped amount of a wSOL account (that amount was sent, it is no rent)."""
    if not b.keys or b.keys[0] not in own:
        return 0
    before = {r.index for r in b.pre_tokens}
    opened = {r.index: r.mint for r in b.post_tokens if not r.mine and r.index not in before}
    return sum(
        _sol_of_token_account(b, i, mint)
        for i, mint in opened.items()
        if b.pre[i] == 0 and b.keys[i] not in own
    )


def received_rent(
    tx: Mapping[str, JsonValue],
    own: Collection[str],
    own_token_accounts: Collection[str] | None = None,
) -> int:
    """The rent a third party paid into own token accounts it opened in this transaction."""
    return _received_rent(_balances(tx, own, own_token_accounts), own)


def _received_rent(b: _Balances, own: Collection[str]) -> int:
    if b.keys and b.keys[0] in own:
        return 0
    before = {r.index for r in b.pre_tokens}
    opened = {r.index: r.mint for r in b.post_tokens if r.mine and r.index not in before}
    return sum(_sol_of_token_account(b, i, mint) for i, mint in opened.items() if b.pre[i] == 0)


# --- the group (D183, D187) ---------------------------------------------------------------


@dataclass(frozen=True)
class SolanaLeg:
    asset_key: AssetKey
    kind: MovementKind
    role: Role
    units: int  # base units, positive
    index: str


@dataclass(frozen=True)
class SolanaGroup:
    tx_hash: str
    slot: int
    occurred_at: datetime
    status: Literal["ok", "failed"]
    kind: GroupKind
    legs: tuple[SolanaLeg, ...]
    decoder: DecoderRow | None = None  # the row that read the program, if one did
    program: str | None = None  # the program the question names (not a plain one)
    question: str | None = None
    card: str | None = None
    hidden: bool = False  # spam: confirmed and kept off the screens (D187 (1))
    # A stake delegation to an account no own address holds yet: the account a „Ja“ registers
    # as the address of kind `stake` (§6 (10)).
    stake_account: str | None = None
    label: ProviderLabel | None = None
    fee_payer: bool = False
    signed: bool = False

    @property
    def fee_only(self) -> bool:
        """D186 (1)'s „a fee“: the group moves nothing but the fee it paid."""
        return bool(self.legs) and all(leg.role == "fee" for leg in self.legs)

    @property
    def decoder_listed(self) -> bool:
        return self.decoder is not None

    @property
    def assets(self) -> list[AssetKey]:
        seen: list[AssetKey] = []
        for leg in self.legs:
            if leg.asset_key not in seen:
                seen.append(leg.asset_key)
        return seen


def rows_lookup(rows: Sequence[DecoderRow]) -> DecoderLookup:
    """A decoder lookup over rows in precedence order (a workspace's rows before the public
    ones, D191): a row for the call's method before a method-less row of the same program.
    `decoders.decoder_for` gives the same answer from the database; this serves a caller that
    holds the rows."""

    def lookup(program: str, method: str | None) -> DecoderRow | None:
        mine = [r for r in rows if r.chain is Chain.SOLANA and r.program == program]
        exact = [r for r in mine if method is not None and r.method == method]
        return (exact or [r for r in mine if r.method is None] or [None])[0]

    return lookup


def known_token(mint: str, held: Collection[str]) -> bool:
    """D187 (1): a registry token or one the workspace holds with a confirmed lot."""
    return asset_registry().token(Chain.SOLANA, mint) is not None or mint in held


def _leg_index(leg: SolanaDelta, top: Sequence[Instruction]) -> str:
    """The leg's index in movement_hash (D182 (2)), from what every jsonParsed record carries:
    the top-level instructions' positions and program ids, never a parsed `type` or `info`
    (a provider may leave either out, and the hash must not change with it). A token leg's
    index is the first call to a Token program, a SOL leg's the first call to the System
    program; else the first call that is not a compute-budget, memo or account-creation call;
    else "0"."""
    wanted = TOKEN_PROGRAMS if leg.asset_key.contract is not None else {SYSTEM_PROGRAM}
    for ins in top:
        if ins.program in wanted:
            return str(ins.position)
    for ins in top:
        if ins.program not in _SETUP_PROGRAMS:
            return str(ins.position)
    return "0"


def _decoder(top: Sequence[Instruction], lookup: DecoderLookup) -> DecoderRow | None:
    """The first top-level call a row reads (D183 (4)); a program-specific call first, then the
    Token program's own rows (the wSOL wrap)."""
    found = [(ins, lookup(ins.program, ins.type)) for ins in top]
    rows = [(ins, row) for ins, row in found if row is not None]
    rows.sort(key=lambda pair: pair[0].program in PLAIN_PROGRAMS)
    return rows[0][1] if rows else None


def _contract_question(program: str | None) -> str:
    return f"Was war diese Transaktion bei {mask(program)}?" if program else QUESTION_UNKNOWN


def _stake_counterpart(b: _Balances, own: Collection[str], outgoing: bool) -> str | None:
    """The account the delegated SOL went to (or a withdrawal came from) that no own address
    holds: the largest lamport change of the opposite sign."""
    sign = 1 if outgoing else -1
    moved = [
        (sign * b.lamports(i), b.keys[i])
        for i in range(len(b.keys))
        if b.keys[i] not in own and sign * b.lamports(i) > 0
    ]
    return max(moved)[1] if moved else None


def classify_solana(
    tx: Mapping[str, JsonValue],
    own: Collection[str],
    lookup: DecoderLookup,
    *,
    held: Collection[str] = (),
    label: ProviderLabel | None = None,
    own_token_accounts: Collection[str] | None = None,
) -> SolanaGroup:
    """D183's shape rules over one record of the own addresses (the main address and its
    registered stake accounts). The caller links own counterparts and exchange hashes
    afterwards (D184) and routes the group (D186); nothing here is priced.
    `own_token_accounts` as in solana_deltas."""
    b = _balances(tx, own, own_token_accounts)
    top, _ = instructions(tx)
    payer = bool(b.keys) and b.keys[0] in own
    signed = bool(signers(tx) & set(own))
    base = dict(
        tx_hash=signature(tx),
        slot=cast(int, tx["slot"]),
        occurred_at=block_time(tx),
        label=label,
        fee_payer=payer,
        signed=signed,
    )
    fee = b.fee if payer else 0
    if failed(tx):
        legs = (_fee_leg(fee),) if fee else ()
        return SolanaGroup(status="failed", kind=GroupKind.FAILED, legs=legs, **base)

    deltas = solana_deltas(tx, own, own_token_accounts)
    sol = next((d for d in deltas if d.asset_key.contract is None), None)
    rest = [d for d in deltas if d.asset_key.contract is not None]
    rent_in = 0
    if sol is not None and sol.units < 0:
        folded = min(-sol.units, _foreign_rent(b, own))
        fee += folded
        units = sol.units + folded
        sol = sol.model_copy(update={"units": units, "rent": False}) if units else None
    elif sol is not None and sol.units > 0:
        rent_in = min(sol.units, _received_rent(b, own))
        units = sol.units - rent_in
        sol = sol.model_copy(update={"units": units, "rent": False}) if units else None
    shaped = ([sol] if sol is not None else []) + rest

    def leg(d: SolanaDelta, kind: MovementKind) -> SolanaLeg:
        return SolanaLeg(
            asset_key=d.asset_key,
            kind=kind,
            role="in" if d.units > 0 else "out",
            units=abs(d.units),
            index=_leg_index(d, top),
        )

    # D187 (1): a token credited while the own address signed nothing, neither listed nor held.
    spam = [
        d
        for d in shaped
        if not signed
        and d.units > 0
        and d.asset_key.contract is not None
        and not known_token(d.asset_key.contract, held)
    ]
    shaped = [d for d in shaped if d not in spam]
    decoder = _decoder(top, lookup)
    foreign = next((i.program for i in top if i.program not in PLAIN_PROGRAMS), None)
    ins = [d for d in shaped if d.units > 0]
    outs = [d for d in shaped if d.units < 0]

    kind: GroupKind
    legs: list[SolanaLeg]
    question: str | None = None
    card: str | None = None
    stake_account: str | None = None
    used = decoder
    if not shaped and spam:
        kind, legs, used = GroupKind.SPAM, [], None
    elif not shaped:
        kind, legs, used = GroupKind.TRANSFER, [], None
    elif _is_wrap(ins, outs):
        kind = GroupKind.WRAP
        legs = [leg(outs[0], MovementKind.INTERNAL_TRANSFER_OUT)]
        legs.append(leg(ins[0], MovementKind.INTERNAL_TRANSFER_IN))
        used = decoder if decoder is not None and decoder.kind is DecoderKind.WRAP else None
    elif decoder is not None and decoder.kind is DecoderKind.LP:
        kind, card = GroupKind.LP, E.GROUP_LP_UNDECIDED
        lp = MovementKind.LP_DEPOSIT if len(outs) >= len(ins) else MovementKind.LP_WITHDRAWAL
        legs = [leg(d, lp) for d in shaped]
    elif (
        decoder is not None
        and decoder.kind is DecoderKind.STAKE
        and len(shaped) == 1
        and sol is not None
    ):
        kind = GroupKind.STAKE
        out = sol.units < 0
        stake_account = _stake_counterpart(b, own, outgoing=out)
        move = MovementKind.INTERNAL_TRANSFER_OUT if out else MovementKind.INTERNAL_TRANSFER_IN
        legs = [leg(sol, move)]
        if stake_account is not None:
            question = f"Ist {mask(stake_account)} Ihr eigenes Stake-Konto?"
    elif decoder is not None and decoder.kind is DecoderKind.REWARD and ins and not outs:
        kind = GroupKind.REWARD
        legs = [leg(d, MovementKind.STAKING_REWARD) for d in ins]
    elif len(ins) == 1 and len(outs) == 1:
        # D183 (3): one asset given, another received. A decoder of another kind never
        # overrides the shape; an unlisted program asks once.
        kind = GroupKind.SWAP
        legs = [leg(outs[0], MovementKind.DISPOSAL), leg(ins[0], MovementKind.ACQUISITION)]
        if decoder is None or decoder.kind is not DecoderKind.SWAP:
            used = None
            question = _contract_question(foreign)
    elif len(shaped) == 1 and foreign is None:
        kind, card, used = GroupKind.TRANSFER, E.TRANSFER_UNMATCHED_LEG, None
        legs = [leg(shaped[0], MovementKind.UNCATEGORIZED)]
    elif len(shaped) == 1 and ins and not signed and shaped[0].asset_key.contract is not None:
        # A listed or held token credited through a program the own address did not call.
        kind, used = GroupKind.AIRDROP, None
        legs = [leg(shaped[0], MovementKind.AIRDROP)]
    elif len(shaped) == 1 and ins and not signed:
        kind, card, used = GroupKind.TRANSFER, E.TRANSFER_UNMATCHED_LEG, None
        legs = [leg(shaped[0], MovementKind.UNCATEGORIZED)]
    elif foreign is not None:
        kind, card, used = GroupKind.CONTRACT_CALL, E.GROUP_CONTRACT_CALL, None
        legs = [leg(d, MovementKind.UNCATEGORIZED) for d in shaped]
        question = _contract_question(foreign)
    else:
        kind, card, used = GroupKind.UNKNOWN, E.GROUP_UNKNOWN, None
        legs = [leg(d, MovementKind.UNCATEGORIZED) for d in shaped]
        question = QUESTION_UNKNOWN

    legs += [leg(d, MovementKind.SPAM) for d in spam]
    if rent_in:
        # Received without anything given: an airdrop (a lot); in a spam group a leg of that
        # hidden group, never a card of its own (it recurs with every spam attack).
        legs.append(
            SolanaLeg(
                asset_key=AssetKey(chain=Chain.SOLANA),
                kind=MovementKind.AIRDROP,
                role="in",
                units=rent_in,
                index="rent",
            )
        )
    if fee:
        legs.append(_fee_leg(fee))
    return SolanaGroup(
        status="ok",
        kind=kind,
        legs=_unique(legs),
        decoder=used,
        program=foreign if question and foreign else None,
        question=question,
        card=card,
        hidden=kind is GroupKind.SPAM,
        stake_account=stake_account,
        **base,
    )


def _fee_leg(units: int) -> SolanaLeg:
    return SolanaLeg(
        asset_key=AssetKey(chain=Chain.SOLANA),
        kind=MovementKind.FEE,
        role="fee",
        units=units,
        index="fee",
    )


def _is_wrap(ins: Sequence[SolanaDelta], outs: Sequence[SolanaDelta]) -> bool:
    """SOL into wSOL or back, the same units (the registry's wrap pair, D184 (3))."""
    if len(ins) != 1 or len(outs) != 1 or ins[0].units != -outs[0].units:
        return False
    pair = {ins[0].asset_key.contract, outs[0].asset_key.contract}
    return pair == {None, native_mint()}


def _unique(legs: Sequence[SolanaLeg]) -> tuple[SolanaLeg, ...]:
    """(index, role) is a movement's identity inside its transaction (movement_hash carries no
    asset): a second leg on the same index and role gets „.2“, a third „.3“."""
    seen: dict[tuple[str, str], int] = {}
    out = []
    for leg in legs:
        n = seen.get((leg.index, leg.role), 0) + 1
        seen[(leg.index, leg.role)] = n
        out.append(leg if n == 1 else replace(leg, index=f"{leg.index}.{n}"))
    return tuple(out)


# --- inflation rewards (§6 (10)) -----------------------------------------------------------


@dataclass(frozen=True)
class SolanaReward:
    """One epoch's inflation reward to an own stake account: a `staking_reward` dated the
    epoch's end (the effective slot's block time), its lot's day the Berlin day. Hashed over
    the chain, `epoch:<n>`, the stake account and `reward` (D181 (d))."""

    stake_account: str
    epoch: int
    units: int
    occurred_at: datetime
    kind: MovementKind = MovementKind.STAKING_REWARD
    index: str = "reward"
    role: Role = "in"
    effective_slot: int | None = None

    @property
    def tx_hash(self) -> str:
        return f"epoch:{self.epoch}"

    @property
    def day(self) -> date:
        return berlin_day(self.occurred_at)


def inflation_reward(
    stake_account: str, result: Mapping[str, JsonValue], epoch_end: datetime
) -> SolanaReward | None:
    """One `getInflationReward` entry (None or an amount of 0: no reward that epoch)."""
    amount = result.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        return None
    slot = result.get("effectiveSlot")
    return SolanaReward(
        stake_account=stake_account,
        epoch=cast(int, result["epoch"]),
        units=amount,
        occurred_at=epoch_end.astimezone(UTC),
        effective_slot=slot if isinstance(slot, int) else None,
    )


# --- the draft the sync writes -------------------------------------------------------------


@dataclass(frozen=True)
class DraftContext:
    """What only the write path knows: the wallet account, the own address's row, and each
    asset's row and decimals."""

    account_id: UUID
    address_id: UUID
    asset_ids: Mapping[AssetKey, UUID]
    decimals: Mapping[AssetKey, int] = field(default_factory=dict)


def quantity(units: int, decimals: int) -> Decimal:
    return Decimal(units).scaleb(-decimals)


def to_draft(group: SolanaGroup, ctx: DraftContext) -> MovementGroupDraft:
    """The group as a draft: one movement per leg, signed on the account (in positive, out and
    fee negative), in the asset's units (D182 (2): base units only until written)."""
    sol_decimals = chain_registry().chain(Chain.SOLANA).native_decimals
    movements = []
    for leg in group.legs:
        decimals = ctx.decimals.get(leg.asset_key)
        if decimals is None:
            if leg.asset_key.contract is not None:
                raise ValueError("a token's decimals come with its asset row")
            decimals = sol_decimals
        sign = 1 if leg.role == "in" else -1
        movements.append(
            AssetMovementDraft(
                account_id=ctx.account_id,
                asset_id=ctx.asset_ids[leg.asset_key],
                kind=leg.kind,
                quantity=sign * quantity(leg.units, decimals),
                occurred_at=group.occurred_at,
                tx_hash=group.tx_hash,
                source=MovementSource.CHAIN,
                address_id=ctx.address_id,
                index=leg.index,
            )
        )
    return MovementGroupDraft(
        account_id=ctx.account_id,
        chain=Chain.SOLANA,
        tx_hash=group.tx_hash,
        occurred_at=group.occurred_at,
        kind=group.kind,
        decoder=group.decoder.name if group.decoder is not None else None,
        movements=movements,
        question=group.question,
        label=group.label,
    )


# --- the sync's classifier (classify.classifier_for) ----------------------------------------


def has_ownerless_token_row(record: Mapping[str, JsonValue]) -> bool:
    """Whether a token balance row of the record carries no `owner` (records from before the
    field existed): only then does the source keep the own token accounts beside it."""
    meta = record.get("meta")
    if not isinstance(meta, Mapping):
        return False
    rows = [*(meta.get("preTokenBalances") or []), *(meta.get("postTokenBalances") or [])]
    return any(isinstance(r, Mapping) and not isinstance(r.get("owner"), str) for r in rows)


def with_token_accounts(
    record: Mapping[str, JsonValue], accounts: Collection[str], block_time: int | None = None
) -> dict[str, JsonValue]:
    """The record as the sync stores it: with the own token accounts under SYNC_KEY when a
    token row has no owner, and the block's time (from the listing or getBlockTime) when the
    record carries no `blockTime` (an old slot's); else unchanged."""
    out = dict(record)
    kept: dict[str, JsonValue] = {}
    if has_ownerless_token_row(record):
        kept["own_token_accounts"] = list(sorted(accounts))
    if not isinstance(record.get("blockTime"), int) and block_time is not None:
        kept["block_time"] = block_time
    if kept:
        out[SYNC_KEY] = kept
    return out


def readable_meta(record: Mapping[str, JsonValue]) -> bool:
    """Whether the record carries its `meta` (fee, balances, error); a very old slot's record
    may come back with `meta: null`, and such a record is a card (classify_tx)."""
    return isinstance(record.get("meta"), Mapping)


def recorded_token_accounts(record: Mapping[str, JsonValue]) -> list[str] | None:
    """The own token accounts the source kept beside the record, None when it kept none."""
    kept = record.get(SYNC_KEY)
    if not isinstance(kept, Mapping):
        return None
    accounts = kept.get("own_token_accounts")
    if not isinstance(accounts, list):
        return None
    return [a for a in accounts if isinstance(a, str)]


def token_decimals(record: Mapping[str, JsonValue]) -> dict[str, int]:
    """Each mint's decimals as the record's token balance rows state them (an unlisted
    token's asset row takes them, D180 amended (a))."""
    meta = record.get("meta")
    found: dict[str, int] = {}
    if not isinstance(meta, Mapping):
        return found
    for key in ("preTokenBalances", "postTokenBalances"):
        for row in meta.get(key) or []:
            if not isinstance(row, Mapping):
                continue
            amount = row.get("uiTokenAmount")
            decimals = amount.get("decimals") if isinstance(amount, Mapping) else None
            mint = row.get("mint")
            if isinstance(mint, str) and isinstance(decimals, int) and 0 <= decimals <= 18:
                found.setdefault(mint, decimals)
    return found


def _unreadable_group(record: Mapping[str, JsonValue], address: str) -> SolanaGroup:
    """wallet.record_unreadable as a card: the question, and the fee when the address paid it
    and the record states it; never a stopped sync, never a token leg guessed. A group with a
    question is never booked as „a fee“ (rules/review.route_group), so the fee waits with the
    card and is booked once a person answers; the address's SOL waits too („wartet auf
    Antwort“), and a token the record moved is a mismatch on the wallet page until then
    (D186 (2)). A record without its meta states no fee: the card has no leg."""
    keys = account_keys(record)
    payer = bool(keys) and keys[0] == address
    meta = record.get("meta")
    fee = meta.get("fee") if isinstance(meta, Mapping) else None
    paid = payer and isinstance(fee, int) and not isinstance(fee, bool) and fee > 0
    return SolanaGroup(
        tx_hash=signature(record),
        slot=cast(int, record["slot"]),
        occurred_at=block_time(record),
        status="ok",
        kind=GroupKind.UNKNOWN,
        legs=(_fee_leg(cast(int, fee)),) if paid else (),
        question=QUESTION_UNREADABLE,
        card=E.GROUP_UNKNOWN,
        fee_payer=payer,
    )


def classify_tx(tx: ChainTx, ctx: ClassifyContext) -> ChainGroup | None:
    """The sync's Solana classifier, over the stored record (`tx.raw`) of the synced address.

    Each own address is its own run, its movements hashed and reconciled per address, so the
    deltas are those of `ctx.address` alone: a payment between two own addresses is an `out`
    on one run and an `in` on the other, never one leg twice. The other own addresses only
    answer the stake question: a delegation to (or from) an address the wallet already holds
    asks nothing. A record whose token legs cannot be read is a card (`_unreadable_group`),
    never a stopped sync. Helius's labels are not read: they set no kind, amount or
    confirmation (D192)."""
    record = tx.raw
    held = {k.contract for k in ctx.held if k.chain is Chain.SOLANA and k.contract}
    decoders = list(ctx.decoders)

    def lookup(program: str, method: str | None) -> DecoderRow | None:
        return decoder_for(Chain.SOLANA, program, method, decoders)

    try:
        if not readable_meta(record):
            raise _unreadable(record)
        group = classify_solana(
            record,
            {ctx.address},
            lookup,
            held=held,
            own_token_accounts=recorded_token_accounts(record),
        )
    except E.RuleError as exc:
        if exc.code != E.WALLET_RECORD_UNREADABLE:
            raise
        group = _unreadable_group(record, ctx.address)
    question = group.question
    if group.stake_account is not None and group.stake_account in ctx.own_addresses:
        question = None
    decimals = token_decimals(record)
    legs = [
        ChainLeg(
            asset_key=leg.asset_key,
            units=leg.units,
            role=leg.role,
            kind=leg.kind,
            index=leg.index,
            decimals=decimals.get(leg.asset_key.contract) if leg.asset_key.contract else None,
            spam=leg.kind is MovementKind.SPAM,
        )
        for leg in group.legs
    ]
    row = group.decoder
    return ChainGroup(
        tx_hash=group.tx_hash,
        occurred_at=group.occurred_at,
        block=group.slot,
        kind=group.kind,
        legs=legs,
        decoder=None if row is None else (row.name or row.program),
        decoder_listed=group.decoder_listed,
        question=question,
    )
