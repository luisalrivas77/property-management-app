"""GET /api/properties/{id}/anlage-v (docs/phases/phase-3.md §10): the draft over HTTP, its
years and its error codes. draft() itself is replaced by a stub returning a minimal draft;
the rule's own fixtures test what it computes."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from api_support import flat, ok

from hausbuch.schemas.tax import (
    AnlageVDraft,
    Coverage,
    DraftHeader,
    DraftTotals,
    RuleSetStamp,
)
from hausbuch.services import tax as tax_service

pytestmark = pytest.mark.db


def _minimal(facts, tax_year, rule_set) -> AnlageVDraft:
    return AnlageVDraft(
        property_id=facts.property.id,
        tax_year=tax_year,
        rule_set=RuleSetStamp(
            tax_year=rule_set.tax_year,
            version=rule_set.version,
            form_year=rule_set.form.form_year,
            form_status=rule_set.form.status,
        ),
        header=DraftHeader(
            transfer_date=facts.property.purchase_date, completed=facts.property.build_year
        ),
        totals=DraftTotals(
            income=Decimal("0.00"),
            costs=Decimal("0.00"),
            result=Decimal("0.00"),
            income_rounded=0,
            costs_rounded=0,
            result_rounded=0,
        ),
        coverage=Coverage(months_owned=12, months_covered=0),
    )


@pytest.fixture
def stub_draft(monkeypatch) -> list:
    calls: list = []

    def fake(facts, tax_year, rule_set):
        calls.append((facts, tax_year, rule_set))
        return _minimal(facts, tax_year, rule_set)

    monkeypatch.setattr(tax_service, "_draft", fake)
    return calls


def _draft(client, property_id, **params):
    return client.get(f"/api/properties/{property_id}/anlage-v", params=params)


def test_the_draft_of_a_year(client_a, stub_draft):
    setup = flat(client_a)  # bought 1 May 2024
    pid = setup["property"]["id"]
    body = ok(_draft(client_a, pid, tax_year=2026))
    assert body["property_id"] == pid
    assert body["tax_year"] == 2026
    assert body["rule_set"]["form_status"] == "provisional"
    assert body["header"]["transfer_date"] == "2024-05-01"
    assert body["totals"]["result"] == "0.00"  # money stays a decimal string
    ((facts, year, rule_set),) = stub_draft
    assert facts.property.id == UUID(pid)
    assert (year, rule_set.tax_year) == (2026, 2026)
    assert [t.id for t in facts.tenancies] == [UUID(setup["tenancy"]["id"])]


def test_a_year_outside_the_available_years_is_422(client_a, stub_draft):
    pid = flat(client_a)["property"]["id"]
    response = _draft(client_a, pid, tax_year=2023)
    assert response.status_code == 422
    assert response.json()["code"] == "tax.year_not_available"
    assert stub_draft == []


def test_a_year_without_a_rule_set_is_422(client_a, stub_draft):
    pid = flat(client_a)["property"]["id"]
    response = _draft(client_a, pid, tax_year=2024)
    assert response.status_code == 422
    assert response.json()["code"] == "tax.no_rule_set"
    assert stub_draft == []


def test_the_year_is_required_and_bounded(client_a, stub_draft):
    pid = flat(client_a)["property"]["id"]
    assert _draft(client_a, pid).status_code == 422
    response = _draft(client_a, pid, tax_year=1999)
    assert response.status_code == 422
    assert response.json()["error"] == "validation"


def test_an_unknown_or_foreign_property_is_404(client_a, client_b, stub_draft):
    pid = flat(client_a)["property"]["id"]
    for client, property_id in ((client_a, str(uuid4())), (client_b, pid)):
        response = _draft(client, property_id, tax_year=2026)
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
    assert stub_draft == []


def test_today_bounds_the_years(client_a, stub_draft):
    """The current (Berlin) year is the last available one."""
    pid = flat(client_a)["property"]["id"]
    next_year = date.today().year + 1
    response = _draft(client_a, pid, tax_year=next_year)
    assert response.status_code == 422
    assert response.json()["code"] == "tax.year_not_available"
