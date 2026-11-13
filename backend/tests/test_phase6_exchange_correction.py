"""D178 amended (13 Nov 2026, migration 0030): a balance_correction in the pure rules. It
counts in its coin's position like any movement, never adds a EUR leg, is never a trade and
never a withdrawal to complete, and with it the typed balance agrees. Invented coins and
amounts only."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hausbuch.rules import exchange
from hausbuch.schemas.household import MovementKind, WithdrawalRecord

T = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Moved:
    asset: str
    kind: MovementKind
    quantity: Decimal
    eur_amount: Decimal | None = None
    occurred_at: datetime = T


EXPORT = [
    _Moved("EUR", MovementKind.INTERNAL_TRANSFER_IN, Decimal("100.00")),
    _Moved("TST", MovementKind.ACQUISITION, Decimal("2.5"), Decimal("-80.00")),
    _Moved("TST", MovementKind.INTERNAL_TRANSFER_OUT, Decimal("-1.0")),
]
CORRECTION = _Moved("TST", MovementKind.BALANCE_CORRECTION, Decimal("0.25"))


def test_a_correction_counts_in_its_coin_and_adds_no_eur():
    before = exchange.positions(EXPORT)
    after = exchange.positions([*EXPORT, CORRECTION])
    assert before == {"EUR": Decimal("20.00"), "TST": Decimal("1.5")}
    assert after == {"EUR": Decimal("20.00"), "TST": Decimal("1.75")}


def test_a_correction_with_a_eur_amount_still_adds_no_eur_leg():
    """Never a trade: even a (refused by the database) EUR amount is not a EUR leg."""
    odd = _Moved("TST", MovementKind.BALANCE_CORRECTION, Decimal("0.25"), Decimal("-5.00"))
    assert exchange.positions([*EXPORT, odd])["EUR"] == Decimal("20.00")
    assert exchange.rounded_eur_legs([*EXPORT, odd], exchange.BINANCE) == 1


def test_an_outflow_correction_lowers_the_position():
    out = _Moved("TST", MovementKind.BALANCE_CORRECTION, Decimal("-0.5"))
    assert exchange.positions([*EXPORT, out])["TST"] == Decimal("1.0")


def test_the_typed_balance_agrees_once_the_difference_is_booked():
    typed = {"EUR": Decimal("20.00"), "TST": Decimal("1.75")}
    [eur, coin] = exchange.reconcile(exchange.positions(EXPORT), typed)
    assert (eur.reconciled, coin.reconciled, coin.difference_units) == (
        True,
        False,
        Decimal("0.25"),
    )
    corrected = exchange.positions(
        [*EXPORT, _Moved("TST", MovementKind.BALANCE_CORRECTION, coin.difference_units)]
    )
    assert all(check.reconciled for check in exchange.reconcile(corrected, typed))


def test_a_negative_correction_is_never_a_withdrawal_to_complete():
    out = _Moved("TST", MovementKind.BALANCE_CORRECTION, Decimal("-0.5"))
    history = [
        WithdrawalRecord(
            occurred_at=T,
            coin="TST",
            network="TESTNET",
            amount=Decimal("0.5"),
            fee=Decimal("0"),
            tx_hash="ab" * 32,
            status=exchange.COMPLETED,
        )
    ]
    assert exchange.match_withdrawals([out], history) == {}
