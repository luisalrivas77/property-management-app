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
- the rent of a third party's token account the own address opened beside a token leg (a send
  that creates the recipient's account) is read from the record: the new account's own lamports,
  folded into the `fee` movement (D181 (d) „the rent as fee“), never fetched from the RPC;
- the rent a third party paid into an own token account it opened is a SOL `in` leg of its own
  (index `rent`, uncategorized), never part of the shape and never spam: the own address can
  close the account and keep it, and the reconciliation counts it (D186 (2));
- a token credited through the Token programs alone is a transfer; through any other program,
  without a decoder that reads it, an `airdrop` (review); spam by D187 (1) either way;
- a leg's index is the first top-level instruction that names one of its accounts, else the
  first transfer of its kind, else the first instruction that is not a compute-budget, memo or
  account-creation call, else "0"; a second leg on the same index and role gets ".2", ".3".
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
from hausbuch.rules.chain.registry import registry as chain_registry
from hausbuch.rules.household.assets import registry as asset_registry
from hausbuch.schemas.chain import (
    AssetKey,
    Chain,
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
# Calls that set a transaction up and never carry a leg (the third step of the index rule).
_SETUP_PROGRAMS = frozenset({COMPUTE_BUDGET_PROGRAM, MEMO_PROGRAM, ATA_PROGRAM})
_TOKEN_MOVES = frozenset(
    {"transfer", "transferChecked", "mintTo", "mintToChecked", "burn", "burnChecked"}
)
_SOL_MOVES = frozenset({"transfer", "transferWithSeed", "createAccount", "createAccountWithSeed"})

QUESTION_UNKNOWN = "Was ist diese Transaktion?"

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
    value = tx.get("blockTime")
    if not isinstance(value, int):
        raise ValueError("a finalized transaction carries its blockTime")
    return datetime.fromtimestamp(value, UTC)


@dataclass(frozen=True)
class Instruction:
    """One instruction, top-level or inner, as jsonParsed gives it."""

    position: int  # the top-level instruction it belongs to
    program: str
    type: str | None
    accounts: frozenset[str]  # every address its parsed info names


def _named(info: object) -> set[str]:
    if isinstance(info, Mapping):
        return {v for value in info.values() for v in _named(value)}
    if isinstance(info, list):
        return {v for value in info for v in _named(value)}
    return {info} if isinstance(info, str) else set()


def _instruction(position: int, raw: object, keys: Sequence[str]) -> Instruction:
    ins = _obj(raw)
    program = ins.get("programId")
    if program is None and isinstance(ins.get("programIdIndex"), int):
        program = keys[cast(int, ins["programIdIndex"])]
    parsed = ins.get("parsed")
    kind = _obj(parsed).get("type") if isinstance(parsed, Mapping) else None
    info = _obj(parsed).get("info") if isinstance(parsed, Mapping) else None
    named = _named(info) | {str(a) for a in _list(ins.get("accounts")) if isinstance(a, str)}
    return Instruction(position, str(program), str(kind) if kind else None, frozenset(named))


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
    owner: str | None
    amount: int


def _token_rows(meta: Mapping[str, JsonValue], key: str) -> list[_TokenRow]:
    rows = []
    for raw in _list(meta.get(key)):
        row = _obj(raw)
        owner = row.get("owner")
        rows.append(
            _TokenRow(
                index=cast(int, row["accountIndex"]),
                mint=str(row["mint"]),
                owner=str(owner) if isinstance(owner, str) else None,
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


def _balances(tx: Mapping[str, JsonValue]) -> _Balances:
    meta = _obj(tx["meta"])
    keys = account_keys(tx)
    pre = [int(cast(int, v)) for v in _list(meta["preBalances"])]
    post = [int(cast(int, v)) for v in _list(meta["postBalances"])]
    if len(keys) != len(pre) or len(keys) != len(post):
        raise ValueError("a balance per account key")
    return _Balances(
        keys=keys,
        pre=pre,
        post=post,
        pre_tokens=_token_rows(meta, "preTokenBalances"),
        post_tokens=_token_rows(meta, "postTokenBalances"),
        fee=int(cast(int, meta["fee"])),
    )


def _own_token_accounts(b: _Balances, own: Collection[str]) -> dict[int, str]:
    """Each token account an own address owns before or after the transaction, by its index,
    with its mint (a closed account appears only before, a new one only after)."""
    return {r.index: r.mint for r in b.pre_tokens + b.post_tokens if r.owner in own}


def _sol_of_token_account(b: _Balances, i: int, mint: str) -> int:
    """A token account's SOL: its lamports, less the wrapped amount of a native-mint account
    (that amount is WSOL, never SOL twice)."""
    delta = b.lamports(i)
    if mint == native_mint():
        before, after = b.token_amounts(i)
        delta -= after - before
    return delta


def solana_deltas(tx: Mapping[str, JsonValue], own: Collection[str]) -> list[SolanaDelta]:
    """D181 (d): the own addresses' SOL delta over their main accounts and their token accounts
    (so a token account's rent moving from the main account is no delta), without the fee they
    paid; one delta per mint over the token balances they own. SOL first, then by mint."""
    b = _balances(tx)
    tokens = _own_token_accounts(b, own)
    mains = {i for i, k in enumerate(b.keys) if k in own and i not in tokens}
    sol = sum(b.lamports(i) for i in mains)
    sol += sum(_sol_of_token_account(b, i, mint) for i, mint in tokens.items())
    if b.keys and b.keys[0] in own:
        sol += b.fee
    deltas: list[SolanaDelta] = []
    if sol:
        rent = sol == -foreign_rent(tx, own) or sol == received_rent(tx, own)
        deltas.append(SolanaDelta(asset_key=AssetKey(chain=Chain.SOLANA), units=sol, rent=rent))
    per_mint: dict[str, int] = {}
    per_account: dict[str, dict[int, int]] = {}
    for sign, rows in ((-1, b.pre_tokens), (1, b.post_tokens)):
        for r in rows:
            if r.owner in own:
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


def foreign_rent(tx: Mapping[str, JsonValue], own: Collection[str]) -> int:
    """The lamports the own fee payer placed in token accounts of others it opened in this
    transaction (a send that creates the recipient's account): D181 (d)'s rent as fee."""
    b = _balances(tx)
    if not b.keys or b.keys[0] not in own:
        return 0
    opened = {r.index for r in b.post_tokens if r.owner not in own} - {
        r.index for r in b.pre_tokens
    }
    return sum(b.post[i] for i in opened if b.pre[i] == 0 and b.keys[i] not in own)


def received_rent(tx: Mapping[str, JsonValue], own: Collection[str]) -> int:
    """The rent a third party paid into own token accounts it opened in this transaction."""
    b = _balances(tx)
    if b.keys and b.keys[0] in own:
        return 0
    opened = {r.index for r in b.post_tokens if r.owner in own} - {r.index for r in b.pre_tokens}
    return sum(
        _sol_of_token_account(b, i, mint)
        for i, mint in _own_token_accounts(b, own).items()
        if i in opened and b.pre[i] == 0
    )


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


def _leg_index(
    leg: SolanaDelta,
    accounts: set[str],
    top: Sequence[Instruction],
    inner: Sequence[Instruction],
) -> str:
    for ins in sorted([*top, *inner], key=lambda i: i.position):
        if ins.accounts & accounts:
            return str(ins.position)
    token = leg.asset_key.contract is not None
    for ins in top:
        if token and ins.program in TOKEN_PROGRAMS and ins.type in _TOKEN_MOVES:
            return str(ins.position)
        if not token and ins.program == SYSTEM_PROGRAM and ins.type in _SOL_MOVES:
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
) -> SolanaGroup:
    """D183's shape rules over one record of the own addresses (the main address and its
    registered stake accounts). The caller links own counterparts and exchange hashes
    afterwards (D184) and routes the group (D186); nothing here is priced."""
    b = _balances(tx)
    top, inner = instructions(tx)
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

    deltas = solana_deltas(tx, own)
    sol = next((d for d in deltas if d.asset_key.contract is None), None)
    rest = [d for d in deltas if d.asset_key.contract is not None]
    rent_in = 0
    if sol is not None and sol.units < 0:
        folded = min(-sol.units, foreign_rent(tx, own))
        fee += folded
        units = sol.units + folded
        sol = sol.model_copy(update={"units": units, "rent": False}) if units else None
    elif sol is not None and sol.units > 0:
        rent_in = min(sol.units, received_rent(tx, own))
        units = sol.units - rent_in
        sol = sol.model_copy(update={"units": units, "rent": False}) if units else None
    shaped = ([sol] if sol is not None else []) + rest

    tokens = _own_token_accounts(b, own)
    mains = {k for k in b.keys if k in own}

    def accounts(d: SolanaDelta) -> set[str]:
        if d.asset_key.contract is None:
            return set(mains)
        return {b.keys[i] for i, mint in tokens.items() if mint == d.asset_key.contract}

    def leg(d: SolanaDelta, kind: MovementKind) -> SolanaLeg:
        return SolanaLeg(
            asset_key=d.asset_key,
            kind=kind,
            role="in" if d.units > 0 else "out",
            units=abs(d.units),
            index=_leg_index(d, accounts(d), top, inner),
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
        legs.append(
            SolanaLeg(
                asset_key=AssetKey(chain=Chain.SOLANA),
                kind=MovementKind.UNCATEGORIZED,
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
