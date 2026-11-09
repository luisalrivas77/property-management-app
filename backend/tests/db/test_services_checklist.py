"""services.checklist end to end under hausbuch_app and RLS (spec amendment 3): the facts
it loads from the real tables, the year handling, and that no tenant data reaches the
response."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY

from hausbuch.schemas.checklist import ChecklistScopeType, ChecklistStatus, RequirementCode
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.enums import (
    AcquisitionCostKind,
    DepositForm,
    DocumentSource,
    DocumentSubtype,
    DocumentType,
    LinkTarget,
    PriceSplitSource,
    PropertyKind,
    RentModel,
    TenancyRole,
)
from hausbuch.schemas.loan import LoanCreate
from hausbuch.schemas.property import (
    AcquisitionCostCreate,
    Address,
    PriceSplitCreate,
    PropertyCreate,
    UnitCreate,
)
from hausbuch.schemas.tenancy import TenancyCreate, TenancyParty, TenantCreate
from hausbuch.services.checklist import document_checklist
from hausbuch.services.documents import link_document, type_document, upload_document
from hausbuch.services.errors import NotFoundError
from hausbuch.services.loans import create_loan
from hausbuch.services.properties import (
    add_acquisition_cost,
    create_property,
    record_price_split,
    remove_acquisition_cost,
    select_price_split,
    update_acquisition_cost,
)
from hausbuch.services.tenancies import create_tenancy
from hausbuch.services.tenants import create_tenant
from hausbuch.services.units import create_unit

NOW = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 6)
TENANT_NAME = "Erika Mustermann"


def _upload(run, ws, store, typing, *, minutes):
    doc = run(
        ws,
        upload_document,
        store,
        f"%PDF-1.7 {uuid.uuid4()}".encode(),
        media_type="application/pdf",
        source=DocumentSource.UPLOAD,
        now=NOW + timedelta(minutes=minutes),
    )
    run(ws, type_document, doc.id, typing, None, now=NOW + timedelta(minutes=minutes))
    return doc


@pytest.fixture
def flat(run, fresh_workspaces, blob_store):
    """An ETW bought on 1 May 2024 with a let unit, a loan, a cost row, an appraisal split
    and some of its paperwork."""
    a, _ = fresh_workspaces
    prop = run(
        a,
        create_property,
        PropertyCreate(
            label="Wohnung Lindenstraße",
            kind=PropertyKind.ETW,
            address=Address(
                street="Lindenstraße", house_number="12", postal_code="10115", city="Berlin"
            ),
            build_year=1994,
            notarized_on=date(2024, 3, 15),
            purchase_date=date(2024, 5, 1),
            purchase_price=Decimal("289000.00"),
        ),
        now=NOW,
    )
    unit = run(
        a,
        create_unit,
        UnitCreate(property_id=prop.id, label="WE 3", floor_area_m2=Decimal("68.40")),
        now=NOW,
    )
    tenant = run(
        a, create_tenant, TenantCreate(full_name=TENANT_NAME), iban_key=TEST_IBAN_KEY, now=NOW
    )
    tenancy = run(
        a,
        create_tenancy,
        TenancyCreate(
            unit_id=unit.id,
            parties=[TenancyParty(tenant_id=tenant.id, role=TenancyRole.PRIMARY)],
            start_date=date(2024, 7, 1),
            rent_model=RentModel.FIXED,
            deposit_amount=Decimal("2400.00"),
            deposit_form=DepositForm.CASH_ACCOUNT,
        ),
        initial_rent=None,
        iban_key=TEST_IBAN_KEY,
        now=NOW,
    )
    loan = run(
        a,
        create_loan,
        LoanCreate(
            property_id=prop.id,
            lender="Sparkasse Musterstadt",
            principal=Decimal("240000.00"),
            nominal_rate=Decimal("0.0365"),
            monthly_payment=Decimal("1130.00"),
            disbursed_on=date(2024, 4, 30),
            first_payment_on=date(2024, 5, 30),
        ),
        iban_key=TEST_IBAN_KEY,
        now=NOW,
    )
    # Documents, received one minute apart.
    contract = _upload(
        run,
        a,
        blob_store,
        DocumentTyping(type=DocumentType.CONTRACT, subtype=DocumentSubtype.PURCHASE_CONTRACT),
        minutes=1,
    )
    run(a, link_document, contract.id, LinkTarget.PROPERTY, prop.id, now=NOW)
    # The lease's title names the tenant: it must not reach the checklist.
    lease = _upload(
        run,
        a,
        blob_store,
        DocumentTyping(
            type=DocumentType.CONTRACT,
            subtype=DocumentSubtype.LEASE,
            title=f"Mietvertrag {TENANT_NAME}",
            document_date=date(2024, 6, 1),
        ),
        minutes=2,
    )
    run(a, link_document, lease.id, LinkTarget.TENANCY, tenancy.id, now=NOW)
    grundsteuer = _upload(
        run,
        a,
        blob_store,
        DocumentTyping(type=DocumentType.BESCHEID, subtype=DocumentSubtype.GRUNDSTEUERBESCHEID),
        minutes=3,
    )
    run(a, link_document, grundsteuer.id, LinkTarget.PROPERTY, prop.id, now=NOW)
    zins = _upload(
        run, a, blob_store, DocumentTyping(type=DocumentType.ZINSBESCHEINIGUNG), minutes=4
    )
    run(a, link_document, zins.id, LinkTarget.LOAN, loan.id, now=NOW)
    plan = _upload(
        run,
        a,
        blob_store,
        DocumentTyping(type=DocumentType.WIRTSCHAFTSPLAN, tax_year=2026),
        minutes=5,
    )
    run(a, link_document, plan.id, LinkTarget.UNIT, unit.id, now=NOW)
    # Referenced, not linked: the notary invoice and the Gutachten.
    notary_invoice = _upload(
        run,
        a,
        blob_store,
        DocumentTyping(type=DocumentType.INVOICE, subtype=DocumentSubtype.COST_BASIS_EVIDENCE),
        minutes=6,
    )
    cost = run(
        a,
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=prop.id,
            kind=AcquisitionCostKind.NOTARY,
            amount=Decimal("2100.50"),
            document_id=notary_invoice.id,
        ),
        now=NOW,
    )
    appraisal = _upload(
        run,
        a,
        blob_store,
        DocumentTyping(type=DocumentType.OTHER, subtype=DocumentSubtype.APPRAISAL),
        minutes=7,
    )
    split = run(
        a,
        record_price_split,
        PriceSplitCreate(
            property_id=prop.id,
            source=PriceSplitSource.APPRAISAL,
            building_share=Decimal("0.780000"),
            evidence_document_id=appraisal.id,
        ),
        now=NOW,
    )
    run(a, select_price_split, prop.id, split.id, now=NOW)
    return {
        "ws": a,
        "property": prop,
        "unit": unit,
        "tenancy": tenancy,
        "loan": loan,
        "cost": cost,
        "docs": {
            "contract": contract,
            "lease": lease,
            "grundsteuer": grundsteuer,
            "zins": zins,
            "plan": plan,
            "notary_invoice": notary_invoice,
            "appraisal": appraisal,
        },
    }


def test_the_checklist_of_a_let_and_financed_flat(read, flat):
    got = read(flat["ws"], document_checklist, flat["property"].id, tax_year=2026, today=TODAY)
    docs = flat["docs"]
    assert got.property_id == flat["property"].id
    assert got.tax_year == 2026
    assert got.available_years == [2024, 2025, 2026]
    assert [(i.code, i.status, i.scope.type, [d.id for d in i.documents]) for i in got.items] == [
        (
            RequirementCode.PURCHASE_CONTRACT,
            ChecklistStatus.PRESENT,
            ChecklistScopeType.PROPERTY,
            [docs["contract"].id],
        ),
        (
            RequirementCode.PRICE_SPLIT,
            ChecklistStatus.PRESENT,
            ChecklistScopeType.PROPERTY,
            [docs["appraisal"].id],
        ),
        (
            RequirementCode.ACQUISITION_COST_EVIDENCE,
            ChecklistStatus.PRESENT,
            ChecklistScopeType.ACQUISITION_COST,
            [docs["notary_invoice"].id],
        ),
        (
            RequirementCode.GRUNDSTEUERBESCHEID,
            ChecklistStatus.PRESENT,
            ChecklistScopeType.PROPERTY,
            [docs["grundsteuer"].id],
        ),
        (RequirementCode.LOAN_CONTRACT, ChecklistStatus.MISSING, ChecklistScopeType.LOAN, []),
        (
            RequirementCode.ZINSBESCHEINIGUNG,
            ChecklistStatus.INCOMPLETE,
            ChecklistScopeType.LOAN,
            [docs["zins"].id],
        ),
        (
            RequirementCode.LEASE,
            ChecklistStatus.PRESENT,
            ChecklistScopeType.TENANCY,
            [docs["lease"].id],
        ),
        (
            RequirementCode.HANDOVER_PROTOCOL,
            ChecklistStatus.MISSING,
            ChecklistScopeType.TENANCY,
            [],
        ),
        (RequirementCode.DEPOSIT_RECORD, ChecklistStatus.MISSING, ChecklistScopeType.TENANCY, []),
        (
            RequirementCode.WIRTSCHAFTSPLAN,
            ChecklistStatus.PRESENT,
            ChecklistScopeType.PROPERTY,
            [docs["plan"].id],
        ),
        (
            RequirementCode.HAUSGELDABRECHNUNG,
            ChecklistStatus.EXPECTED,
            ChecklistScopeType.PROPERTY,
            [],
        ),
        # Phase 3 (§8): the flat is let in the year and no ortsübliche Miete is recorded.
        (RequirementCode.MARKET_RENT, ChecklistStatus.MISSING, ChecklistScopeType.PROPERTY, []),
    ]
    by_code = {i.code: i for i in got.items}
    assert by_code[RequirementCode.LOAN_CONTRACT].scope.lender == "Sparkasse Musterstadt"
    lease = by_code[RequirementCode.LEASE]
    assert lease.scope.unit_label == "WE 3"
    assert lease.scope.tenancy_start == date(2024, 7, 1)
    assert lease.documents[0].document_date == date(2024, 6, 1)
    cost = by_code[RequirementCode.ACQUISITION_COST_EVIDENCE].scope
    assert (cost.id, cost.cost_kind, cost.cost_amount) == (
        flat["cost"].id,
        AcquisitionCostKind.NOTARY,
        Decimal("2100.50"),
    )
    assert by_code[RequirementCode.ZINSBESCHEINIGUNG].due_on == date(2027, 2, 28)


def test_no_tenant_data_in_the_checklist(read, flat):
    """D5: the tenant's name is in the tenant row and in the lease's title; neither is in
    the response, nor is any tenant id."""
    got = read(flat["ws"], document_checklist, flat["property"].id, tax_year=2026, today=TODAY)
    body = got.model_dump_json()
    assert TENANT_NAME not in body
    assert "Mustermann" not in body
    assert "title" not in body
    assert "tenant_id" not in body


def test_the_year_defaults_to_today_clamped_into_the_years(read, flat):
    ws, pid = flat["ws"], flat["property"].id
    assert read(ws, document_checklist, pid, tax_year=None, today=TODAY).tax_year == 2026
    # Before the purchase year (a clock far behind): the first year offered.
    assert read(ws, document_checklist, pid, tax_year=None, today=date(2023, 6, 1)).tax_year == (
        2024
    )


def test_a_property_bought_before_2000_offers_years_from_2000(run, read, fresh_workspaces):
    # Review finding: the response model refused 1999 in available_years (TaxYear >= 2000).
    a, _ = fresh_workspaces
    prop = run(
        a,
        create_property,
        PropertyCreate(
            label="Altbau",
            kind=PropertyKind.EFH,
            address=Address(
                street="Gartenweg", house_number="3", postal_code="10115", city="Berlin"
            ),
            build_year=1960,
            notarized_on=date(1999, 3, 15),
            purchase_date=date(1999, 5, 1),
            purchase_price=Decimal("150000.00"),
        ),
        now=NOW,
    )
    got = read(a, document_checklist, prop.id, tax_year=None, today=TODAY)
    assert got.available_years[0] == 2000 and got.available_years[-1] == 2026
    assert got.tax_year == 2026


def test_a_year_outside_the_available_years_is_refused(read, flat):
    for year in (2023, 2027):
        with pytest.raises(ValueError, match="outside this property's years 2024-2026"):
            read(flat["ws"], document_checklist, flat["property"].id, tax_year=year, today=TODAY)


def test_editing_a_cost_row_updates_its_checklist_item(run, read, flat, blob_store):
    """The item reads the row's document_id: a Beleg attached later by PATCH makes it present,
    clearing it makes it missing again, and a removed row has no item at all."""
    ws, pid = flat["ws"], flat["property"].id

    def cost_items():
        got = read(ws, document_checklist, pid, tax_year=2026, today=TODAY)
        return [
            (i.scope.id, i.status, [d.id for d in i.documents])
            for i in got.items
            if i.code is RequirementCode.ACQUISITION_COST_EVIDENCE
        ]

    tax = run(
        ws,
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=pid,
            kind=AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX,
            amount=Decimal("17340.00"),
        ),
        now=NOW,
    )
    notary = flat["cost"]
    invoice = flat["docs"]["notary_invoice"]
    assert cost_items() == [
        (tax.id, ChecklistStatus.MISSING, []),
        (notary.id, ChecklistStatus.PRESENT, [invoice.id]),
    ]

    bescheid = _upload(
        run,
        ws,
        blob_store,
        DocumentTyping(type=DocumentType.BESCHEID, subtype=DocumentSubtype.COST_BASIS_EVIDENCE),
        minutes=8,
    )
    run(ws, update_acquisition_cost, tax.id, document_id=bescheid.id, now=NOW)
    assert cost_items() == [
        (tax.id, ChecklistStatus.PRESENT, [bescheid.id]),
        (notary.id, ChecklistStatus.PRESENT, [invoice.id]),
    ]

    run(ws, update_acquisition_cost, notary.id, document_id=None, now=NOW)
    assert cost_items() == [
        (tax.id, ChecklistStatus.PRESENT, [bescheid.id]),
        (notary.id, ChecklistStatus.MISSING, []),
    ]

    run(ws, remove_acquisition_cost, notary.id, now=NOW)
    assert cost_items() == [(tax.id, ChecklistStatus.PRESENT, [bescheid.id])]


def test_another_workspaces_property_is_not_found(read, fresh_workspaces, flat):
    _, b = fresh_workspaces
    with pytest.raises(NotFoundError):
        read(b, document_checklist, flat["property"].id, tax_year=None, today=TODAY)
