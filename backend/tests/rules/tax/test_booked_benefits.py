"""Phase 6 D159 amended (8): confirmed wage-replacement lines on the accounts against the typed
Lohnersatzleistungen (rules/tax/anlage_n/booked_benefits). Invented owners, payees and
amounts only (the repo is public)."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from hausbuch.rules.tax.anlage_n.booked_benefits import (
    benefit_kind,
    booked_benefit_warnings,
    kind_of_text,
)
from hausbuch.schemas.employment import BookedBenefit, OwnerReturnFacts, ReturnInputFact
from hausbuch.schemas.tax import WarningCode

pytestmark = pytest.mark.rule

A = UUID("00000000-0000-4000-8000-00000000000a")
B = UUID("00000000-0000-4000-8000-00000000000b")


def _line(amount: str, owners: list[UUID], text: str = "Elterngeld 03/2026", on=None):
    return BookedBenefit(
        allocation_id=uuid4(),
        booking_date=on or date(2026, 3, 30),
        amount=Decimal(amount),
        text=text,
        owner_ids=owners,
    )


def _typed(owner: UUID, amount: str, year: int = 2026, benefit: str = "elterngeld"):
    return ReturnInputFact(
        id=uuid4(),
        owner_id=owner,
        kind="replacement_income",
        tax_year=year,
        value={"benefit": benefit, "amount": amount},
    )


def _facts(*typed: ReturnInputFact, owner: UUID = A) -> OwnerReturnFacts:
    return OwnerReturnFacts(owner_id=owner, inputs=list(typed))


def test_one_owner_with_nothing_typed_gets_the_booked_sum():
    lines = [_line("643.50", [A]), _line("643.50", [A], on=date(2026, 4, 29))]
    (got,) = booked_benefit_warnings(2026, [_facts(), _facts(owner=B)], lines)
    assert got.code is WarningCode.RETURN_BENEFIT_BOOKED
    assert got.severity == "warning"
    assert got.params == {
        "tax_year": "2026",
        "booked": "1287.00",
        "typed": "0.00",
        "difference": "1287.00",
        "owner_id": str(A),
        "benefit": "elterngeld",
    }
    assert [(r.kind, r.id, r.amount) for r in got.refs] == [
        ("allocation", x.allocation_id, x.amount) for x in lines
    ]


def test_a_joint_account_asks_for_the_owner():
    lines = [_line("900.00", [A, B], text="Krankenkasse Krankengeld 01.05.-31.05.2026")]
    (got,) = booked_benefit_warnings(2026, [_facts(), _facts(owner=B)], lines)
    assert "owner_id" not in got.params
    assert (got.params["difference"], got.params["benefit"]) == ("900.00", "krankengeld")


def test_an_account_without_recorded_owners_asks_for_the_owner_too():
    (got,) = booked_benefit_warnings(2026, [_facts()], [_line("100.00", [])])
    assert "owner_id" not in got.params


def test_only_lines_booked_in_the_tax_year_count():
    lines = [
        _line("500.00", [A], on=date(2025, 12, 31)),
        _line("300.00", [A], on=date(2026, 1, 2)),
        _line("200.00", [A], on=date(2027, 1, 1)),
    ]
    (got,) = booked_benefit_warnings(2026, [_facts()], lines)
    assert got.params["booked"] == "300.00"
    assert [r.id for r in got.refs] == [lines[1].allocation_id]
    assert booked_benefit_warnings(2024, [_facts()], lines) == []


def test_typed_equal_to_booked_needs_nothing():
    lines = [_line("643.50", [A]), _line("643.50", [A])]
    assert booked_benefit_warnings(2026, [_facts(_typed(A, "1287.00"))], lines) == []


def test_typed_more_than_booked_needs_nothing():
    lines = [_line("643.50", [A])]
    assert booked_benefit_warnings(2026, [_facts(_typed(A, "1287.00"))], lines) == []


def test_typed_less_than_booked_asks_for_the_difference():
    typed = _typed(A, "643.50")
    lines = [_line("643.50", [A]), _line("643.50", [A])]
    (got,) = booked_benefit_warnings(2026, [_facts(typed)], lines)
    assert (got.params["booked"], got.params["typed"], got.params["difference"]) == (
        "1287.00",
        "643.50",
        "643.50",
    )
    assert ("return_input", typed.id) in [(r.kind, r.id) for r in got.refs]


def test_a_row_of_another_year_or_a_withdrawn_row_is_not_typed():
    old = _typed(A, "1287.00", year=2025)
    row = _typed(A, "1287.00")
    gone = ReturnInputFact(
        id=uuid4(),
        owner_id=A,
        kind="replacement_income",
        tax_year=2026,
        value={"benefit": "elterngeld", "amount": "1287.00"},
        supersedes_id=row.id,
        withdrawn=True,
    )
    lines = [_line("1287.00", [A])]
    (got,) = booked_benefit_warnings(2026, [_facts(old, row, gone)], lines)
    assert got.params["typed"] == "0.00"


def test_a_payment_back_lowers_the_booked_sum():
    lines = [_line("1287.00", [A]), _line("-643.50", [A], text="Rückforderung Elterngeld")]
    (got,) = booked_benefit_warnings(2026, [_facts()], lines)
    assert got.params["difference"] == "643.50"


def test_each_owner_is_compared_with_their_own_rows():
    lines = [_line("1000.00", [A]), _line("400.00", [B])]
    facts = [_facts(_typed(A, "1000.00")), _facts(owner=B)]
    (got,) = booked_benefit_warnings(2026, facts, lines)
    assert (got.params["owner_id"], got.params["difference"]) == (str(B), "400.00")


def test_joint_lines_count_as_covered_by_what_an_owner_typed_beyond_their_own_lines():
    joint = [_line("900.00", [A, B])]
    assert booked_benefit_warnings(2026, [_facts(_typed(A, "900.00"))], joint) == []
    (got,) = booked_benefit_warnings(2026, [_facts(_typed(A, "400.00"))], joint)
    assert (got.params["typed"], got.params["difference"]) == ("400.00", "500.00")


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Elterngeldstelle Elterngeld 03/2026", "elterngeld"),
        ("ELTERNGELDSTELLE ElterngeldPlus", "elterngeld"),
        ("Krankenkasse Mutterschaftsgeld 01.02.-15.05.2026", "mutterschaftsgeld"),
        ("Krankenkasse Kinderkrankengeld", "krankengeld"),
        ("Bundesagentur für Arbeit Arbeitslosengeld 04/2026", "arbeitslosengeld"),
        ("Bundesagentur für Arbeit Arbeitslosengeld 02/2026", "arbeitslosengeld"),
        # Kurzarbeitergeld is the wage certificate's Nr. 15 (phase 4b D102), never a kind here.
        ("Muster GmbH Kurzarbeitergeld", None),
        # The Krankenkasse names no kind: Mutterschaftsgeld or Krankengeld is never guessed.
        ("Krankenkasse Leistung von 01.03.2026 bis 31.03.2026", None),
        ("Mutterschaftsgeld und Krankengeld", None),
        ("Jobcenter Arbeitslosengeld II", None),
        ("Jobcenter Arbeitslosengeld-II", None),
        ("Jobcenter Arbeitslosengeld-2", None),
        ("Jobcenter Arbeitslosengeld 2", None),
        ("", None),
    ],
)
def test_the_benefit_kind_is_read_from_the_text(text, kind):
    assert kind_of_text(text) == kind


def test_the_kind_is_given_only_when_every_line_names_the_same():
    assert benefit_kind([_line("1", [A]), _line("1", [A], text="Elterngeld 04/2026")]) == (
        "elterngeld"
    )
    mixed = [_line("1", [A]), _line("1", [A], text="Krankengeld")]
    assert benefit_kind(mixed) is None
    unnamed = [_line("1", [A]), _line("1", [A], text="Leistung von 01.04. bis 30.04.")]
    assert benefit_kind(unnamed) is None
    (got,) = booked_benefit_warnings(2026, [_facts()], unnamed)
    assert "benefit" not in got.params


def test_kurzarbeitergeld_on_the_certificate_is_never_asked_for_again():
    """B's certificate carries 4,000.00 Kurzarbeitergeld on Nr. 15, which is already in the
    Progressionsvorbehalt; the same money booked on B's account must not ask to type it."""
    lines = [
        _line("4000.00", [B], text="Muster GmbH Kurzarbeitergeld 04/2026"),
        _line("150.00", [B], text="Muster GmbH KUG Nachzahlung"),
        _line("300.00", [A, B], text="Kurzarbeit Erstattung 05/2026"),
    ]
    assert booked_benefit_warnings(2026, [_facts(), _facts(owner=B)], lines) == []


def test_a_typed_kurzarbeitergeld_row_covers_no_other_benefit():
    facts = [_facts(_typed(A, "1000.00", benefit="kurzarbeitergeld")), _facts(owner=B)]
    (got,) = booked_benefit_warnings(2026, facts, [_line("1000.00", [A])])
    assert (got.params["typed"], got.params["difference"]) == ("0.00", "1000.00")


def test_a_line_on_one_owners_account_typed_under_the_other_owner_is_covered():
    """The spouses are assessed jointly: Elterngeld of B paid into A's own account and typed
    under B is on the return once, so no to-do asks to type it again under A."""
    lines = [_line("1800.00", [A])]
    facts = [_facts(), _facts(_typed(B, "1800.00"), owner=B)]
    assert booked_benefit_warnings(2026, facts, lines) == []


def test_the_differences_sum_to_the_households_gap():
    """A's own lines 1,000, B's own lines 400, 1,200 typed under A: 200 are missing, all of
    them B's, never B's whole 400."""
    lines = [_line("1000.00", [A]), _line("400.00", [B])]
    typed = _typed(A, "1200.00")
    (got,) = booked_benefit_warnings(2026, [_facts(typed), _facts(owner=B)], lines)
    assert got.params == {
        "tax_year": "2026",
        "booked": "400.00",
        "typed": "200.00",
        "difference": "200.00",
        "owner_id": str(B),
        "benefit": "elterngeld",
    }
    assert ("return_input", typed.id) in [(r.kind, r.id) for r in got.refs]


def test_owners_short_first_then_the_joint_lines():
    lines = [_line("400.00", [B]), _line("500.00", [A, B])]
    facts = [_facts(_typed(A, "300.00")), _facts(owner=B)]
    got = booked_benefit_warnings(2026, facts, lines)
    assert [(w.params.get("owner_id"), w.params["difference"]) for w in got] == [
        (str(B), "400.00"),
        (None, "200.00"),
    ]


def test_a_payment_back_larger_than_the_benefits_never_counts_as_typed():
    """A's own sum is −500 (a payment back); it must not cover the joint +500."""
    lines = [
        _line("-500.00", [A], text="Rückforderung Elterngeld"),
        _line("500.00", [A, B]),
    ]
    (got,) = booked_benefit_warnings(2026, [_facts(), _facts(owner=B)], lines)
    assert "owner_id" not in got.params
    assert (got.params["booked"], got.params["difference"]) == ("500.00", "500.00")


def test_nothing_booked_needs_nothing():
    assert booked_benefit_warnings(2026, [_facts()], []) == []


def test_every_kind_read_is_a_benefit_the_form_saves():
    """The pre-filled kind must be one ReplacementFact accepts, or the form would drop it."""
    from typing import get_args

    from hausbuch.rules.tax.anlage_n.booked_benefits import _KINDS
    from hausbuch.schemas.employment import ReplacementFact

    saved = set(get_args(ReplacementFact.model_fields["benefit"].annotation))
    assert {code for code, _ in _KINDS} <= saved
