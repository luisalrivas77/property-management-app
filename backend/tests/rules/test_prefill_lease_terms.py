"""Phase 2 D152: the lease's Nebenkosten clause and rent due clause, mapped by code to the
tenancy's operating_costs_terms (phase 3 D72) and rent_due_day (D67). The records these
values reach are covered by prefill.yaml; these are the two mappers' wordings."""

import pytest

from hausbuch.rules.prefill import _operating_costs_terms, _rent_due_day
from hausbuch.schemas.enums import OperatingCostsTerms

FLAT = OperatingCostsTerms.FLAT_RATE
PRE = OperatingCostsTerms.PREPAYMENT
INC = OperatingCostsTerms.INCLUDED


@pytest.mark.parametrize(
    ("clause", "terms"),
    [
        # The household's room leases: a Pauschale that covers heating and electricity.
        (
            "Nebenkosten inkl. Betriebs-, Heiz-, Warmwasser, Strom 90,00 Euro. Eine gesonderte"
            " Neben- oder Betriebskostenabrechnung erfolgt nicht.",
            FLAT,
        ),
        ("Nebenkostenpauschale inkl. Heizung und Strom: 120,00 EUR monatlich", FLAT),
        ("Die Betriebskosten werden pauschal mit 80 € abgegolten.", FLAT),
        ("Über die Nebenkosten wird keine Abrechnung erstellt.", FLAT),
        ("Die Nebenkosten werden nicht abgerechnet.", FLAT),
        (
            "Der Mieter leistet eine monatliche Vorauszahlung auf die Betriebskosten von"
            " 180,00 EUR, über die jährlich abgerechnet wird.",
            PRE,
        ),
        ("Betriebskostenvorauszahlung: 180,00 EUR", PRE),
        ("Abschlag auf die Heiz- und Betriebskosten 250 €, Abrechnung jährlich", PRE),
        (
            "Die Miete ist eine Inklusiv-/Warmmiete und enthält sämtliche Nebenkosten.",
            INC,
        ),
        ("Mit der Warmmiete von 650 € sind alle Betriebs- und Nebenkosten abgegolten.", INC),
        ("Pauschalmiete: Die Nebenkosten sind in der Miete enthalten.", INC),
        ("Warmmiete; eine Abrechnung der Nebenkosten erfolgt nicht.", INC),
        # Ambiguous or silent: missing, the review picks.
        ("Nebenkosten: 90,00 Euro", None),
        ("Nebenkosten inkl. Heizung", None),
        ("Die Betriebskosten werden jährlich abgerechnet.", None),
        ("Warmmiete 700 €; die Heizkosten werden jährlich abgerechnet.", None),
        (
            "Pauschale für Kabelfernsehen 10 €; Vorauszahlung auf die Betriebskosten 150 €,"
            " jährliche Abrechnung.",
            None,
        ),
    ],
)
def test_operating_costs_terms(clause, terms):
    assert _operating_costs_terms(clause) is terms


@pytest.mark.parametrize(
    ("clause", "day"),
    [
        ("spätestens am dritten Werktag eines jeden Monats", 3),
        ("Die Miete ist monatlich im Voraus, spätestens bis zum 3. Werktag zu zahlen.", 3),
        ("bis zum 5. eines jeden Monats", 5),
        ("Die Miete ist am 15. des Monats fällig.", 15),
        ("zum Monatsersten", 1),
        ("im Voraus zum 1. eines Monats", 1),
        ("monatlich im Voraus bis zum ersten des Monats", 1),
        ("spätestens am ersten Werktag", 1),
        ("bis zum zehnten Werktag", 10),
        # A date is not a due day; the clause states the day once more as a Werktag.
        ("ab dem 01.05.2026 spätestens am dritten Werktag", 3),
        # Unclear: no day, two days, or outside 1..28.
        ("monatlich im Voraus", None),
        ("im Voraus zum 1., spätestens am dritten Werktag", None),
        ("bis zum 30. eines Monats", None),
        ("gemäß § 4. Absatz 2", None),
    ],
)
def test_rent_due_day(clause, day):
    assert _rent_due_day(clause) == day
