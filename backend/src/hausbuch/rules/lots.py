"""Lots and the holding period (docs/phases/phase-7.md D184 (4), D187, D188, §8); built in
step 6 (the pure rules; the service, the routes and the statement follow).

Pure, on read, no table and no cache (phase 3 D59's precedent: a `tax_lot` table would be a
cache the first superseded movement makes wrong, D184 (5)):

- `book(movements, links, prices, on)`: FIFO per account and asset over the confirmed,
  unsuperseded movements whose Berlin day is on or before `on` (a linked pair: whose earlier
  leg's is), and the unsuperseded links between two confirmed movements. Every lot ever
  created (closed ones too), the lots each movement took (`uses`), and the units a movement
  took that no lot covered (`unfilled`).
- `lots(movements, links, prices, on)`: the lots of `book` still open at the end of `on`.
- `consume(open_lots, units)`: which lots a disposal, an outgoing transfer or a fee takes, the
  oldest `acquired_on` first, then the oldest `acquired_at`, from one account and asset; it
  never takes more than is open (the caller sees the shortfall as `units − Σ use.units`).
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
else, in a swap group, the given legs' value (D188 (4)); plus its `fee_eur` and the group's
`fee` movements in another coin valued on the day when the group creates exactly one lot; a
fee in the coin acquired and withheld from the units received, as in an exchange trade,
adds nothing, its value being inside the EUR paid, while chain gas in that coin is paid on
top and joins the cost, D188 amended 16 Nov 2026); a
`staking_reward`, `cashback_reward` or `airdrop` (its EUR leg, else the day's price × units);
a linked transfer in (the moved lot keeps its day, cost per unit and source, D184 (4)). What
consumes: a `disposal`, a `fee` (at cost), a linked or unlinked transfer out. Nothing:
`lp_deposit`, `lp_withdrawal` (phase 8 decides, D188 (1)), `spam` (never a lot, D187),
`distribution`, `capital_income_tax`, `uncategorized`. Readings this step had to make (the
spec leaves them open; listed in the step 6 report):

- a price counts for a cost only when it is the price OF the movement's Berlin day („no price
  for its day“, D188 (4)); an older row `price_on` would return leaves the cost unpriced;
- a `balance_correction` (phase 6 Q-O96) adds units as a lot of unknown cost (source
  `unknown`, cost None, acquired on the correction's day) and takes units like a disposal.
  PROVISIONAL until phase 8 decides Q-O96: that lot's day starts a holding period the coins
  may not have, and its missing cost withholds every total it is in;
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
- a pair is processed whole at the earlier of its two legs, so a credit seen before its
  debit (D162 amended) does not first appear as an unknown lot; links are resolved over every
  confirmed movement whatever `on` is, and a pair whose earlier leg's Berlin day is on or
  before `on` is read whole, its later leg too (a transfer across 31 Dec: the lot is on the
  receiver at the year's end, never on both accounts and never an unknown lot); an unlinked
  movement is read only up to `on`;
- a link that competes with another for one leg: the earliest created stands (then the id);
  the matcher refuses a second link, so this only keeps a bad row from depending on order;
- at equal instants: what adds units first, then the linked moves (one whose receiving
  account and asset is another's sending side before that other: a chain of moves within one
  instant runs in its order; else by the sender's `index`), then what takes units (a
  disposal, a transfer out or a correction before a fee, then the movement's `index`, an
  all-digit one by its number, then its id), so the result depends on the random ids only
  when two movements at one instant share kind class and index (e.g. a hand entry without
  an index).

Every amount is exact until output: a lot keeps its exact cost per unit (the total over its
units), moved parts carry it unchanged, and a cost is quantized to the cent half up only when
a `Lot` or a `LotUse` is written (`cost_per_unit` to 8 places, numeric(20,8)). The arithmetic
runs with 60 significant digits (`_exact`), not the default 28, so units of 18 decimals on a
large balance subtract exactly once the quantity type is widened (migration 0042).

Fixtures tests/fixtures/phase7/lots.yaml, holding_period.yaml.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from functools import wraps
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import Field

from hausbuch.schemas.chain import Lot, LotSource, LotUse, PriceNeed
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.household import (
    AssetMovement,
    MovementKind,
    MovementSource,
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
# Significant digits for the unit arithmetic: numeric(38,18) units need 38, a cost per unit
# over them more; Python's default of 28 would round a large balance's last decimals.
PRECISION = 60


def _exact[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Run `fn` with PRECISION significant digits (nested calls keep it)."""

    @wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext() as ctx:
            ctx.prec = max(ctx.prec, PRECISION)
            return fn(*args, **kwargs)

    return run


# For the modules that sum or print the lots' units (rules.holdings, the statement, its files).
exact = _exact


# The lot with its id, creating movement and instant: folded into `schemas/chain.Lot` in
# step 6; the name stays for the callers of step 0's rules. PriceNeed lives there too.
HeldLot = Lot


class LotBook(Schema):
    """`book`'s answer. `lots`: every lot, closed ones too, per account and asset in FIFO
    order. `uses`: per movement id, the lots it took (a disposal, a fee, a transfer out to
    another account). `unfilled`: per movement id, the units it took that no lot covered (an
    incomplete history); never priced, never 0 €. `price_needs`: per creating movement id
    (a lot's `movement_id`, kept through every move), the prices whose absence leaves its
    lot unpriced; a lot of source `unknown` has none (no price can give it a cost)."""

    lots: list[HeldLot] = Field(default_factory=list)
    uses: dict[UUID, list[LotUse]] = Field(default_factory=dict)
    unfilled: dict[UUID, NonNegativeQuantity] = Field(default_factory=dict)
    price_needs: dict[UUID, list[PriceNeed]] = Field(default_factory=dict)


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


@_exact
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


@_exact
def consume(open_lots: Sequence[HeldLot], units: Decimal) -> list[LotUse]:
    """Which lots `units` takes, FIFO: the oldest `acquired_on` first, then the oldest
    `acquired_at`. The lots are of one account and asset (FIFO is per account and asset); lots
    of several are refused. Each use's cost is the lot's cost per unit × the units taken, to
    the cent; None for an unpriced lot. The cost per unit is the lot's rounded open cost over
    its open units (a `HeldLot` carries no exact one), so a use may differ by a cent from the
    same use in `book`, which keeps the exact cost per unit. Takes at most what is open: the
    shortfall is `units` minus the uses' units. The lots are not changed."""
    if units < 0:
        raise ValueError("units taken are not negative")
    if len({(lot.account_id, lot.asset_id) for lot in open_lots}) > 1:
        raise ValueError("consume takes from the lots of one account and asset")
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

    @_exact
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
    def __init__(
        self, prices: Sequence[PricePoint], priced_as: Mapping[UUID, UUID] | None = None
    ) -> None:
        self._by_asset: dict[UUID, list[PricePoint]] = defaultdict(list)
        for point in prices:
            self._by_asset[point.asset_id].append(point)
        self._twin = dict(priced_as or {})

    def of_day(self, asset_id: UUID, day: date) -> Decimal | None:
        """The asset's price for exactly that day (D188 (4)), by phase 6's precedence; a chain
        asset's `priced_as` asset's rows are read with its own (D180 (3), §15.1 item 18: the
        asset's own row first on its day)."""
        twin = self._twin.get(asset_id)
        point = valuation.price_on(
            self._by_asset.get(asset_id, []),
            day,
            priced_as=self._by_asset.get(twin, []) if twin is not None else (),
        )
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

    def missing(self, movements: Iterable[AssetMovement], day: date) -> list[PriceNeed]:
        """The (asset, day) pairs among `movements` with no price of that day."""
        return [
            PriceNeed(asset_id=m.asset_id, on=day)
            for m in movements
            if self.of_day(m.asset_id, day) is None
        ]


def _acquisition_total(
    m: AssetMovement, group: Sequence[AssetMovement], prices: _Prices
) -> tuple[Decimal | None, list[PriceNeed]]:
    """D188 (1), (4) and D188 amended (16 Nov 2026): the EUR leg, else the day's price ×
    units, else (a swap that creates this one lot) the given legs' value; plus `fee_eur`, plus
    the group's fees paid in another coin valued on the day (only when the group creates this
    one lot). Both fee parts count; neither replaces the other.

    A fee paid in the coin this movement acquires and withheld from the units received (an
    exchange trade: Binance's default, „Buy 1 SOL“, „Fee −0.001 SOL“) adds nothing here and
    needs no price: its value is already inside the EUR paid (the trade's own euro price per
    unit, EUR paid ÷ units received), so the lot's cost is the EUR paid (plus any other fee).
    Gas a chain transaction pays in that coin is paid on top of the units received (the fee
    movement is read from a chain, D183 (2)), so its value on the day joins the cost like a
    fee in another coin. Either way the fee movement then takes its units FIFO at cost, like
    every fee (D188 (1)). The test is the fee movement's own source, never whether the trade
    has a EUR leg (a coin-for-coin trade on an exchange has none and still withholds). A fee
    movement that carries its own EUR amount while the acquisition carries `fee_eur` is that
    same EUR fee (Binance's EUR fee leg, phase 6 D178 amended (3)) and is not counted twice.

    Any part unpriced → None, never 0 €, with the (asset, day) prices that would price it: a
    group that creates two lots cannot give each the whole given value."""
    day = berlin_day(m.occurred_at)
    units = abs(m.quantity)
    creating = [g for g in group if g.kind in _CREATES_IN_GROUP and g.quantity > 0]
    needs: list[PriceNeed] = []
    if m.eur_amount is not None:
        base: Decimal | None = abs(m.eur_amount)
    else:
        price = prices.of_day(m.asset_id, day)
        if price is not None:
            base = price * units
        else:
            # The received coin's own price for the day prices it (Rn. 58: received first).
            needs.append(PriceNeed(asset_id=m.asset_id, on=day))
            if len(creating) != 1:
                base = None
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
                needs += prices.missing(given, day)
    total = base
    if m.fee_eur is not None and total is not None:
        total += m.fee_eur
    if len(creating) == 1:
        coin_fees = [
            g
            for g in group
            if g.kind is MovementKind.FEE
            and (g.asset_id != m.asset_id or _paid_on_top(g))
            and not (m.fee_eur is not None and g.eur_amount is not None)  # the fee_eur itself
        ]
        if coin_fees:
            needs += prices.missing(coin_fees, day)
            fee = prices.value(coin_fees, day)
            total = None if fee is None or total is None else total + fee
    if total is not None:
        return total, []
    once: dict[tuple[UUID, date], PriceNeed] = {}
    for need in needs:  # chain gas in the coin acquired needs that coin's price too
        once.setdefault((need.asset_id, need.on), need)
    return None, list(once.values())


def _paid_on_top(fee: AssetMovement) -> bool:
    """A fee in the coin acquired is paid on top of the units received when a chain
    transaction paid it (gas, D183 (2)); an exchange withholds it from the units received."""
    return fee.source is MovementSource.CHAIN


def _reward_total(m: AssetMovement, prices: _Prices) -> tuple[Decimal | None, list[PriceNeed]]:
    if m.eur_amount is not None:
        return abs(m.eur_amount), []
    day = berlin_day(m.occurred_at)
    price = prices.of_day(m.asset_id, day)
    if price is None:
        return None, [PriceNeed(asset_id=m.asset_id, on=day)]
    return price * abs(m.quantity), []


def _per(total: Decimal | None, units: Decimal) -> Decimal | None:
    return None if total is None or not units else total / units


def _live_links(
    links: Sequence[TransferLink], movements: dict[UUID, AssetMovement]
) -> dict[UUID, TransferLink]:
    """Per movement id, the one unsuperseded link that joins it to another given movement,
    out-leg negative and in-leg positive. A bank or EUR link (a transaction leg) moves no lot.
    A leg in two links (the matcher refuses that): the earliest created stands, then the
    lowest id, whatever order the links come in."""
    out: dict[UUID, TransferLink] = {}
    for link in sorted(links, key=lambda k: (k.created_at, str(k.id))):
        if link.superseded_by is not None:
            continue
        a, b = link.out_movement_id, link.in_movement_id
        if a is None or b is None or a not in movements or b not in movements:
            continue
        if movements[a].quantity >= 0 or movements[b].quantity <= 0:
            continue
        if a in out or b in out:
            continue
        out[a] = out[b] = link
    return out


@_exact
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


def _confirmed(m: AssetMovement) -> bool:
    return m.status is MovementStatus.CONFIRMED and m.superseded_by is None


def _adds(m: AssetMovement) -> bool:
    return m.quantity > 0


# A step of the book: its sort key, the movement (a pair's sender) and a pair's link and
# receiver. Classes at one instant: 0 adds, 1 linked moves, 2 takes.
_Key = tuple[datetime, int, int, str, str]
_Step = tuple[_Key, AssetMovement, TransferLink | None, AssetMovement | None]


def _index(m: AssetMovement) -> str:
    """The movement's index in its transaction as a sort key: an all-digit index (a log index,
    a vout, an instruction) by its number, so 9 goes before 10; any other (`fee`, `net`,
    `internal:<trace>`) after the numbers, by its text; none first."""
    if m.index is None:
        return ""
    return m.index.zfill(64) if m.index.isdigit() else f"~{m.index}"


def _step(m: AssetMovement) -> _Step:
    if _adds(m):
        return ((m.occurred_at, 0, 0, "", str(m.id)), m, None, None)
    fee_last = 1 if m.kind is MovementKind.FEE else 0
    return ((m.occurred_at, 2, fee_last, _index(m), str(m.id)), m, None, None)


def _chain_order(moves: list[_Step]) -> list[_Step]:
    """Linked moves at one instant: a move whose receiving account and asset is another's
    sending side goes before it (A→B before B→C); otherwise, and in a cycle, by the sender's
    index, then its id."""

    def sends(s: _Step) -> tuple[UUID, UUID]:
        return (s[1].account_id, s[1].asset_id)

    def receives(s: _Step) -> tuple[UUID, UUID]:
        assert s[3] is not None
        return (s[3].account_id, s[3].asset_id)

    left = list(moves)
    out: list[_Step] = []
    while left:
        ready = next(
            (s for s in left if not any(o is not s and receives(o) == sends(s) for o in left)),
            left[0],
        )
        out.append(ready)
        left.remove(ready)
    return out


def _ordered(steps: list[_Step]) -> list[_Step]:
    steps = sorted(steps, key=lambda s: s[0])
    out: list[_Step] = []
    i = 0
    while i < len(steps):
        j = i + 1
        if steps[i][2] is not None:
            while j < len(steps) and steps[j][2] is not None and steps[j][0][0] == steps[i][0][0]:
                j += 1
            out.extend(_chain_order(steps[i:j]))
        else:
            out.append(steps[i])
        i = j
    return out


def _reading(
    movements: Sequence[AssetMovement], links: Sequence[TransferLink], on: date
) -> tuple[list[_Step], list[AssetMovement]]:
    """The steps of the book at the end of `on` and the movements they read: one step per
    unlinked confirmed movement of a Berlin day on or before `on`; a linked pair one step at
    its earlier leg, read whole (both legs) when that leg's Berlin day is on or before `on`,
    the later leg possibly after it."""
    confirmed = {m.id: m for m in movements if _confirmed(m)}
    linked = _live_links(links, confirmed)
    steps: list[_Step] = []
    read: list[AssetMovement] = []
    done: set[UUID] = set()
    for m in confirmed.values():
        link = linked.get(m.id)
        if link is None:
            if berlin_day(m.occurred_at) <= on:
                steps.append(_step(m))
                read.append(m)
        elif link.id not in done:
            done.add(link.id)
            sender = confirmed[link.out_movement_id]  # type: ignore[index]
            receiver = confirmed[link.in_movement_id]  # type: ignore[index]
            at = min(sender.occurred_at, receiver.occurred_at)
            if berlin_day(at) <= on:
                key = (at, 1, 0, _index(sender), str(sender.id))
                steps.append((key, sender, link, receiver))
                read += [sender, receiver]
    return steps, read


def read_through(
    movements: Sequence[AssetMovement], links: Sequence[TransferLink], on: date
) -> list[AssetMovement]:
    """The confirmed, unsuperseded movements the book reads at the end of the Berlin day `on`
    (an unlinked one of that day or before; a linked pair whole when its earlier leg's day is),
    so the units a statement shows and the lots it lists read a transfer across the day the
    same way (a pair across 31 Dec counts once, on the receiver). Order as given."""
    return _reading(movements, links, on)[1]


@_exact
def units_through(
    movements: Sequence[AssetMovement], links: Sequence[TransferLink], on: date
) -> dict[tuple[UUID, UUID], Decimal]:
    """Σ quantity per (account, asset) of `read_through`, with PRECISION digits; equal to the
    open lots' units wherever no movement took more than its account held."""
    out: dict[tuple[UUID, UUID], Decimal] = defaultdict(Decimal)
    for m in read_through(movements, links, on):
        out[(m.account_id, m.asset_id)] += m.quantity
    return dict(out)


@_exact
def book(
    movements: Sequence[AssetMovement],
    links: Sequence[TransferLink],
    prices: Sequence[PricePoint],
    on: date,
    *,
    priced_as: Mapping[UUID, UUID] | None = None,
) -> LotBook:
    """Every lot of the movements read at the end of the Berlin day `on`, and what took them.
    `movements` and `prices` may hold several accounts and assets; FIFO is per account and
    asset. The caller passes only assets that are lots (not EUR, whose fee legs the
    acquisitions' `fee_eur` already carry). `priced_as` maps a chain asset to the asset it is
    priced as (D180 (3)); `prices` then holds that asset's rows too."""
    steps, read = _reading(movements, links, on)
    priced = _Prices(prices, priced_as)
    needs: dict[UUID, list[PriceNeed]] = {}
    groups: dict[UUID, list[AssetMovement]] = defaultdict(list)
    for m in read:
        if m.group_id is not None:
            groups[m.group_id].append(m)

    lots = _Book()
    for _, m, link, receiver in _ordered(steps):
        units = abs(m.quantity)
        if link is not None and receiver is not None:
            _move(lots, link, m, receiver)
        elif m.kind is MovementKind.ACQUISITION:
            group = groups[m.group_id] if m.group_id is not None else [m]
            total, missing = _acquisition_total(m, group, priced)
            if missing:
                needs[m.id] = missing
            lots.add(_new(m, units, _per(total, units), LotSource.ACQUISITION))
        elif m.kind in _REWARDS:
            total, missing = _reward_total(m, priced)
            if missing:
                needs[m.id] = missing
            lots.add(_new(m, units, _per(total, units), LotSource.REWARD))
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
        price_needs=needs,
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
    *,
    priced_as: Mapping[UUID, UUID] | None = None,
) -> list[HeldLot]:
    """The lots open at the end of the Berlin day `on` (D188 (1)), per account and asset in
    FIFO order, each with its `tax_free_from`."""
    got = book(movements, links, prices, on, priced_as=priced_as)
    return [lot for lot in got.lots if lot.units_open > 0]
