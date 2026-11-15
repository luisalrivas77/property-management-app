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
        ("L-Bank Elterngeld 03/2026", "elterngeld"),
        ("ELTERNGELDSTELLE ElterngeldPlus", "elterngeld"),
        ("Krankenkasse Mutterschaftsgeld 01.02.-15.05.2026", "mutterschaftsgeld"),
        ("Krankenkasse Kinderkrankengeld", "krankengeld"),
        ("Bundesagentur für Arbeit Arbeitslosengeld 04/2026", "arbeitslosengeld"),
        ("Muster GmbH Kurzarbeitergeld", "kurzarbeitergeld"),
        # The Krankenkasse names no kind: Mutterschaftsgeld or Krankengeld is never guessed.
        ("Krankenkasse Leistung von 01.03.2026 bis 31.03.2026", None),
        ("Mutterschaftsgeld und Krankengeld", None),
        ("Jobcenter Arbeitslosengeld II", None),
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


def test_nothing_booked_needs_nothing():
    assert booked_benefit_warnings(2026, [_facts()], []) == []
