"""The exchanges' pure rules (docs/phases/phase-6.md D166 as amended 13 Nov 2026, D175,
D178, §8; fixtures tests/fixtures/phase6/bison_csv.yaml and binance_csv.yaml). Built in
phase 6 step 5a.

- `classify(line)`: a BISON row (MovementLine) to its movement kind; `review_needed` says
  whether code may confirm it.
- `bison_movements(lines)`: BISON's rows to movements, one per row, each with its dedup hash
  (the Transaction ID, else the row's content and its position among identical rows).
- `binance_movements(rows, offset)`: Binance's transaction history to movements: the trade
  rows of one second summed per coin and role, a EUR leg folded into the coin leg's
  eur_amount; every other row one movement. The rows' times are UTC; `offset` is the export's
  time zone (D178 amended (4), 16 Nov 2026), which only the dedup key reads: the key keeps
  the time as the file writes it.
- `trade_groups(movements, provider)`, `trade_fee_eur(legs)`: which movements are one trade
  (phase 6 D178 amended (3), migration 0043: one `movement_group` of kind swap per trade, so
  phase 7's lots read its legs together, D188 (1), (4)), and the fee a trade paid in EUR, which
  its acquisition carries as `fee_eur` (a EUR movement has no price for the lots to value).
- `match_withdrawals(withdrawals, history)`: the withdrawal history's row that completes each
  withdrawal (same coin, Amount + Fee = the amount sent, within one hour before).
- `positions(movements)`, `reconcile(held, typed)`: the typed balance per asset (D166, D178
  (6)): Σ movements per asset, EUR plus Σ eur_amount of the acquisitions and disposals.
  Coins must agree exactly; EUR within the rounding bound `eur_tolerance` (D178 (6) as
  amended 13 Nov 2026): each EUR leg code rounded to the cent is off by at most half a cent,
  and the exchange's own display of the typed balance by one more half cent.

Nothing here reads a User ID or an address: the parsers drop them (D178 (2)).
"""

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from pydantic import ValidationError

from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.chain import GroupKind
from hausbuch.schemas.household import (
    AssetCheck,
    BinanceRow,
    ExchangeMovement,
    MovementKind,
    MovementLine,
    MovementStatus,
    WithdrawalRecord,
)

EUR = "EUR"
# The account's provider names its exchange (D178 (7)); the export must be that exchange's.
BINANCE, BISON = "Binance", "BISON"
EXCHANGE_PROVIDERS = (BINANCE, BISON)
CENT = Decimal("0.01")
HALF_CENT = Decimal("0.005")
# The exchanges whose trade EUR legs code rounds to the cent (Binance writes up to 8
# decimals); BISON's EUR amounts are taken as given.
ROUNDS_EUR_LEGS = frozenset({BINANCE})
# D178 (3), (4): the exchanges whose transaction export lists a withdrawal gross, with no
# fee, network or hash; its withdrawal history (Binance's Auszahlungsverlauf) completes it.
# Until then its units are not what arrived (D181 (c) amended 16 Nov 2026, review).
WITHDRAWALS_COMPLETED_BY_HISTORY = frozenset({BINANCE})
# D178 (3): the history's row lies within one hour before the transaction history's row.
WITHDRAWAL_WINDOW = timedelta(hours=1)
COMPLETED = "Completed"

_INWARD = frozenset(
    {
        MovementKind.ACQUISITION,
        MovementKind.INTERNAL_TRANSFER_IN,
        MovementKind.STAKING_REWARD,
        MovementKind.CASHBACK_REWARD,
        MovementKind.UNCATEGORIZED,
    }
)
TRADE_KINDS = frozenset({MovementKind.ACQUISITION, MovementKind.DISPOSAL})
# Phase 6 D178 amended (3), migration 0043: the kinds of a trade's legs, and the kind of the
# movement group that ties them (a group without a chain is always this kind, a CHECK).
TRADE_LEG_KINDS = TRADE_KINDS | {MovementKind.FEE}
TRADE_GROUP_KIND = GroupKind.SWAP

# Binance's trade operations by role (D178 (3)); a Convert row's role is its sign.
_BINANCE_IN = frozenset({"Transaction Buy", "Transaction Revenue"})
_BINANCE_OUT = frozenset({"Transaction Spend", "Transaction Sold"})
_BINANCE_FEE = frozenset({"Transaction Fee"})
_BINANCE_CONVERT = "Binance Convert"
BINANCE_TRADE_OPERATIONS = _BINANCE_IN | _BINANCE_OUT | _BINANCE_FEE | {_BINANCE_CONVERT}


def cents(amount: Decimal) -> Decimal:
    """A euro amount to the cent, half up (eur_amount is numeric(14,2))."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def canonical(amount: Decimal) -> str:
    """An amount as hash text: no trailing zeros, no exponent, so 0.0010 and 0.001 agree."""
    text = format(amount.normalize(), "f")
    return "0" if text in ("-0", "0") else text


def movement_hash(exchange: str, key: Sequence[str], position: int) -> str:
    """D166, D178 (5): SHA-256 over the exchange's name, the row's key and its position among
    identical keys of the same file (0 for the first)."""
    text = "\x1f".join([exchange, *key, str(position)])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _positions_of(keys: Iterable[tuple[str, ...]]) -> list[int]:
    seen: dict[tuple[str, ...], int] = {}
    out = []
    for key in keys:
        out.append(seen.get(key, 0))
        seen[key] = out[-1] + 1
    return out


def review_needed(kind: MovementKind, asset: str) -> bool:
    """D166: code confirms a trade, a reward and an EUR transfer; a crypto Deposit (a bonus?),
    a crypto withdrawal (where to?) and an unknown row wait for a person."""
    if kind in (MovementKind.CASHBACK_REWARD, MovementKind.UNCATEGORIZED):
        return True
    if kind in (MovementKind.INTERNAL_TRANSFER_IN, MovementKind.INTERNAL_TRANSFER_OUT):
        return asset != EUR
    return False


def _status(kind: MovementKind, asset: str) -> MovementStatus:
    return MovementStatus.PROPOSED if review_needed(kind, asset) else MovementStatus.CONFIRMED


def _question(kind: MovementKind) -> str | None:
    return E.EXCHANGE_DEPOSIT_QUESTION if kind is MovementKind.CASHBACK_REWARD else None


# --- BISON (D166 amended) ------------------------------------------------------------------


def classify(line: MovementLine) -> MovementKind:
    """D166: a BISON row's kind by its type and whether the asset is EUR."""
    kind, eur = line.type.strip().casefold(), line.asset == EUR
    if kind == "buy":
        return MovementKind.ACQUISITION
    if kind == "sell":
        return MovementKind.DISPOSAL
    if kind == "deposit":
        return MovementKind.INTERNAL_TRANSFER_IN if eur else MovementKind.CASHBACK_REWARD
    if kind in ("withdraw", "withdrawal"):
        return MovementKind.INTERNAL_TRANSFER_OUT
    if kind.startswith(("staking", "reward")):
        return MovementKind.STAKING_REWARD
    return MovementKind.UNCATEGORIZED


def _bison_key(line: MovementLine) -> tuple[str, ...]:
    if line.external_ref is not None:
        return ("id", line.external_ref)
    return (
        "row",
        line.occurred_at.isoformat(),
        line.type,
        line.asset,
        canonical(line.quantity),
        canonical(line.eur_amount) if line.eur_amount is not None else "",
        canonical(line.fee_quantity) if line.fee_quantity is not None else "",
    )


def _movement(where: str, **fields) -> ExchangeMovement:  # noqa: ANN003
    """An ExchangeMovement, or exchange.unreadable naming the row or second whose value is
    out of the movement's range (a positive Transaction Fee, say). The message names no
    value and the validation error is not chained: its text would quote the cell."""
    try:
        return ExchangeMovement(**fields)
    except ValidationError:
        raise RuleError(E.EXCHANGE_UNREADABLE, f"{where}: a value is out of range") from None


def bison_movements(lines: Sequence[MovementLine]) -> list[ExchangeMovement]:
    """One movement per row, in the file's order. A Transaction ID repeated in the file gives
    the same hash twice; the import skips the second (D166)."""
    keys = [_bison_key(line) for line in lines]
    positions = [
        0 if line.external_ref is not None else p
        for line, p in zip(lines, _positions_of(keys), strict=True)
    ]
    out = []
    for n, (line, key, position) in enumerate(zip(lines, keys, positions, strict=True), 1):
        kind = classify(line)
        sign = 1 if kind in _INWARD else -1
        quantity = line.quantity * sign
        if line.asset == EUR:
            eur = cents(quantity)
        elif line.eur_amount is None:
            eur = None
        else:
            eur = -line.eur_amount if kind is MovementKind.ACQUISITION else line.eur_amount
        out.append(
            _movement(
                f"row {n}",
                occurred_at=line.occurred_at,
                asset=line.asset,
                kind=kind,
                status=_status(kind, line.asset),
                quantity=quantity,
                eur_amount=eur,
                fee_eur=line.fee_eur,
                fee_quantity=line.fee_quantity,
                external_ref=line.external_ref,
                network=line.network,
                tx_hash=line.tx_hash,
                question=_question(kind),
                movement_hash=movement_hash("bison", key, position),
                position=position,
            )
        )
    return out


# --- Binance (D178) ------------------------------------------------------------------------


def _role(row: BinanceRow) -> str:
    if row.operation in _BINANCE_IN:
        return "in"
    if row.operation in _BINANCE_OUT:
        return "out"
    if row.operation in _BINANCE_FEE:
        return "fee"
    return "in" if row.change > 0 else "out"  # a Convert row


def _trade(at: datetime, rows: list[BinanceRow]) -> list[tuple[tuple[str, ...], dict]]:
    """One second's trade rows to its legs (D178 (3)): the coin received, the coin given and
    each fee coin, summed; the EUR side folded into eur_amount."""
    sums: dict[tuple[str, str], Decimal] = {}
    for row in rows:
        role = _role(row)
        sums[(role, row.coin)] = sums.get((role, row.coin), Decimal(0)) + row.change
    received = [coin for role, coin in sums if role == "in"]
    given = [coin for role, coin in sums if role == "out"]
    if len(received) != 1 or len(given) != 1 or received == given:
        raise RuleError(
            E.EXCHANGE_BINANCE_AMBIGUOUS_TRADE,
            f"the trade rows of {at.isoformat()} do not give one coin for one other",
        )
    coin_in, coin_out = received[0], given[0]
    amount_in, amount_out = sums[("in", coin_in)], sums[("out", coin_out)]
    legs: list[tuple[tuple[str, ...], dict]] = []
    second = at.isoformat()
    if coin_in != EUR:
        legs.append(
            (
                (second, "in", coin_in, canonical(amount_in)),
                {
                    "asset": coin_in,
                    "kind": MovementKind.ACQUISITION,
                    "quantity": amount_in,
                    "eur_amount": cents(amount_out) if coin_out == EUR else None,
                },
            )
        )
    if coin_out != EUR:
        legs.append(
            (
                (second, "out", coin_out, canonical(amount_out)),
                {
                    "asset": coin_out,
                    "kind": MovementKind.DISPOSAL,
                    "quantity": amount_out,
                    "eur_amount": cents(amount_in) if coin_in == EUR else None,
                },
            )
        )
    for (role, coin), amount in sorted(sums.items(), key=lambda item: item[0][1]):
        if role != "fee":
            continue
        legs.append(
            (
                (second, "fee", coin, canonical(amount)),
                {
                    "asset": coin,
                    "kind": MovementKind.FEE,
                    "quantity": amount,
                    "fee_quantity": -amount,
                    "eur_amount": cents(amount) if coin == EUR else None,
                },
            )
        )
    return legs


def _other(row: BinanceRow, written: datetime) -> tuple[tuple[str, ...], dict]:
    """D178 (3): a row that is no trade leg, one movement each; `written` its second as the
    file writes it (the dedup key's)."""
    if row.operation == "Deposit":
        kind = (
            MovementKind.INTERNAL_TRANSFER_IN if row.coin == EUR else MovementKind.CASHBACK_REWARD
        )
    elif row.operation == "Withdraw":
        kind = MovementKind.INTERNAL_TRANSFER_OUT
    else:
        kind = MovementKind.UNCATEGORIZED
    key = (written.isoformat(), row.operation, row.coin, canonical(row.change))
    return key, {
        "asset": row.coin,
        "kind": kind,
        "quantity": row.change,
        "eur_amount": cents(row.change) if row.coin == EUR else None,
    }


def binance_movements(
    rows: Sequence[BinanceRow], offset: timedelta = timedelta(0)
) -> list[ExchangeMovement]:
    """D178 (3), (5): the transaction history to movements, oldest second first; within a
    second the trade's acquisition, disposal and fees (by coin), then the other rows in the
    order Binance wrote them. Raises exchange.binance_ambiguous_trade for a second whose
    trade rows do not give exactly one coin for one other.

    D178 amended (4) (16 Nov 2026): the rows' `occurred_at` is UTC, the export's own time
    less `offset` (the zone the export was written in, binance_csv.utc_offset). The dedup key
    keeps the second as the file writes it, labelled UTC as every import before the
    amendment hashed it, so a stored row's movement_hash never changes when its time is
    corrected, and a longer export of the same zone re-uploaded skips every stored row."""
    by_second: dict[datetime, list[BinanceRow]] = {}
    for row in rows:
        by_second.setdefault(row.occurred_at, []).append(row)
    entries: list[tuple[datetime, tuple[str, ...], dict]] = []
    for at in sorted(by_second):
        written = at + offset  # the file's own second, the dedup key's (D178 amended (4))
        trade = [r for r in by_second[at] if r.operation in BINANCE_TRADE_OPERATIONS]
        if trade:
            entries.extend((at, *leg) for leg in _trade(written, trade))
        entries.extend(
            (at, *_other(r, written))
            for r in by_second[at]
            if r.operation not in BINANCE_TRADE_OPERATIONS
        )
    out = []
    for (at, key, fields), position in zip(
        entries, _positions_of(key for _, key, _ in entries), strict=True
    ):
        kind, asset = fields["kind"], fields["asset"]
        out.append(
            _movement(
                f"the second {at.isoformat()}",
                occurred_at=at,
                status=_status(kind, asset),
                question=_question(kind),
                movement_hash=movement_hash("binance", key, position),
                position=position,
                **fields,
            )
        )
    # D178 amended (3): a fee paid in EUR is also the acquisition's fee_eur (D188 (1)); the
    # EUR fee movement stays, since the EUR position counts it (D178 (6)).
    for legs in trade_groups(out, BINANCE):
        fee = trade_fee_eur([out[i] for i in legs])
        for i in legs:
            if fee is not None and out[i].kind is MovementKind.ACQUISITION:
                out[i] = out[i].model_copy(update={"fee_eur": fee})
    return out


# --- Trades as groups (D178 amended (3), migration 0043) -----------------------------------


def trade_groups(movements: Sequence["Moved"], provider: str) -> list[list[int]]:
    """Which movements are one trade, by index, oldest first. Binance: the acquisition,
    disposal and fee movements of one second (`binance_movements` writes those kinds only for
    a trade's legs and refuses a second with two trades); BISON: each Buy or Sell row alone
    (its EUR leg is on the row, D166 amended; its fee is the row's fee_quantity, never a lot
    cost). A group holds at least one acquisition or disposal; any other movement is in none.
    The caller passes the movements an export wrote (the import's new ones, or the stored ones
    `hausbuch group-exchange-trades` reads), never a person's answer."""
    if provider == BISON:
        return [[i] for i, m in enumerate(movements) if m.kind in TRADE_KINDS]
    if provider != BINANCE:
        raise ValueError(f"no trade rule for the exchange {provider!r}")
    by_second: dict[datetime, list[int]] = {}
    for i, m in enumerate(movements):
        if m.kind in TRADE_LEG_KINDS:
            by_second.setdefault(m.occurred_at, []).append(i)
    return [
        legs for legs in by_second.values() if any(movements[i].kind in TRADE_KINDS for i in legs)
    ]


def trade_fee_eur(legs: Sequence["Moved"]) -> Decimal | None:
    """The fee one trade paid in EUR, to the cent, as its acquisition's `fee_eur` (D188 (1):
    the cost includes the fee). None when the trade paid no fee in EUR or acquired no coin
    (a sale's EUR fee lowers its proceeds, phase 8's to read from the group). A fee in a coin
    stays a fee movement of the group only, valued on the day by the lots (D188 (4))."""
    fees = [m for m in legs if m.kind is MovementKind.FEE and m.asset == EUR]
    acquired = any(m.kind is MovementKind.ACQUISITION and m.asset != EUR for m in legs)
    if not fees or not acquired:
        return None
    return cents(-sum((m.quantity for m in fees), Decimal(0)))


class Moved(Protocol):
    """What the withdrawal match and the positions need of a movement: a parsed one
    (ExchangeMovement) or a stored row."""

    @property
    def asset(self) -> str: ...
    @property
    def kind(self) -> MovementKind: ...
    @property
    def quantity(self) -> Decimal: ...
    @property
    def eur_amount(self) -> Decimal | None: ...
    @property
    def occurred_at(self) -> datetime: ...


def match_withdrawals(
    withdrawals: Sequence[Moved], history: Sequence[WithdrawalRecord]
) -> dict[int, WithdrawalRecord]:
    """D178 (3): for each withdrawal (by its index), the history's Completed row of the same
    coin whose Amount + Fee equals the amount sent and whose time lies 0 to 60 minutes before
    it; the nearest such row when several fit, each row used once. Withdrawals are taken
    oldest first; a withdrawal with no such row is left out (the to-do of D178 (4))."""
    used: set[int] = set()
    found: dict[int, WithdrawalRecord] = {}
    order = sorted(range(len(withdrawals)), key=lambda i: withdrawals[i].occurred_at)
    for i in order:
        w = withdrawals[i]
        if w.kind is not MovementKind.INTERNAL_TRANSFER_OUT or w.asset == EUR:
            continue
        sent = -w.quantity
        fits = [
            j
            for j, h in enumerate(history)
            if j not in used
            and h.status == COMPLETED
            and h.coin == w.asset
            and h.amount + h.fee == sent
            and timedelta(0) <= w.occurred_at - h.occurred_at <= WITHDRAWAL_WINDOW
        ]
        if not fits:
            continue
        best = min(fits, key=lambda j: (w.occurred_at - history[j].occurred_at, j))
        used.add(best)
        found[i] = history[best]
    return found


def completed(movement: ExchangeMovement, record: WithdrawalRecord) -> ExchangeMovement:
    """The withdrawal with the history's network, fee and hash (D175, D178 (3))."""
    return movement.model_copy(
        update={
            "fee_quantity": record.fee,
            "network": record.network,
            "tx_hash": record.tx_hash,
            "external_ref": record.tx_hash,
        }
    )


# --- The typed balance (D166, D178 (6)) ----------------------------------------------------


def positions(
    movements: Iterable[Moved], held_before: dict[str, Decimal] | None = None
) -> dict[str, Decimal]:
    """Σ quantity per asset; EUR also takes every acquisition's and disposal's eur_amount (a
    trade's EUR leg is never its own movement)."""
    held = dict(held_before or {})
    for m in movements:
        held[m.asset] = held.get(m.asset, Decimal(0)) + m.quantity
        if m.kind in TRADE_KINDS and m.eur_amount is not None:
            held[EUR] = held.get(EUR, Decimal(0)) + m.eur_amount
    return held


def rounded_eur_legs(movements: Iterable[Moved], provider: str) -> int:
    """How many EUR legs code rounded to the cent among these movements: on an exchange in
    ROUNDS_EUR_LEGS every acquisition and disposal with an eur_amount (the same legs
    `positions` adds to EUR), else none."""
    if provider not in ROUNDS_EUR_LEGS:
        return 0
    return sum(1 for m in movements if m.kind in TRADE_KINDS and m.eur_amount is not None)


def eur_tolerance(rounded_legs: int) -> Decimal:
    """D178 (6) as amended: the most the EUR position can differ from the exchange's display
    by rounding alone, half a cent per rounded leg plus half a cent for the display."""
    return HALF_CENT * (rounded_legs + 1)


def agrees(asset: str, difference: Decimal, eur_rounded_legs: int = 0) -> bool:
    """A typed asset agrees with its position: a coin exactly, EUR within eur_tolerance."""
    if asset == EUR:
        return abs(difference) <= eur_tolerance(eur_rounded_legs)
    return difference == 0


def reconcile(
    held: dict[str, Decimal], typed: dict[str, Decimal], *, eur_rounded_legs: int = 0
) -> list[AssetCheck]:
    """Each typed asset against the account's position: agreeing (`agrees`) -> reconciled,
    else the difference (typed - held) is named, never bridged. The difference is kept on a
    reconciled EUR too, so the page can say it lies within the rounding."""
    checks = []
    for asset, units in typed.items():
        difference = units - held.get(asset, Decimal(0))
        checks.append(
            AssetCheck(
                asset=asset,
                held=held.get(asset, Decimal(0)),
                typed=units,
                reconciled=agrees(asset, difference, eur_rounded_legs),
                difference_units=difference,
            )
        )
    return checks
