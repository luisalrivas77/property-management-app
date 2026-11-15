"""rules.household.returns (phase 6 D162 amended (7), 15 Nov 2026): a credit whose purpose
reads as a returned payment, paired with the equal earlier debit on its account. Invented
amounts, purposes and payees only."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from hausbuch.rules.household.returns import (
    RETURN_WINDOW_DAYS,
    RETURN_WORDS,
    ReturnLine,
    reads_as_return,
    return_candidates,
)

ACCOUNT = uuid.UUID(int=1)
OTHER_ACCOUNT = uuid.UUID(int=2)
RECALL = "RUECKUEBERWEISUNG Rueckgabe aufgrund eines Recalls (Rueckrufes) Umbuchung Ruecklage Juli"


def _line(
    day: date,
    amount: str,
    purpose: str = "",
    *,
    account=ACCOUNT,
    iban: str | None = None,
    name: str | None = None,
) -> ReturnLine:
    return ReturnLine(
        id=uuid.uuid4(),
        account_id=account,
        booked_on=day,
        amount=Decimal(amount),
        purpose=purpose,
        counterparty_iban_hmac=iban,
        counterparty_name=name,
    )


# --- The purpose -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "purpose",
    [
        RECALL,
        "Rücküberweisung Miete",
        "RÜCKÜBERWEISUNG",
        "rueckueber weisung wegen falscher IBAN",  # wrapped across two purpose lines
        "Rückgabe Lastschrift",
        "Rückruf der Überweisung",
        "Recall of payment",
        "RETOURE Bestellung 4711",
        "Rücklastschrift mangels Deckung",
        "RUECKLASTSCHRIFT",
    ],
)
def test_a_purpose_that_says_the_money_came_back_reads_as_a_return(purpose):
    assert reads_as_return(purpose)


@pytest.mark.parametrize(
    "purpose",
    ["", None, "Gehalt Oktober", "Umbuchung Rücklage", "Erstattung Stromabschlag", "Überweisung"],
)
def test_any_other_purpose_does_not(purpose):
    assert not reads_as_return(purpose)


def test_the_word_list_is_kept_normalised():
    """Each word is lower case with umlauts spelled out, as reads_as_return compares them."""
    assert all(w == w.lower() and not set(w) & set("äöüß ") for w in RETURN_WORDS)


# --- The debit it returns ----------------------------------------------------------------------


def test_the_recall_pairs_with_the_equal_debit_two_days_before():
    debit = _line(date(2026, 7, 28), "-3200.00", "Umbuchung Ruecklage Juli")
    credit = _line(date(2026, 7, 30), "3200.00", RECALL)
    other = _line(date(2026, 7, 29), "-3200.01", "Umbuchung Ruecklage Juli")
    assert return_candidates(credit, [debit, other]) == [debit.id]


def test_no_return_word_no_pair():
    debit = _line(date(2026, 7, 28), "-3200.00")
    credit = _line(date(2026, 7, 30), "3200.00", "Umbuchung Ruecklage Juli")
    assert return_candidates(credit, [debit]) == []


def test_a_debit_is_never_a_return():
    debit = _line(date(2026, 7, 28), "-3200.00")
    assert return_candidates(_line(date(2026, 7, 30), "-3200.00", RECALL), [debit]) == []


def test_the_window_is_thirty_days_before_and_the_same_day():
    credit = _line(date(2026, 7, 30), "3200.00", RECALL)
    edge = _line(date(2026, 6, 30), "-3200.00")  # 30 days before
    outside = _line(date(2026, 6, 29), "-3200.00")  # 31 days before
    same_day = _line(date(2026, 7, 30), "-3200.00")
    later = _line(date(2026, 7, 31), "-3200.00")
    assert RETURN_WINDOW_DAYS == 30
    assert return_candidates(credit, [outside]) == []
    assert return_candidates(credit, [later]) == []
    assert return_candidates(credit, [edge]) == [edge.id]
    assert return_candidates(credit, [same_day]) == [same_day.id]


def test_only_a_debit_of_the_same_account():
    credit = _line(date(2026, 7, 30), "3200.00", RECALL)
    elsewhere = _line(date(2026, 7, 28), "-3200.00", account=OTHER_ACCOUNT)
    assert return_candidates(credit, [elsewhere]) == []


def test_the_debit_whose_purpose_the_return_quotes_is_preferred():
    quoted = _line(date(2026, 7, 28), "-3200.00", "Umbuchung  Ruecklage JULI")
    unrelated = _line(date(2026, 7, 20), "-3200.00", "Kaufpreis Auto")
    credit = _line(date(2026, 7, 30), "3200.00", RECALL)
    assert return_candidates(credit, [unrelated, quoted]) == [quoted.id]


def test_a_short_purpose_never_decides():
    """An empty or very short purpose is contained in any text: it narrows nothing."""
    empty = _line(date(2026, 7, 28), "-3200.00", "")
    short = _line(date(2026, 7, 27), "-3200.00", "Juli")
    credit = _line(date(2026, 7, 30), "3200.00", RECALL)
    assert return_candidates(credit, [empty, short]) == [short.id, empty.id]


def test_else_the_debit_to_the_same_payee():
    payee = "a" * 64
    paid = _line(date(2026, 7, 28), "-3200.00", "Juli", iban=payee)
    other = _line(date(2026, 7, 27), "-3200.00", "Juli", iban="b" * 64)
    credit = _line(date(2026, 7, 30), "3200.00", "RETOURE", iban=payee)
    assert return_candidates(credit, [other, paid]) == [paid.id]
    by_name = _line(date(2026, 7, 28), "-50.00", name="Versand Beispiel GmbH")
    other_name = _line(date(2026, 7, 27), "-50.00", name="Buchladen Muster")
    refund = _line(date(2026, 7, 30), "50.00", "Retoure", name="VERSAND  beispiel gmbh")
    assert return_candidates(refund, [other_name, by_name]) == [by_name.id]


def test_several_that_fit_are_all_offered_never_one_guessed():
    first = _line(date(2026, 7, 10), "-3200.00", "Juli")
    second = _line(date(2026, 7, 28), "-3200.00", "Juli")
    credit = _line(date(2026, 7, 30), "3200.00", RECALL)
    assert return_candidates(credit, [second, first]) == [first.id, second.id]
    # Two debits the return both quotes: both offered.
    a = _line(date(2026, 7, 10), "-3200.00", "Umbuchung Ruecklage Juli")
    b = _line(date(2026, 7, 28), "-3200.00", "Umbuchung Ruecklage Juli")
    assert return_candidates(credit, [a, b]) == [a.id, b.id]
