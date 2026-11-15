"""A returned payment paired with its original (docs/phases/phase-6.md, D162 amended (6),
15 Nov 2026; Luis: „What is the 3200€ payment still there?“).

A private account sends money, the payment is recalled or bounced, and the bank credits it
back on the same account: „RUECKUEBERWEISUNG Rueckgabe aufgrund eines Recalls …“. The two
lines together are nothing, but the credit names an own IBAN, so the transfer matcher books it
as a transfer whose counterpart no other account holds, and the debit waits with a wrong
category. Code, never the model, recognises such a pair; a person books it
(services.returns.book_return).

A credit is a return of an earlier debit when both hold:

1. its purpose reads as a return: one of RETURN_WORDS, ignoring case, umlauts (ü = ue) and
   spacing (reads_as_return);
2. a debit of the same absolute amount was booked on the same account at most
   RETURN_WINDOW_DAYS days before it (the same day included).

Among several such debits the choice narrows to those whose purpose the credit's purpose
contains (at least MIN_CONTAINED characters, so an empty or one-word purpose never decides),
else to those paid to the credit's counterparty (IBAN, else name); when neither narrows it,
every equal debit is offered. Never one is guessed: the person picks (return_candidates).

Pure: the service reads the lines, this module decides."""

import re
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

# The words a bank writes on a returned payment, normalised (lower case, ü = ue, ä = ae,
# ö = oe, ß = ss): a returned transfer („Rücküberweisung“), its reason („Rückgabe“, a recall
# „Rückruf“ / „Recall“), a merchant's return („Retoure“) and a returned direct debit
# („Rücklastschrift“).
RETURN_WORDS = (
    "rueckueberweisung",
    "rueckgabe",
    "rueckruf",
    "recall",
    "retoure",
    "ruecklastschrift",
)
RETURN_WINDOW_DAYS = 30
# A debit's purpose decides among equal debits only when it says something: a purpose of
# fewer characters (after normalising) is contained in almost any text.
MIN_CONTAINED = 8

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_SPACE = re.compile(r"\s+")


class ReturnLine(NamedTuple):
    """One bank line as the rule reads it."""

    id: UUID
    account_id: UUID
    booked_on: date
    amount: Decimal
    purpose: str
    counterparty_iban_hmac: str | None = None
    counterparty_name: str | None = None


def normalise(text: str | None) -> str:
    """Lower case, umlauts spelled out, every run of spacing one blank, trimmed."""
    return _SPACE.sub(" ", (text or "").lower().translate(_UMLAUTS)).strip()


def reads_as_return(purpose: str | None) -> bool:
    """Whether a purpose says the payment came back (RETURN_WORDS). Spacing is dropped too,
    since a bank may wrap a word across its purpose lines („RUECKUEBER WEISUNG“)."""
    text = normalise(purpose).replace(" ", "")
    return any(word in text for word in RETURN_WORDS)


def _contained(debit: ReturnLine, credit: ReturnLine) -> bool:
    inner = normalise(debit.purpose)
    return len(inner) >= MIN_CONTAINED and inner in normalise(credit.purpose)


def _same_payee(debit: ReturnLine, credit: ReturnLine) -> bool:
    if debit.counterparty_iban_hmac is not None and credit.counterparty_iban_hmac is not None:
        return debit.counterparty_iban_hmac == credit.counterparty_iban_hmac
    name = normalise(debit.counterparty_name)
    return name != "" and name == normalise(credit.counterparty_name)


def in_window(debit: ReturnLine, credit: ReturnLine) -> bool:
    """Whether `debit` is an earlier debit of `credit`'s amount on its account, at most
    RETURN_WINDOW_DAYS days before it."""
    return (
        debit.id != credit.id
        and debit.account_id == credit.account_id
        and debit.amount < 0
        and debit.amount == -credit.amount
        and timedelta(0) <= credit.booked_on - debit.booked_on <= timedelta(RETURN_WINDOW_DAYS)
    )


def return_candidates(credit: ReturnLine, debits: Sequence[ReturnLine]) -> list[UUID]:
    """The debits `credit` may return, in booking order; empty when it is no return (a debit,
    no return word, or no equal debit inside the window)."""
    if credit.amount <= 0 or not reads_as_return(credit.purpose):
        return []
    equal = sorted(
        (d for d in debits if in_window(d, credit)), key=lambda d: (d.booked_on, str(d.id))
    )
    if len(equal) > 1:
        narrowed = [d for d in equal if _contained(d, credit)] or [
            d for d in equal if _same_payee(d, credit)
        ]
        equal = narrowed or equal
    return [d.id for d in equal]


def window_start(credit_booked_on: date) -> date:
    """The earliest booking day of a debit the credit may return."""
    return credit_booked_on - timedelta(RETURN_WINDOW_DAYS)


__all__ = [
    "MIN_CONTAINED",
    "RETURN_WINDOW_DAYS",
    "RETURN_WORDS",
    "ReturnLine",
    "in_window",
    "normalise",
    "reads_as_return",
    "return_candidates",
    "window_start",
]
