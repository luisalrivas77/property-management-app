"""services.tax under hausbuch_app and RLS (docs/phases/phase-3.md §5.11, §10): the DraftFacts
it builds from the real tables (allocations and their due dates, cost rows and their
payments, rent rows in force, tax inputs, coverage, pending lines), the year checks, and that
no tenant data reaches the facts. The rule draft() is replaced by a stub here; its own
fixtures test it."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from sqlalchemy import insert, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import AcquisitionCostKind, ImportStatus, PriceSplitSource
from hausbuch.schemas.property import (
    AcquisitionCostCreate,
    Address,
    PriceSplitCreate,
    PropertyCreate,
)
from hausbuch.services import allocations
from hausbuch.services import tax as tax_service
from hausbuch.services.errors import NotFoundError
from hausbuch.services.properties import (
    add_acquisition_cost,
    create_property,
    record_price_split,
    select_price_split,
)
from hausbuch.services.tenancies import correct_rent_row, list_rent_schedule

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = date(2026, 11, 9)
TENANT_NAME = "Erika Mustermieterin"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _book(world: World, tx_id, *parts: dict, confirm: bool = True):
    """One allocation set on the line, confirmed (default) or proposed."""
    drafts = [AllocationDraft(**p) for p in parts]
    answer = AllocationSet(
        transaction_id=tx_id,
        allocations=drafts,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )
    return world.cmd(allocations.record_set, answer, confirm=confirm, auto=False)


def _facts(world: World, tax_year: int = 2026):
    return world.query(tax_service.draft_facts, world.property_id, tax_year=tax_year, today=TODAY)


def _scope(world: World, **more) -> dict:
    return {"property_id": world.property_id} | more


def _by_id(facts):
    return {a.allocation_id: a for a in facts.allocations}


# --- Allocations -------------------------------------------------------------------------


def test_only_confirmed_nonzero_allocations_of_the_property_or_its_accounts(world):
    fee = world.line(date(2026, 3, 31), "-4.90")
    (fee_row,) = _book(world, fee, {"amount": Decimal("-4.90"), "category": C.BANK_FEES})
    insured = world.line(date(2026, 3, 2), "-50.00")
    (insured_row,) = _book(
        world, insured, _scope(world, amount=Decimal("-50.00"), category=C.INSURANCE)
    )
    zero = world.line(date(2026, 3, 31), "0.00")
    _book(world, zero, _scope(world, amount=Decimal("0.00"), category=C.OTHER_EXPENSE))
    proposed = world.line(date(2026, 4, 2), "-20.00")
    _book(
        world,
        proposed,
        _scope(world, amount=Decimal("-20.00"), category=C.INSURANCE),
        confirm=False,
    )
    corrected = world.line(date(2026, 4, 3), "-30.00")
    (old,) = _book(world, corrected, _scope(world, amount=Decimal("-30.00"), category=C.REPAIRS))
    (new,) = _book(world, corrected, _scope(world, amount=Decimal("-30.00"), category=C.INSURANCE))

    facts = _facts(world)
    rows = _by_id(facts)
    assert set(rows) == {fee_row.id, insured_row.id, new.id}
    assert old.id not in rows  # superseded
    assert rows[insured_row.id].property_id == world.property_id
    # A line of the rental account without a property is read, with property_id None.
    assert rows[fee_row.id].property_id is None
    assert rows[fee_row.id].transaction_id == fee
    assert rows[fee_row.id].amount == Decimal("-4.90")


def test_a_line_without_a_property_on_another_account_is_not_read(world):
    with workspace_transaction(world.engine, world.ws) as conn:
        other = world._account(conn, "DE02120300000000202051", owner=False)
    _book(
        world,
        world.line(date(2026, 3, 2), "-50.00"),
        _scope(world, amount=Decimal("-50.00"), category=C.INSURANCE),
    )
    rental = (world.account_id, world.import_id)
    world.account_id = other
    world.import_id = _new_import(world, ImportStatus.RECONCILED, date(2026, 3, 31))
    stray = world.line(date(2026, 3, 3), "-9.00")
    world.account_id, world.import_id = rental
    (stray_row,) = _book(world, stray, {"amount": Decimal("-9.00"), "category": C.BANK_FEES})
    assert stray_row.id not in _by_id(_facts(world))


def test_due_dates_from_the_rent_due_day_the_schedule_and_the_period(world):
    tenancy = world.tenancy(start=date(2024, 6, 1), cold="850.00", prepayment="180.00")
    loan = world.loan()  # first annuity 1 May 2024, monthly
    january = {"start": date(2027, 1, 1), "end": date(2027, 1, 31)}

    rent = world.line(date(2026, 12, 29), "1030.00")
    cold, prepayment = _book(
        world,
        rent,
        _scope(
            world,
            amount=Decimal("850.00"),
            category=C.RENT_COLD,
            unit_id=world.unit_id,
            tenancy_id=tenancy,
            recurring=True,
            service_period_start=january["start"],
            service_period_end=january["end"],
        ),
        _scope(
            world,
            amount=Decimal("180.00"),
            category=C.OPERATING_COSTS_PREPAYMENT,
            unit_id=world.unit_id,
            tenancy_id=tenancy,
            recurring=True,
            service_period_start=january["start"],
            service_period_end=january["end"],
        ),
    )
    annuity = world.line(date(2026, 12, 30), "-1400.00")
    interest, principal = _book(
        world,
        annuity,
        _scope(world, amount=Decimal("-900.00"), category=C.LOAN_INTEREST, loan_id=loan),
        _scope(world, amount=Decimal("-500.00"), category=C.LOAN_PRINCIPAL, loan_id=loan),
    )
    far = world.line(date(2026, 6, 16), "-1400.00")  # 15 days from both due dates
    (far_row,) = _book(
        world,
        far,
        _scope(world, amount=Decimal("-1400.00"), category=C.LOAN_INTEREST, loan_id=loan),
    )
    hausgeld = world.line(date(2026, 12, 28), "-320.00")
    (hausgeld_row,) = _book(
        world,
        hausgeld,
        _scope(
            world,
            amount=Decimal("-320.00"),
            category=C.HAUSGELD_UMLAGEFAEHIG,
            recurring=True,
            service_period_start=january["start"],
            service_period_end=january["end"],
        ),
    )
    repair = world.line(date(2026, 12, 29), "-200.00")
    (repair_row,) = _book(
        world,
        repair,
        _scope(
            world,
            amount=Decimal("-200.00"),
            category=C.REPAIRS,
            service_period_start=date(2026, 11, 1),
            service_period_end=date(2026, 11, 30),
        ),
    )

    rows = _by_id(_facts(world))
    assert rows[cold.id].due_on == date(2027, 1, 3)  # rent_due_day 3 (D67 default)
    assert rows[prepayment.id].due_on == date(2027, 1, 3)
    # The loan line booked without a period: the schedule entry of 1 Jan 2027 (D39) gives
    # its due date, and the period of that instalment's month, recurring.
    for row in (rows[interest.id], rows[principal.id]):
        assert row.due_on == date(2027, 1, 1)
        assert row.recurring is True
        assert row.service_period is not None
        assert (row.service_period.start, row.service_period.end) == (
            date(2027, 1, 1),
            date(2027, 1, 31),
        )
    # No entry within 10 days: no due date, the rule keeps the year paid.
    assert rows[far_row.id].due_on is None
    assert rows[far_row.id].service_period is None
    assert rows[hausgeld_row.id].due_on == date(2027, 1, 1)  # the period's first day
    assert rows[repair_row.id].due_on is None  # not recurring


def test_rent_due_day_of_the_tenancy_is_used(world):
    tenancy = world.tenancy(start=date(2024, 6, 1))
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(tenancy_table).where(tenancy_table.c.id == tenancy).values(rent_due_day=15)
        )
    rent = world.line(date(2026, 3, 14), "1280.00")
    (row,) = _book(
        world,
        rent,
        _scope(
            world,
            amount=Decimal("1280.00"),
            category=C.RENT_COLD,
            unit_id=world.unit_id,
            tenancy_id=tenancy,
            recurring=True,
            service_period_start=date(2026, 3, 1),
            service_period_end=date(2026, 3, 31),
        ),
    )
    facts = _facts(world)
    assert _by_id(facts)[row.id].due_on == date(2026, 3, 15)
    (t,) = facts.tenancies
    assert t.rent_due_day == 15


def test_invoice_net_comes_from_the_linked_invoice(world):
    invoice = world.document("invoice", {"amount_total": "1190.00", "amount_net": "1000.00"})
    gross_only = world.document("invoice", {"amount_total": "595.00", "amount_net": None})
    painter = world.line(date(2026, 9, 21), "-1190.00")
    (with_net,) = _book(
        world,
        painter,
        _scope(world, amount=Decimal("-1190.00"), category=C.REPAIRS, document_id=invoice),
    )
    tiles = world.line(date(2026, 9, 22), "-595.00")
    (without_net,) = _book(
        world,
        tiles,
        _scope(world, amount=Decimal("-595.00"), category=C.REPAIRS, document_id=gross_only),
    )
    rows = _by_id(_facts(world))
    assert rows[with_net.id].invoice_net == Decimal("1000.00")
    assert rows[with_net.id].document_id == invoice
    assert rows[without_net.id].invoice_net is None


# --- The property record -----------------------------------------------------------------


def test_cost_rows_are_paired_with_their_payments(world):
    notary_invoice = world.document("invoice", {"amount_total": "4200.00"})
    grest = world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX,
            amount=Decimal("19500.00"),
        ),
    )
    notary = world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.NOTARY,
            amount=Decimal("4200.00"),
            document_id=notary_invoice,
        ),
    )
    unpaid = world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind=AcquisitionCostKind.LAND_REGISTRY,
            amount=Decimal("1300.00"),
        ),
    )
    by_amount = world.line(date(2024, 4, 20), "-19500.00")
    (grest_paid,) = _book(
        world,
        by_amount,
        _scope(world, amount=Decimal("-19500.00"), category=C.ACQUISITION_COST),
    )
    # A second payment of the same amount pays nothing more: one row, one allocation.
    again = world.line(date(2024, 4, 21), "-19500.00")
    _book(world, again, _scope(world, amount=Decimal("-19500.00"), category=C.ACQUISITION_COST))
    by_document = world.line(date(2024, 4, 25), "-4200.00")
    (notary_paid,) = _book(
        world,
        by_document,
        _scope(
            world,
            amount=Decimal("-4200.00"),
            category=C.ACQUISITION_COST,
            document_id=notary_invoice,
        ),
    )
    rows = {r.id: r for r in _facts(world).cost_rows}
    assert rows[grest.id].paid_by == [grest_paid.id]
    assert rows[notary.id].paid_by == [notary_paid.id]
    assert rows[notary.id].document_id == notary_invoice
    assert rows[unpaid.id].paid_by == []


def test_property_split_owners_units_and_loans(world):
    split = world.cmd(
        record_price_split,
        PriceSplitCreate(
            property_id=world.property_id,
            source=PriceSplitSource.APPRAISAL,
            building_share=Decimal("0.720000"),
            evidence_document_id=world.document("invoice", {}, type="other"),
        ),
    )
    world.cmd(select_price_split, world.property_id, split.id)
    loan = world.loan()
    world.document(
        "grundsteuerbescheid",
        {"reference": "999/123/45678", "tax_year": 2025},
        type="bescheid",
        subtype="grundsteuerbescheid",
    )
    facts = _facts(world)
    assert facts.property.id == world.property_id
    assert facts.property.purchase_date == date(2024, 5, 1)
    assert facts.property.purchase_price == Decimal("289000.00")
    assert facts.property.grundsteuer_reference == "999/123/45678"
    assert facts.split is not None
    assert (facts.split.id, facts.split.building_share) == (split.id, Decimal("0.720000"))
    assert [u.id for u in facts.units] == [world.unit_id]
    assert [(lo.id, lo.lender) for lo in facts.loans] == [(loan, "Beispielbank AG")]
    assert facts.owners == []  # no shares set
    assert facts.checklist is not None and facts.checklist.property_id == world.property_id
    assert facts.today == TODAY


def test_rent_rows_in_force_only(world):
    tenancy = world.tenancy(start=date(2024, 6, 1), cold="850.00", prepayment="180.00")
    (initial,) = world.query(list_rent_schedule, tenancy)
    world.cmd(correct_rent_row, initial.id, cold_rent=Decimal("870.00"))
    (t,) = _facts(world).tenancies
    assert [(r.cold_rent, r.operating_costs_prepayment) for r in t.rents] == [
        (Decimal("870.00"), Decimal("180.00"))
    ]
    assert t.unit_id == world.unit_id
    assert t.operating_costs_terms == "prepayment"


def test_no_tenant_data_in_the_facts(world):
    tenancy = world.tenancy(name=TENANT_NAME, start=date(2024, 6, 1))
    rent = world.line(date(2026, 3, 2), "1280.00", name=TENANT_NAME, purpose=TENANT_NAME)
    _book(
        world,
        rent,
        _scope(
            world,
            amount=Decimal("1280.00"),
            category=C.RENT_COLD,
            unit_id=world.unit_id,
            tenancy_id=tenancy,
        ),
    )
    dumped = _facts(world).model_dump_json()
    assert TENANT_NAME not in dumped
    assert "Mustermieterin" not in dumped


# --- Tax inputs --------------------------------------------------------------------------


def test_tax_inputs_carry_their_whole_history(world):
    first, second = uuid.uuid4(), uuid.uuid4()
    value = {"valid_from": "2026-01-01", "cold_rent_per_m2": "11.20", "source": "mietspiegel"}
    with workspace_transaction(world.engine, world.ws) as conn:
        for row in (
            {"id": first, "value": value, "withdrawn": False, "supersedes_id": None},
            {"id": second, "value": value, "withdrawn": True, "supersedes_id": first},
        ):
            conn.execute(
                insert(tax_input_table).values(
                    workspace_id=world.ws,
                    property_id=world.property_id,
                    kind="market_rent",
                    recorded_at=world.now,
                    **row,
                )
            )
    inputs = _facts(world).tax_inputs
    assert [(i.id, i.withdrawn, i.supersedes_id) for i in inputs] == [
        (first, False, None),
        (second, True, first),
    ]
    assert inputs[0].value.cold_rent_per_m2 == Decimal("11.20")  # type: ignore[union-attr]


# --- Coverage and pending lines ----------------------------------------------------------


def _set_import(world: World, import_id, **values) -> None:
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(update(import_table).where(import_table.c.id == import_id).values(**values))


def _new_import(world: World, status: ImportStatus, last: date):
    """An import row the statement_import CHECKs accept: a file balance declares a closing
    one, a reconciled import's computed closing equals it, and an unreconciled or failed one
    carries its error code (as World's own import and set_import_status do)."""
    failed = status in (ImportStatus.UNRECONCILED, ImportStatus.FAILED)
    with workspace_transaction(world.engine, world.ws) as conn:
        doc = world._document_row(conn, type="statement", subtype=None, title=None)
        return conn.execute(
            insert(import_table)
            .values(
                workspace_id=world.ws,
                account_id=world.account_id,
                document_id=doc,
                format="camt",
                status=status.value,
                balance_source="file",
                declared_closing=Decimal("0.00"),
                computed_closing=Decimal("0.00"),
                error_code="statement.unreconciled" if failed else None,
                first_booking_on=last.replace(day=1),
                last_booking_on=last,
                created_at=world.now,
                updated_at=world.now,
            )
            .returning(import_table.c.id)
        ).scalar_one()


def test_coverage_is_the_last_reconciled_day_per_account(world):
    # Both dates in one update: the CHECK wants them set (or empty) together.
    _set_import(
        world,
        world.import_id,
        first_booking_on=date(2026, 1, 1),
        last_booking_on=date(2026, 11, 30),
    )
    _new_import(world, ImportStatus.UNRECONCILED, date(2026, 12, 30))
    _book(
        world,
        world.line(date(2026, 3, 2), "-50.00"),
        _scope(world, amount=Decimal("-50.00"), category=C.INSURANCE),
    )
    (coverage,) = _facts(world).coverage
    assert coverage.account_id == world.account_id
    assert coverage.covered_through == date(2026, 11, 30)


def test_no_allocation_no_account(world):
    facts = _facts(world)
    assert facts.coverage == []
    assert facts.allocations == []
    assert (facts.pending.count, facts.pending.sum) == (0, Decimal("0"))


def test_pending_counts_proposed_and_unmatched_lines_of_the_year(world):
    other = world.cmd(
        create_property,
        PropertyCreate(
            label="Wohnung Bergstraße",
            kind="etw",
            address=Address(
                street="Bergstraße", house_number="3", postal_code="10115", city="Berlin"
            ),
            build_year=2001,
            notarized_on=date(2024, 3, 15),
            purchase_date=date(2024, 5, 1),
            purchase_price=Decimal("200000.00"),
        ),
    ).id
    _book(
        world,
        world.line(date(2026, 3, 2), "-50.00"),
        _scope(world, amount=Decimal("-50.00"), category=C.INSURANCE),
    )  # confirmed: in the draft
    _book(
        world,
        world.line(date(2026, 12, 3), "-60.00"),
        _scope(world, amount=Decimal("-60.00"), category=C.INSURANCE),
        confirm=False,
    )  # proposed for this property: pending
    _book(
        world,
        world.line(date(2026, 12, 4), "-29.90"),
        {"amount": Decimal("-29.90"), "category": C.BANK_FEES},
        confirm=False,
    )  # proposed without a property: pending
    _book(
        world,
        world.line(date(2026, 12, 5), "-15.00"),
        {"amount": Decimal("-15.00"), "category": C.INSURANCE, "property_id": other},
        confirm=False,
    )  # proposed for the other property: not this draft's
    world.line(date(2026, 12, 6), "100.00")  # not matched at all: pending
    world.line(date(2025, 12, 6), "-70.00")  # another year
    late = _new_import(world, ImportStatus.UNRECONCILED, date(2026, 12, 31))
    world.line(date(2026, 12, 31), "-80.00", import_id=late)  # not reconciled: coverage's
    merged = world.line(date(2026, 12, 7), "-90.00")
    keep = world.line(date(2026, 12, 7), "-90.00")
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(transaction_table)
            .where(transaction_table.c.id == merged)
            .values(excluded_as_duplicate_of=keep)
        )  # the kept one is open: pending once

    pending = _facts(world).pending
    assert pending.count == 4
    assert pending.sum == Decimal("-60.00") + Decimal("-29.90") + Decimal("100.00") + Decimal(
        "-90.00"
    )
    assert _facts(world, tax_year=2025).pending.count == 1


# --- The draft call, the years and RLS ---------------------------------------------------


def test_the_draft_gets_the_facts_the_year_and_its_rule_set(world, monkeypatch):
    calls = []

    def fake(facts, tax_year, rule_set):
        calls.append((facts, tax_year, rule_set))
        return "the draft"

    monkeypatch.setattr(tax_service, "draft", fake)
    result = world.query(tax_service.anlage_v_draft, world.property_id, tax_year=2026, today=TODAY)
    assert result == "the draft"
    ((facts, year, rule_set),) = calls
    assert facts.property.id == world.property_id
    assert year == 2026
    assert (rule_set.tax_year, rule_set.form.status) == (2026, "provisional")


def test_years_outside_or_without_a_rule_set_are_refused(world, monkeypatch):
    monkeypatch.setattr(tax_service, "draft", lambda *a: pytest.fail("not called"))
    with pytest.raises(RuleError) as err:  # before the purchase year (2024)
        world.query(tax_service.anlage_v_draft, world.property_id, tax_year=2023, today=TODAY)
    assert err.value.code == "tax.year_not_available"
    with pytest.raises(RuleError) as err:  # after today's year
        world.query(tax_service.anlage_v_draft, world.property_id, tax_year=2027, today=TODAY)
    assert err.value.code == "tax.year_not_available"
    with pytest.raises(RuleError) as err:  # owned, but no rulesets/2024.yaml
        world.query(tax_service.anlage_v_draft, world.property_id, tax_year=2024, today=TODAY)
    assert err.value.code == "tax.no_rule_set"


def test_another_workspace_sees_no_property(world, app_engine, fresh_workspaces, monkeypatch):
    monkeypatch.setattr(tax_service, "draft", lambda *a: pytest.fail("not called"))
    _book(
        world,
        world.line(date(2026, 3, 2), "-50.00"),
        _scope(world, amount=Decimal("-50.00"), category=C.INSURANCE),
    )
    _, b = fresh_workspaces
    with workspace_transaction(app_engine, b) as conn, pytest.raises(NotFoundError):
        tax_service.anlage_v_draft(conn, world.property_id, tax_year=2026, today=TODAY)
    with workspace_transaction(app_engine, b) as conn, pytest.raises(NotFoundError):
        tax_service.draft_facts(conn, world.property_id, tax_year=2026, today=TODAY)
    with workspace_transaction(app_engine, b) as conn, pytest.raises(NotFoundError):
        tax_service.anlage_v_sources(conn, world.property_id, tax_year=2026, today=TODAY)


# --- The sources drawer ------------------------------------------------------------------


def test_the_sources_are_the_drafts_allocations(world):
    insured = world.line(date(2026, 3, 2), "-50.00")
    (insured_row,) = _book(
        world, insured, _scope(world, amount=Decimal("-50.00"), category=C.INSURANCE)
    )
    earlier = world.line(date(2025, 6, 2), "-80.00")
    (earlier_row,) = _book(
        world, earlier, _scope(world, amount=Decimal("-80.00"), category=C.REPAIRS)
    )
    sources = world.query(
        tax_service.anlage_v_sources, world.property_id, tax_year=2026, today=TODAY
    )
    # The same rows the draft reads, every year (a line can cite another year's payment).
    assert sources == _facts(world).allocations
    rows = {a.allocation_id: a for a in sources}
    assert set(rows) == {insured_row.id, earlier_row.id}
    assert rows[insured_row.id].transaction_id == insured
    assert rows[insured_row.id].booking_date == date(2026, 3, 2)


def test_the_sources_refuse_the_years_the_draft_refuses(world):
    for year, code in ((2023, "tax.year_not_available"), (2024, "tax.no_rule_set")):
        with pytest.raises(RuleError) as err:
            world.query(tax_service.anlage_v_sources, world.property_id, tax_year=year, today=TODAY)
        assert err.value.code == code
