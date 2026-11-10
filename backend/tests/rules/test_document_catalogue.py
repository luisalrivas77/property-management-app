"""The document catalogue (docs/phases/phase-2.md D130, D131, §6.2.1): routing of every subtype,
the tenant gate's two lists, the fields the general reading asks, and the retention class.

The table below is §6.2.1 typed out: subtype, its document type, retention (OWN, TEN, TAX =
O, M, S), the link its group proposes, and the reader (general unless named)."""

from datetime import date

import pytest

from hausbuch.ai.requests import field_list
from hausbuch.rules.extraction_parse import TENANT_SUBTYPES, extraction_kind
from hausbuch.rules.retention import document_retention
from hausbuch.schemas.document import DocumentRetentionInput, RetentionPolicy
from hausbuch.schemas.enums import DocumentSubtype as S
from hausbuch.schemas.enums import DocumentType as T
from hausbuch.schemas.enums import ExtractionKind as K
from hausbuch.schemas.enums import RetentionClass
from hausbuch.schemas.extraction import GENERAL_LINK, asked_fields
from hausbuch.services.proposals import _TENANT_SUBTYPES

OWN, TEN, TAX = (
    RetentionClass.OWNERSHIP_RECORDS,
    RetentionClass.TENANCY_RECORDS,
    RetentionClass.TAX_RECORDS,
)

# (subtype, type, retention, link, reader)
CATALOGUE = [
    # property and purchase
    (S.LAND_REGISTER_EXTRACT, T.LETTER, OWN, "property", K.GENERAL),
    (S.DECLARATION_OF_DIVISION, T.CONTRACT, OWN, "property", K.GENERAL),
    (S.LAND_CHARGE_DEED, T.CONTRACT, OWN, "property", K.GENERAL),
    (S.ENERGY_CERTIFICATE, T.OTHER, OWN, "property", K.GENERAL),
    (S.BUILDING_RECORDS, T.OTHER, OWN, "property", K.GENERAL),
    (S.INSURANCE_POLICY, T.CONTRACT, OWN, "property", K.GENERAL),
    (S.MANAGEMENT_CONTRACT, T.CONTRACT, OWN, "property", K.MANAGEMENT_CONTRACT),
    (S.SERVICE_CONTRACT, T.CONTRACT, OWN, "property", K.GENERAL),
    # WEG
    (S.WEG_MINUTES, T.LETTER, OWN, "property", K.GENERAL),
    (S.WEG_INVITATION, T.LETTER, TAX, "property", K.GENERAL),
    (S.SPECIAL_LEVY, T.LETTER, TAX, "property", K.GENERAL),
    # loan
    (S.REPAYMENT_SCHEDULE, T.OTHER, OWN, "loan", K.GENERAL),
    (S.LOAN_TERMS_CHANGE, T.LETTER, OWN, "loan", K.GENERAL),
    (S.LOAN_STATEMENT, T.STATEMENT, TAX, "loan", K.GENERAL),
    # tenancy
    (S.LEASE_AMENDMENT, T.CONTRACT, TEN, "tenancy", K.GENERAL),
    (S.RENT_INCREASE, T.LETTER, TEN, "tenancy", K.GENERAL),
    (S.TERMINATION_NOTICE, T.LETTER, TEN, "tenancy", K.GENERAL),
    (S.TENANT_SELF_DISCLOSURE, T.OTHER, TEN, "tenancy", K.GENERAL),
    (S.LANDLORD_CONFIRMATION, T.LETTER, TEN, "tenancy", K.GENERAL),
    (S.OPERATING_COSTS_STATEMENT, T.LETTER, TEN, "tenancy", K.GENERAL),
    (S.TENANT_CORRESPONDENCE, T.LETTER, TEN, "tenancy", K.GENERAL),
    # running costs
    (S.UTILITY_BILL, T.INVOICE, TAX, "property", K.INVOICE),
    (S.QUOTE, T.LETTER, TAX, "property", K.GENERAL),
    (S.CHIMNEY_SWEEP_NOTICE, T.BESCHEID, TAX, "property", K.GENERAL),
    # income tax
    (S.EINKOMMENSTEUERBESCHEID, T.BESCHEID, TAX, None, K.EINKOMMENSTEUERBESCHEID),
    (S.PREPAYMENT_NOTICE, T.BESCHEID, TAX, None, K.GENERAL),
    (S.TAX_RETURN_COPY, T.OTHER, TAX, None, K.GENERAL),
    (S.DONATION_RECEIPT, T.LETTER, TAX, None, K.GENERAL),
    (S.CHILDCARE_INVOICE, T.INVOICE, TAX, None, K.GENERAL),
    (S.HOUSEHOLD_SERVICES_INVOICE, T.INVOICE, TAX, None, K.INVOICE),
    (S.INSURANCE_CONTRIBUTION_CERTIFICATE, T.LETTER, TAX, None, K.GENERAL),
    (S.PENSION_CONTRIBUTION_CERTIFICATE, T.LETTER, TAX, None, K.GENERAL),
    (S.BENEFIT_NOTICE, T.BESCHEID, TAX, None, K.GENERAL),
    (S.CHILD_BENEFIT_NOTICE, T.BESCHEID, TAX, None, K.GENERAL),
    (S.EMPLOYMENT_CONTRACT, T.CONTRACT, TAX, None, K.GENERAL),
    (S.EMPLOYER_CERTIFICATE, T.LETTER, TAX, None, K.GENERAL),
    (S.WORK_EXPENSE_INVOICE, T.INVOICE, TAX, None, K.INVOICE),
    # household finances
    (S.DEPOT_STATEMENT, T.STATEMENT, TAX, None, K.GENERAL),
    (S.DEPOT_REPORT, T.OTHER, TAX, None, K.GENERAL),
    (S.TRADE_CONFIRMATION, T.LETTER, TAX, None, K.GENERAL),
    (S.CAPITAL_INCOME_TAX_CERTIFICATE, T.LETTER, TAX, None, K.GENERAL),
]
# The subtypes before the catalogue: (subtype, type, reader).
EXISTING = [
    (S.PURCHASE_CONTRACT, T.CONTRACT, K.PURCHASE_CONTRACT),
    (S.PRICE_SPLIT_EVIDENCE, T.OTHER, K.GENERAL),
    (S.APPRAISAL, T.OTHER, K.GENERAL),
    (S.LOAN_CONTRACT, T.CONTRACT, K.LOAN_CONTRACT),
    (S.LEASE, T.CONTRACT, K.LEASE),
    (S.HANDOVER_PROTOCOL, T.LETTER, K.HANDOVER_PROTOCOL),
    (S.DEPOSIT_RECORD, T.LETTER, K.DEPOSIT_RECORD),
    (S.COST_BASIS_EVIDENCE, T.INVOICE, K.INVOICE),
    (S.COST_BASIS_EVIDENCE, T.BESCHEID, K.BESCHEID),
    (S.GRUNDSTEUERBESCHEID, T.BESCHEID, K.GRUNDSTEUERBESCHEID),
]
ADDRESS = ("street", "house_number", "postal_code")


def test_the_table_covers_every_subtype():
    assert {row[0] for row in CATALOGUE} | {row[0] for row in EXISTING} == set(S)
    assert len(CATALOGUE) == 41  # 40 new subtypes and the Einkommensteuerbescheid


@pytest.mark.parametrize(("subtype", "type_", "retention", "link", "reader"), CATALOGUE)
def test_a_catalogue_subtype_is_routed_by_subtype_first(subtype, type_, retention, link, reader):
    assert extraction_kind(type_, subtype) is reader
    # whatever type the classifier gave it
    for other in T:
        assert extraction_kind(other, subtype) is reader
    assert GENERAL_LINK.get(subtype) == link


@pytest.mark.parametrize(("subtype", "type_", "reader"), EXISTING)
def test_an_existing_subtype_keeps_its_reader(subtype, type_, reader):
    assert extraction_kind(type_, subtype) is reader


@pytest.mark.parametrize(
    ("type_", "reader"),
    [
        (T.STATEMENT, K.STATEMENT),
        (T.BESCHEID, K.BESCHEID),
        (T.INVOICE, K.INVOICE),
        (T.WIRTSCHAFTSPLAN, K.WIRTSCHAFTSPLAN),
        (T.HAUSGELDABRECHNUNG, K.HAUSGELDABRECHNUNG),
        (T.ZINSBESCHEINIGUNG, K.ZINSBESCHEINIGUNG),
        (T.LOHNSTEUERBESCHEINIGUNG, K.LOHNSTEUERBESCHEINIGUNG),
        (T.GEHALTSABRECHNUNG, K.GEHALTSABRECHNUNG),
        (T.CONTRACT, K.GENERAL),
        (T.LETTER, K.GENERAL),
        (T.OTHER, K.GENERAL),
    ],
)
def test_without_a_subtype_the_type_decides_and_nothing_is_typing_only(type_, reader):
    assert extraction_kind(type_, None) is reader


def test_both_tenant_lists_hold_the_tenancy_group():
    tenancy = {row[0] for row in CATALOGUE if row[3] == "tenancy"}
    assert len(tenancy) == 7
    expected = tenancy | {S.LEASE, S.HANDOVER_PROTOCOL, S.DEPOSIT_RECORD}
    assert expected == TENANT_SUBTYPES
    assert expected == _TENANT_SUBTYPES


@pytest.mark.parametrize(("subtype", "type_", "retention", "link", "reader"), CATALOGUE)
def test_the_general_reading_asks_only_its_groups_fields(subtype, type_, retention, link, reader):
    asked = asked_fields(K.GENERAL, subtype)
    expected = {
        "property": ("title", "document_date", *ADDRESS),
        "tenancy": ("title", "document_date", *ADDRESS),
        "loan": ("title", "document_date", "lender", "contract_number"),
        None: ("title", "document_date"),
    }[link]
    assert asked == expected
    text = field_list(K.GENERAL, subtype)
    for name in ADDRESS + ("lender", "contract_number"):
        assert (f"\n{name}:" in text) == (name in expected), name


def test_no_subtype_asks_title_and_date_only():
    assert asked_fields(K.GENERAL, None) == ("title", "document_date")
    assert field_list(K.GENERAL).splitlines()[1:] == [
        line
        for line in field_list(K.GENERAL).splitlines()[1:]
        if line.startswith(("title:", "document_date:"))
    ]


def test_another_kind_asks_every_field_whatever_the_subtype():
    assert "sender" in asked_fields(K.INVOICE, S.UTILITY_BILL)
    assert asked_fields(K.MANAGEMENT_CONTRACT, S.MANAGEMENT_CONTRACT) == (
        "title",
        "document_date",
        "manager",
        "street",
        "house_number",
        "postal_code",
        "start_date",
        "monthly_fee",
        "notice_period",
    )


@pytest.mark.parametrize(("subtype", "type_", "retention", "link", "reader"), CATALOGUE)
def test_retention_follows_the_group(subtype, type_, retention, link, reader):
    got = document_retention(
        DocumentRetentionInput(
            type=type_,
            subtype=subtype,
            received_on=date(2026, 11, 10),
            tax_year=2026,
            ownership_end_dates=[date(2040, 6, 30)],
            tenancy_end_dates=[date(2030, 6, 30)],
        ),
        RetentionPolicy(),
    )
    assert got.retention_class is retention
