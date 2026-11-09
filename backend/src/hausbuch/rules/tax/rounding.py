"""Every rounding the tax rules do, in one place (phase 3 D60, §5.11).

The draft computes in cents (Decimal, two places). The rules round a computed figure to the
cent half-up (the AfA basis and amount, a §82b share, a net estimate, a monthly figure), and a
share shown to the user to one decimal half-up. Whole euros exist only for the field sheet:
an income line is rounded down and a cost line up (the form's "EUR ,–" fields, in the
taxpayer's favour), totals are the sums of the rounded lines, and the owners' Zurechnung is
split by largest remainder with the odd unit to the first owner.

Direction for a negative line: "down" is toward minus infinity and "up" toward plus infinity
(floor and ceiling), so a negative income line (a refund to a tenant on Z21) becomes one euro
more negative, i.e. lower income, which is the same "in the taxpayer's favour" reading as for
a positive line. No fixture pins a negative line yet; D60 is provisional until the Anleitung
is read (open question 4), and a change belongs here only.
"""

from collections.abc import Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from hausbuch.schemas.common import Fraction
from hausbuch.schemas.tax import Rounding

CENT = Decimal("0.01")
TENTH = Decimal("0.1")
ONE = Decimal("1")


def cents(value: Decimal) -> Decimal:
    """Half-up to the cent (a negative zero becomes 0.00)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP) + 0


def one_decimal(value: Decimal) -> Decimal:
    """Half-up to one decimal (a percentage shown as 14.2)."""
    return value.quantize(TENTH, rounding=ROUND_HALF_UP)


def whole_half_up(value: Decimal) -> int:
    """Half-up to a whole number (a Kürzung percentage)."""
    return int(value.quantize(ONE, rounding=ROUND_HALF_UP))


def money_str(value: Decimal) -> str:
    """A cent amount as a warning param ("4760.00", never "4.76E+3")."""
    return str(cents(value))


def whole_euros(amount: Decimal, rounding: Rounding) -> int:
    """A line's whole-euro entry for the field sheet (D60): down = floor, up = ceiling,
    none = half-up (a line the form does not round, such as a percentage)."""
    mode = {"down": ROUND_FLOOR, "up": ROUND_CEILING, "none": ROUND_HALF_UP}[rounding]
    return int(amount.quantize(ONE, rounding=mode))


def split_largest_remainder(total: Decimal, shares: Sequence[Fraction], unit: Decimal) -> list:
    """Split `total` by `shares` in steps of `unit` (CENT or ONE) so the parts add up to
    `total` exactly. Each part is first truncated toward zero; the units left over go to the
    largest remainders, ties to the earlier owner (D60: the odd euro to the first owner). The
    sign is applied at the end, so a loss is split like a surplus."""
    if not shares:
        return []
    sign = -1 if total < 0 else 1
    magnitude = abs(total) / unit  # a whole number of units
    exact = [magnitude * s.numerator / s.denominator for s in shares]
    floors = [int(x) for x in exact]  # x >= 0, so int() truncates down
    left = int(magnitude) - sum(floors)
    order = sorted(range(len(shares)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[: max(left, 0)]:
        floors[i] += 1
    return [sign * Decimal(n) * unit for n in floors]


def split_cents(total: Decimal, shares: Sequence[Fraction]) -> list[Decimal]:
    return [cents(p) for p in split_largest_remainder(total, shares, CENT)]


def split_euros(total: int, shares: Sequence[Fraction]) -> list[int]:
    return [int(p) for p in split_largest_remainder(Decimal(total), shares, ONE)]
