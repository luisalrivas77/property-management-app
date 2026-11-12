"""A credit card statement's interest and fee lines (docs/phases/phase-6.md D176 (3); fixture
tests/fixtures/phase6/card_pdf.yaml, case `interest`) and its payment line (D176 (4)).

The issuer's own charges („Zinsen für einen genutzten Kredit“, a fee) have no payee a learned
rule could key on, so code books them to hh_interest_fees at confidence 1 (method
matcher_card), on a credit card account only (the service's guard). A charge is a debit
without a merchant whose kind word, or else its text, names interest or a fee; a purchase
always carries its merchant (services/bank/card_pdf.py), so a shop with „Gebühr“ in its name
is never one.

The payment received („Bezahlung“, a credit without a merchant) is the card's leg of its
monthly settlement: booked by code as internal_transfer_in, so services.transfers links it to
the paying account's debit (D162). The kind word alone decides: a refund („Gutschrift“)
carries its merchant and stays a purchase category.
"""

from decimal import Decimal

from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
    MatchTransaction,
)

_CATEGORY = AllocationCategory.HH_INTEREST_FEES
_INTEREST = "zins"
_FEE = "gebühr"
_PAYMENT = "bezahlung"
REASONING = "Zinsen oder Gebühren der Kreditkarte, aus der Abrechnung gelesen."
PAYMENT_REASONING = (
    "Zahlung auf die Kreditkarte laut Abrechnung: eine Überweisung zwischen zwei eigenen Konten."
)


def is_interest_or_fee(tx: MatchTransaction) -> bool:
    if tx.amount >= 0 or tx.counterparty_name is not None:
        return False
    kind = (tx.booking_text or "").casefold()
    text = tx.purpose.casefold()
    return any(
        word.startswith(_INTEREST) or _FEE in word for word in (*kind.split(), *text.split())
    )


def interest_fee_set(tx: MatchTransaction) -> AllocationSet | None:
    """The one-part set booking the line to hh_interest_fees, or None for any other line."""
    if not is_interest_or_fee(tx):
        return None
    return AllocationSet(
        transaction_id=tx.id,
        allocations=[AllocationDraft(amount=tx.amount, category=_CATEGORY)],
        method=AllocationMethod.MATCHER_CARD,
        confidence=Decimal(1),
        reasoning=REASONING,
    )


def is_payment(tx: MatchTransaction) -> bool:
    """The statement's payment received: a credit without a merchant, kind word Bezahlung."""
    return (
        tx.amount > 0
        and tx.counterparty_name is None
        and (tx.booking_text or "").strip().casefold() == _PAYMENT
    )


def payment_set(tx: MatchTransaction) -> AllocationSet | None:
    """D176 (4): the one-part set booking the payment as internal_transfer_in, or None."""
    if not is_payment(tx):
        return None
    return AllocationSet(
        transaction_id=tx.id,
        allocations=[
            AllocationDraft(amount=tx.amount, category=AllocationCategory.INTERNAL_TRANSFER_IN)
        ],
        method=AllocationMethod.MATCHER_CARD,
        confidence=Decimal(1),
        reasoning=PAYMENT_REASONING,
    )
