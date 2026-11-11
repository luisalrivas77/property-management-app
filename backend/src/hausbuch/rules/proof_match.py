"""Whether a payment proof's amount pays a bill (docs/phases/phase-3.md D143, D149). Pure.

A bill is described by the amounts it counts with: its acquisition cost rows (the rows that
carry it as Beleg) and its private_cost rows in force. A proof's amount is compared as a
magnitude (a bank shows a transfer as negative), within 0.01:

- WHOLE: the bill's combined total (every row's amount: a bill split into a purchase part and
  a part paid from another account was paid as one), or, for such a split bill, its private
  part alone (the purchase part paid another way), as the draft accepts either (D143);
- PART: one of its acquisition cost rows alone (one buyer's share of a Grunderwerbsteuer that
  one document sets for both buyers, D145; or the purchase part of a split bill), or one of
  the bill's parts: the cost lines its reading states, when they sum to the combined total
  (one buyer's notice in a document holding both, read as one row, D150): the proof of the
  rest is still needed.

The pre-fill compares a proof with its bill (D143) and with the bills that still wait for a
proof (D149); the draft offers an unlinked proof on a bill's to-do by the same test (D149)."""

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

TOLERANCE = Decimal("0.01")


class Fit(StrEnum):
    WHOLE = "whole"
    PART = "part"


def bill_total(acquisition: Sequence[Decimal], private: Sequence[Decimal]) -> Decimal:
    """The bill's combined total: every row's amount."""
    return sum(acquisition, Decimal(0)) + sum(private, Decimal(0))


def bill_parts(
    lines: Sequence[Decimal], acquisition: Sequence[Decimal], private: Sequence[Decimal]
) -> list[Decimal]:
    """D150: the bill's parts: the amounts of the cost lines its reading states (`lines`),
    only when they sum to the bill's combined total within 0.01; otherwise none (a reading
    that disagrees with the rows says nothing about how the bill was paid). A credit line
    (negative) or a line of 0.00 is no part anyone pays."""
    if not lines or (not acquisition and not private):
        return []
    if not _close(sum(lines, Decimal(0)), bill_total(acquisition, private)):
        return []
    return [line for line in lines if line > 0]


def proof_fit(
    amount: Decimal,
    acquisition: Sequence[Decimal],
    private: Sequence[Decimal],
    *,
    parts: Sequence[Decimal] = (),
) -> Fit | None:
    """How a proof of `amount` fits the bill whose rows have these amounts and whose reading
    states these cost lines (`parts`, used only when they sum to the total, bill_parts);
    None when it fits none of them (or the bill has no amount at all)."""
    if not acquisition and not private:
        return None
    paid = abs(amount)
    whole = [bill_total(acquisition, private)]
    if acquisition and private:
        whole.append(sum(private, Decimal(0)))
    if any(_close(paid, total) for total in whole):
        return Fit.WHOLE
    if any(_close(paid, row) for row in acquisition):
        return Fit.PART
    if any(_close(paid, part) for part in bill_parts(parts, acquisition, private)):
        return Fit.PART
    return None


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE
