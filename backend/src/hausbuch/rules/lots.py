"""Lots and the holding period (docs/phases/phase-7.md D184 (4), D187, D188, §8); built in
step 6 (the pure rules; the service, the routes and the statement follow).

Pure, on read, no table and no cache (phase 3 D59's precedent: a `tax_lot` table would be a
cache the first superseded movement makes wrong, D184 (5)):

- `book(movements, links, prices, on)`: FIFO per account and asset over the confirmed,
  unsuperseded movements whose Berlin day is on or before `on`, and the unsuperseded links
  between two of them. Every lot ever created (closed ones too), the lots each movement took
  (`uses`), and the units a movement took that no lot covered (`unfilled`).
- `lots(movements, links, prices, on)`: the lots of `book` still open at the end of `on`.
- `consume(open_lots, units)`: which lots a disposal, an outgoing transfer or a fee takes, the
  oldest `acquired_on` first, then the oldest `acquired_at`; it never takes more than is open
  (the caller sees the shortfall as `units − Σ use.units`).
- `cost_total(lots)`: the cost of some lots, withheld (None) when any is unpriced, with the
  priced part and the count of unpriced lots for „ohne n Posten ohne Kurs“ (D188 (4)).
- `tax_free_from(acquired_on)`: §23 Abs. 1 S. 1 Nr. 2 EStG with §108 Abs. 1 AO and §§ 187
  Abs. 1, 188 Abs. 2 and 3 BGB: the day after the anniversary (after the last day of February
  when the anniversary of 29 Feb does not exist). §108 Abs. 3 AO does not reach the holding
  period (a Zeitraum, not a Frist for an act; Q-O92 settled by the orchestrator 15 Nov 2026),
  so the day never moves for a weekend or a public holiday.
- `within_holding_period(acquired_on, day)`: a disposal on `day` is inside the one year (a
  sale ON the anniversary still is).
- `berlin_day(occurred_at)`: the Europe/Berlin calendar day of an aware timestamp (D188 (2)).

What creates a lot (D188 (1)): an `acquisition` (its EUR leg, else the day's price × units,
else, in a swap group, the given legs' value (D188 (4)); plus its `fee_eur`, else the group's
`fee` movements valued on the day when the group creates exactly one lot); a
`staking_reward`, `cashback_reward` or `airdrop` (its EUR leg, else the day's price × units);
a linked transfer in (the moved lot keeps its day, cost per unit and source, D184 (4)). What
consumes: a `disposal`, a `fee` (at cost), a linked or unlinked transfer out. Nothing:
`lp_deposit`, `lp_withdrawal` (phase 8 decides, D188 (1)), `spam` (never a lot, D187),
`distribution`, `capital_income_tax`, `uncategorized`. Readings this step had to make (the
spec leaves them open; listed in the step 6 report):

- a price counts for a cost only when it is the price OF the movement's Berlin day („no price
  for its day“, D188 (4)); an older row `price_on` would return leaves the cost unpriced;
- a `balance_correction` (phase 6 Q-O96) adds units as a lot of unknown cost (source
  `unknown`, cost None) and takes units like a disposal;
- an unlinked transfer in is a lot of source `unknown` whose cost is its EUR leg or None; an
  unlinked transfer out takes its units like a disposal (they left the account);
- a linked transfer moves min(out units, in units), the oldest first; what the out-leg took
  beyond that (the network fee inside an exchange withdrawal's quantity) is a use that moves
  nowhere; what the in-leg received beyond it is a lot of source `unknown`, cost None;
- a link moves lots from the out-leg's (account, asset) to the in-leg's: between two
  accounts it records the moved parts as uses of the out-leg (lots.yaml's transfer_out);
  within one account to another asset (a wrap, a bridge) it records none, since nothing left
  the account (lots.yaml's wrap and bridge cases); within one account and asset it moves
  nothing (D184 (1)(c));
- a pair is processed at the earlier of its two legs, so a credit seen before its debit
  (D162 amended) does not first appear as an unknown lot;
- at equal instants a movement that adds units goes before one that takes units.

Every amount is exact until output: a lot keeps its exact cost per unit (the total over its
units), moved parts carry it unchanged, and a cost is quantized to the cent half up only when
a `Lot` or a `LotUse` is written (`cost_per_unit` to 8 places, numeric(20,8)).

Fixtures tests/fixtures/phase7/lots.yaml, holding_period.yaml.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import Field

from hausbuch.schemas.chain import Lot, LotSource, LotUse
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.household import (
    AssetMovement,
    MovementKind,
    MovementStatus,
    NonNegativeQuantity,
    PricePoint,
    TransferLink,
)

from . import valuation

BERLIN = ZoneInfo("Europe/Berlin")
CENT = Decimal("0.01")
UNIT_COST = Decimal("0.00000001")  # Lot.cost_per_unit numeric(20,8)
# The ids of lots that are not a movement's own (a moved part, an in-leg's excess) are
# derived from their origin, so a re-read gives the same id (nothing is stored).
_LOT_NS = UUID("6c0f6a43-5d7e-4f53-9a51-0a1c7d3e8b27")

_REWARDS = frozenset(
    {MovementKind.STAKING_REWARD, MovementKind.CASHBACK_REWARD, MovementKind.AIRDROP}
)
_TAKES = frozenset({MovementKind.DISPOSAL, MovementKind.FEE})
_NOTHING = frozenset(
    {
        MovementKind.LP_DEPOSIT,
        MovementKind.LP_WITHDRAWAL,
        MovementKind.SPAM,
        MovementKind.DISTRIBUTION,
        MovementKind.CAPITAL_INCOME_TAX,
        MovementKind.UNCATEGORIZED,
    }
)
_CREATES_IN_GROUP = frozenset({MovementKind.ACQUISITION}) | _REWARDS


class HeldLot(Lot):
    """A `Lot` with what `consume` and `LotUse.lot_id` need and the spec's `Lot` lacks: its
    id, the movement that created it (kept through every move) and the instant it was
    acquired (FIFO's second key). To be folded into `schemas/chain.Lot` (step 6 report)."""

    id: UUID
    movement_id: UUID
    acquired_at: datetime


class LotBook(Schema):
    """`book`'s answer. `lots`: every lot, closed ones too, per account and asset in FIFO
    order. `uses`: per movement id, the lots it took (a disposal, a fee, a transfer out to
    another account). `unfilled`: per movement id, the units it took that no lot covered (an
    incomplete history); never priced, never 0 €."""

    lots: list[HeldLot] = Field(default_factory=list)
    uses: dict[UUID, list[LotUse]] = Field(default_factory=dict)
    unfilled: dict[UUID, NonNegativeQuantity] = Field(default_factory=dict)


class CostTotal(Schema):
    """`cost_total`'s answer: `eur` is the total, None when any lot is unpriced (D188 (4):
    no total that contains an unpriced lot is shown as a number); `priced_eur` the sum of the
    priced lots; `unpriced` how many lots have no cost."""

    eur: Money | None
    priced_eur: Money
    unpriced: int = Field(ge=0)


# --- The day and the holding period ------------------------------------------------------


def berlin_day(occurred_at: datetime) -> date:
    """The Europe/Berlin calendar day of an aware timestamp (D188 (2)): 23:30 UTC on 31 Dec is
    1 Jan in Berlin. A naive timestamp is refused, never read as local time."""
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("a movement's time is aware (chains and exchanges stamp UTC)")
    return occurred_at.astimezone(BERLIN).date()


def tax_free_from(acquired_on: date) -> date:
    """D188 (3): the acquisition day is not counted (§187 Abs. 1 BGB), the year ends with the
    day of the next year bearing its number (§188 Abs. 2 BGB), or with the last day of
    February for 29 Feb (§188 Abs. 3 BGB); the lot is tax-free from the day after. A calendar
    year, never 365 or 366 days, and no shift for a weekend or holiday (Q-O92)."""
    try:
        end = acquired_on.replace(year=acquired_on.year + 1)
    except ValueError:  # 29 Feb, and the next year has none
        end = date(acquired_on.year + 1, 3, 1) - timedelta(days=1)
    return end + timedelta(days=1)


def within_holding_period(acquired_on: date, day: date) -> bool:
    """Whether a disposal on `day` falls inside the one year of §23 Abs. 1 S. 1 Nr. 2 EStG
    („nicht mehr als ein Jahr“): a sale on the anniversary still does."""
    return day < tax_free_from(acquired_on)


# --- FIFO --------------------------------------------------------------------------------


@dataclass
class _Part:
    """A lot while the book is read: its exact cost per unit (None: unpriced) never rounded."""

    id: UUID
    movement_id: UUID
    account_id: UUID
    asset_id: UUID
    acquired_at: datetime
    acquired_on: date
    units: Decimal
    open: Decimal
    per: Decimal | None
    source: LotSource
    moved_through: tuple[UUID, ...] = ()

    def key(self) -> tuple[date, datetime, str]:
        return (self.acquired_on, self.acquired_at, str(self.id))


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def _cost(per: Decimal | None, units: Decimal) -> Decimal | None:
    return None if per is None else _cents(per * units)


def _fifo(parts: Iterable[_Part], units: Decimal) -> list[tuple[_Part, Decimal]]:
    """The parts `units` takes, oldest first, and how much of each; mutates nothing."""
    out: list[tuple[_Part, Decimal]] = []
    left = units
    for part in sorted((p for p in parts if p.open > 0), key=_Part.key):
        if left <= 0:
            break
        take = min(left, part.open)
        out.append((part, take))
        left -= take
    return out


def consume(open_lots: Sequence[HeldLot], units: Decimal) -> list[LotUse]:
    """Which lots `units` takes, FIFO: the oldest `acquired_on` first, then the oldest
    `acquired_at`. Each use's cost is the lot's cost per unit × the units taken, to the cent;
    None for an unpriced lot. Takes at most what is open: the shortfall is `units` minus the
    uses' units. The lots are not changed."""
    if units < 0:
        raise ValueError("units taken are not negative")
    parts = [
        _Part(
            id=lot.id,
            movement_id=lot.movement_id,
            account_id=lot.account_id,
            asset_id=lot.asset_id,
            acquired_at=lot.acquired_at,
            acquired_on=lot.acquired_on,
            units=lot.units,
            open=lot.units_open,
            per=None
            if lot.cost_eur is None or not lot.units_open
            else lot.cost_eur / lot.units_open,
            source=lot.source,
            moved_through=tuple(lot.moved_through),
        )
        for lot in open_lots
    ]
    return [
        LotUse(lot_id=part.id, units=take, cost_eur=_cost(part.per, take))
        for part, take in _fifo(parts, units)
    ]


def cost_total(lots: Iterable[Lot]) -> CostTotal:
    priced = Decimal("0.00")
    unpriced = 0
    for lot in lots:
        if lot.cost_eur is None:
            unpriced += 1
        else:
            priced += lot.cost_eur
    return CostTotal(eur=None if unpriced else priced, priced_eur=priced, unpriced=unpriced)


@dataclass
class _Book:
    parts: dict[tuple[UUID, UUID], list[_Part]] = field(default_factory=lambda: defaultdict(list))
    uses: dict[UUID, list[LotUse]] = field(default_factory=lambda: defaultdict(list))
    unfilled: dict[UUID, Decimal] = field(default_factory=dict)

    def add(self, part: _Part) -> None:
        self.parts[(part.account_id, part.asset_id)].append(part)

    def take(
        self, movement: AssetMovement, units: Decimal, *, record: bool = True
    ) -> list[tuple[_Part, Decimal]]:
        """Take `units` FIFO from the movement's account and asset; record the uses and any
        shortfall under the movement. Returns what was taken."""
        taken = _fifo(self.parts[(movement.account_id, movement.asset_id)], units)
        for part, n in taken:
            part.open -= n
            if record:
                self.uses[movement.id].append(
                    LotUse(lot_id=part.id, units=n, cost_eur=_cost(part.per, n))
                )
        missing = units - sum((n for _, n in taken), Decimal(0))
        if missing > 0:
            self.unfilled[movement.id] = self.unfilled.get(movement.id, Decimal(0)) + missing
        return taken


def _new(movement: AssetMovement, units: Decimal, per: Decimal | None, source: LotSource) -> _Part:
    return _Part(
        id=movement.id,
        movement_id=movement.id,
        account_id=movement.account_id,
        asset_id=movement.asset_id,
        acquired_at=movement.occurred_at,
        acquired_on=berlin_day(movement.occurred_at),
        units=units,
        open=units,
        per=per,
        source=source,
    )


class _Prices:
    def __init__(self, prices: Sequence[PricePoint]) -> None:
        self._by_asset: dict[UUID, list[PricePoint]] = defaultdict(list)
        for point in prices:
            self._by_asset[point.asset_id].append(point)

    def of_day(self, asset_id: UUID, day: date) -> Decimal | None:
        """The asset's price for exactly that day (D188 (4)), by phase 6's precedence."""
        point = valuation.price_on(self._by_asset.get(asset_id, []), day)
        return point.price_eur if point is not None and point.on == day else None

    def value(self, movements: Iterable[AssetMovement], day: date) -> Decimal | None:
        """Σ |units| × the day's price; None when any is unpriced or there is none."""
        total = Decimal(0)
        seen = False
        for m in movements:
            price = self.of_day(m.asset_id, day)
            if price is None:
                return None
            total += abs(m.quantity) * price
            seen = True
        return total if seen else None


def _acquisition_total(
    m: AssetMovement, group: Sequence[AssetMovement], prices: _Prices
) -> Decimal | None:
    """D188 (1), (4): the EUR leg, else the day's price × units, else (a swap) the given legs'
    value; plus `fee_eur`, else the group's fee movements valued on the day (only when the
    group creates this one lot). Any part unpriced → None, never 0 €."""
    day = berlin_day(m.occurred_at)
    units = abs(m.quantity)
    if m.eur_amount is not None:
        base: Decimal | None = abs(m.eur_amount)
    else:
        price = prices.of_day(m.asset_id, day)
        if price is not None:
            base = price * units
        else:
            given = [
                g
                for g in group
                if g.id != m.id
                and g.kind is MovementKind.DISPOSAL
                and g.quantity < 0
                and g.asset_id != m.asset_id
            ]
            base = prices.value(given, day)
    if base is None:
        return None
    if m.fee_eur is not None:
        return base + m.fee_eur
    creating = [g for g in group if g.kind in _CREATES_IN_GROUP and g.quantity > 0]
    fees = [g for g in group if g.kind is MovementKind.FEE]
    if not fees or len(creating) != 1:
        return base
    fee = prices.value(fees, day)
    return None if fee is None else base + fee


def _reward_total(m: AssetMovement, prices: _Prices) -> Decimal | None:
    if m.eur_amount is not None:
        return abs(m.eur_amount)
    price = prices.of_day(m.asset_id, berlin_day(m.occurred_at))
    return None if price is None else price * abs(m.quantity)


def _per(total: Decimal | None, units: Decimal) -> Decimal | None:
    return None if total is None or not units else total / units


def _live_links(
    links: Sequence[TransferLink], movements: dict[UUID, AssetMovement]
) -> dict[UUID, TransferLink]:
    """Per movement id, the one unsuperseded link that joins it to another read movement,
    out-leg negative and in-leg positive. A bank or EUR link (a transaction leg) moves no lot."""
    out: dict[UUID, TransferLink] = {}
    for link in links:
        if link.superseded_by is not None:
            continue
        a, b = link.out_movement_id, link.in_movement_id
        if a is None or b is None or a not in movements or b not in movements:
            continue
        if movements[a].quantity >= 0 or movements[b].quantity <= 0:
            continue
        if a in out or b in out:
            continue  # a leg in two links: the first stands, the matcher refuses a second
        out[a] = out[b] = link
    return out


def _move(book: _Book, link: TransferLink, sender: AssetMovement, receiver: AssetMovement) -> None:
    sent, received = abs(sender.quantity), abs(receiver.quantity)
    same_account = sender.account_id == receiver.account_id
    if same_account and sender.asset_id == receiver.asset_id:
        return  # D184 (1)(c): address to address inside one account, the lot stays
    moving = min(sent, received)
    taken = book.take(sender, moving, record=not same_account)
    for part, n in taken:
        book.add(
            _Part(
                id=uuid5(_LOT_NS, f"{part.id}:{link.id}"),
                movement_id=part.movement_id,
                account_id=receiver.account_id,
                asset_id=receiver.asset_id,
                acquired_at=part.acquired_at,
                acquired_on=part.acquired_on,
                units=n,
                open=n,
                per=part.per,
                source=part.source,
                moved_through=(*part.moved_through, link.id),
            )
        )
    if sent > moving:  # the network fee inside the sender's quantity: it left, moved nowhere
        book.take(sender, sent - moving)
    excess = received - sum((n for _, n in taken), Decimal(0))
    if excess > 0:  # dust, or lots the sender's history does not hold: unknown cost
        part = _new(receiver, excess, None, LotSource.UNKNOWN)
        part.id = uuid5(_LOT_NS, f"{receiver.id}:excess")
        book.add(part)


def _read(m: AssetMovement, on: date) -> bool:
    return (
        m.status is MovementStatus.CONFIRMED
        and m.superseded_by is None
        and berlin_day(m.occurred_at) <= on
    )


def _adds(m: AssetMovement) -> bool:
    return m.quantity > 0


def book(
    movements: Sequence[AssetMovement],
    links: Sequence[TransferLink],
    prices: Sequence[PricePoint],
    on: date,
) -> LotBook:
    """Every lot of the movements read at the end of the Berlin day `on`, and what took them.
    `movements` and `prices` may hold several accounts and assets; FIFO is per account and
    asset. The caller passes only assets that are lots (not EUR)."""
    read = {m.id: m for m in movements if _read(m, on)}
    linked = _live_links(links, read)
    priced = _Prices(prices)
    groups: dict[UUID, list[AssetMovement]] = defaultdict(list)
    for m in read.values():
        if m.group_id is not None:
            groups[m.group_id].append(m)

    # One step per movement, a linked pair one step at its earlier leg.
    steps: list[tuple[datetime, int, str, AssetMovement, TransferLink | None]] = []
    done: set[UUID] = set()
    for m in read.values():
        link = linked.get(m.id)
        if link is None:
            steps.append((m.occurred_at, 0 if _adds(m) else 1, str(m.id), m, None))
        elif link.id not in done:
            done.add(link.id)
            sender = read[link.out_movement_id]  # type: ignore[index]
            receiver = read[link.in_movement_id]  # type: ignore[index]
            at = min(sender.occurred_at, receiver.occurred_at)
            steps.append((at, 1, str(sender.id), sender, link))
    steps.sort(key=lambda s: (s[0], s[1], s[2]))

    lots = _Book()
    for _, _, _, m, link in steps:
        units = abs(m.quantity)
        if link is not None:
            _move(lots, link, m, read[link.in_movement_id])  # type: ignore[index]
        elif m.kind is MovementKind.ACQUISITION:
            group = groups[m.group_id] if m.group_id is not None else [m]
            total = _acquisition_total(m, group, priced)
            lots.add(_new(m, units, _per(total, units), LotSource.ACQUISITION))
        elif m.kind in _REWARDS:
            lots.add(_new(m, units, _per(_reward_total(m, priced), units), LotSource.REWARD))
        elif m.kind in _TAKES or m.kind is MovementKind.INTERNAL_TRANSFER_OUT:
            lots.take(m, units)
        elif m.kind is MovementKind.INTERNAL_TRANSFER_IN:
            eur = None if m.eur_amount is None else abs(m.eur_amount)
            lots.add(_new(m, units, _per(eur, units), LotSource.UNKNOWN))
        elif m.kind is MovementKind.BALANCE_CORRECTION:
            if _adds(m):
                lots.add(_new(m, units, None, LotSource.UNKNOWN))
            else:
                lots.take(m, units)
        elif m.kind in _NOTHING:
            continue
        else:  # pragma: no cover - a new MovementKind must be given its branch here
            raise ValueError(f"no lot rule for {m.kind}")

    return LotBook(
        lots=[
            _out(part)
            for key in sorted(lots.parts, key=lambda k: (str(k[0]), str(k[1])))
            for part in sorted(lots.parts[key], key=_Part.key)
        ],
        uses=dict(lots.uses),
        unfilled=dict(lots.unfilled),
    )


def _out(part: _Part) -> HeldLot:
    return HeldLot(
        id=part.id,
        movement_id=part.movement_id,
        acquired_at=part.acquired_at,
        account_id=part.account_id,
        asset_id=part.asset_id,
        acquired_on=part.acquired_on,
        units=part.units,
        units_open=part.open,
        cost_eur=_cost(part.per, part.open),
        cost_per_unit=None if part.per is None else part.per.quantize(UNIT_COST, ROUND_HALF_UP),
        source=part.source,
        moved_through=list(part.moved_through),
        tax_free_from=tax_free_from(part.acquired_on),
    )


def lots(
    movements: Sequence[AssetMovement],
    links: Sequence[TransferLink],
    prices: Sequence[PricePoint],
    on: date,
) -> list[HeldLot]:
    """The lots open at the end of the Berlin day `on` (D188 (1)), per account and asset in
    FIFO order, each with its `tax_free_from`."""
    return [lot for lot in book(movements, links, prices, on).lots if lot.units_open > 0]
