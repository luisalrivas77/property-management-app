"""The exchanges' pure rules (docs/phases/phase-6.md D166 as amended 13 Nov 2026, D175,
D178, §8; fixtures tests/fixtures/phase6/bison_csv.yaml and binance_csv.yaml). Built in
phase 6 step 5a.

- `classify(line)`: a BISON row (MovementLine) to its movement kind; `review_needed` says
  whether code may confirm it.
- `bison_movements(lines)`: BISON's rows to movements, one per row, each with its dedup hash
  (the Transaction ID, else the row's content and its position among identical rows).
- `binance_movements(rows)`: Binance's transaction history to movements: the trade rows of
  one second summed per coin and role, a EUR leg folded into the coin leg's eur_amount; every
  other row one movement.
- `match_withdrawals(withdrawals, history)`: the withdrawal history's row that completes each
  withdrawal (same coin, Amount + Fee = the amount sent, within one hour before).
- `positions(movements)`, `reconcile(held, typed)`: the typed balance per asset (D166, D178
  (6)): Σ movements per asset, EUR plus Σ eur_amount of the acquisitions and disposals.

Nothing here reads a User ID or an address: the parsers drop them (D178 (2)).
"""

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
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
CENT = Decimal("0.01")
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


def bison_movements(lines: Sequence[MovementLine]) -> list[ExchangeMovement]:
    """One movement per row, in the file's order. A Transaction ID repeated in the file gives
    the same hash twice; the import skips the second (D166)."""
    keys = [_bison_key(line) for line in lines]
    positions = [
        0 if line.external_ref is not None else p
        for line, p in zip(lines, _positions_of(keys), strict=True)
    ]
    out = []
    for line, key, position in zip(lines, keys, positions, strict=True):
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
            ExchangeMovement(
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


def _other(row: BinanceRow) -> tuple[tuple[str, ...], dict]:
    """D178 (3): a row that is no trade leg, one movement each."""
    if row.operation == "Deposit":
        kind = (
            MovementKind.INTERNAL_TRANSFER_IN if row.coin == EUR else MovementKind.CASHBACK_REWARD
        )
    elif row.operation == "Withdraw":
        kind = MovementKind.INTERNAL_TRANSFER_OUT
    else:
        kind = MovementKind.UNCATEGORIZED
    key = (row.occurred_at.isoformat(), row.operation, row.coin, canonical(row.change))
    return key, {
        "asset": row.coin,
        "kind": kind,
        "quantity": row.change,
        "eur_amount": cents(row.change) if row.coin == EUR else None,
    }


def binance_movements(rows: Sequence[BinanceRow]) -> list[ExchangeMovement]:
    """D178 (3), (5): the transaction history to movements, oldest second first; within a
    second the trade's acquisition, disposal and fees (by coin), then the other rows in the
    order Binance wrote them. Raises exchange.binance_ambiguous_trade for a second whose
    trade rows do not give exactly one coin for one other."""
    by_second: dict[datetime, list[BinanceRow]] = {}
    for row in rows:
        by_second.setdefault(row.occurred_at, []).append(row)
    entries: list[tuple[tuple[str, ...], dict]] = []
    for at in sorted(by_second):
        trade = [r for r in by_second[at] if r.operation in BINANCE_TRADE_OPERATIONS]
        if trade:
            entries.extend(_trade(at, trade))
        entries.extend(
            _other(r) for r in by_second[at] if r.operation not in BINANCE_TRADE_OPERATIONS
        )
    out = []
    for (key, fields), position in zip(
        entries, _positions_of(key for key, _ in entries), strict=True
    ):
        at = datetime.fromisoformat(key[0])
        kind, asset = fields["kind"], fields["asset"]
        out.append(
            ExchangeMovement(
                occurred_at=at,
                status=_status(kind, asset),
                question=_question(kind),
                movement_hash=movement_hash("binance", key, position),
                position=position,
                **fields,
            )
        )
    return out


def match_withdrawals(
    withdrawals: Sequence[ExchangeMovement], history: Sequence[WithdrawalRecord]
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
    movements: Iterable[ExchangeMovement], held_before: dict[str, Decimal] | None = None
) -> dict[str, Decimal]:
    """Σ quantity per asset; EUR also takes every acquisition's and disposal's eur_amount (a
    trade's EUR leg is never its own movement)."""
    held = dict(held_before or {})
    for m in movements:
        held[m.asset] = held.get(m.asset, Decimal(0)) + m.quantity
        if m.kind in TRADE_KINDS and m.eur_amount is not None:
            held[EUR] = held.get(EUR, Decimal(0)) + m.eur_amount
    return held


def reconcile(held: dict[str, Decimal], typed: dict[str, Decimal]) -> list[AssetCheck]:
    """Each typed asset against the account's position: equal -> reconciled, else the
    difference (typed - held) is named, never bridged."""
    return [
        AssetCheck(
            asset=asset,
            held=held.get(asset, Decimal(0)),
            typed=units,
            reconciled=units == held.get(asset, Decimal(0)),
            difference_units=units - held.get(asset, Decimal(0)),
        )
        for asset, units in typed.items()
    ]
