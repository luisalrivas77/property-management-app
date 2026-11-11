"""The document catalogue (docs/phases/phase-2.md D130, D131, §6.2.1): routing of every subtype,
the tenant gate's two lists, the fields the general reading asks, and the retention class.

The table below is §6.2.1 typed out: subtype, its document type, retention (OWN, TEN, TAX =
O, M, S), the link its group proposes, and the reader (general unless named)."""

import json
from datetime import date

import pytest

from hausbuch.ai.requests import classify_schema, field_list, load_prompt, parse_classification
from hausbuch.rules.extraction_parse import (
    CATALOGUE_SUBTYPES,
    CODE_ONLY_SUBTYPES,
    TENANT_SUBTYPES,
    extraction_kind,
)
from hausbuch.rules.retention import document_retention
from hausbuch.schemas.document import DocumentRetentionInput, RetentionPolicy
from hausbuch.schemas.enums import DocumentSubtype as S
from hausbuch.schemas.enums import DocumentType as T
from hausbuch.schemas.enums import ExtractionKind as K
from hausbuch.schemas.enums import RetentionClass
from hausbuch.schemas.extraction import GENERAL_LINK, LOAN_GROUP, TENANCY_GROUP, asked_fields
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
    # phase 3 D143: typed and linked to its bill by code (no group link), its own reader
    (S.PAYMENT_PROOF, T.OTHER, TAX, None, K.PAYMENT_PROOF),
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
# A financing bill (D138): in the catalogue's loan group, but read by its type's reader like
# cost_basis_evidence, and set by code only. (subtype, type, retention, link, reader)
FINANCING = [
    (S.FINANCING_INVOICE, T.INVOICE, TAX, "loan", K.INVOICE),
    (S.FINANCING_INVOICE, T.BESCHEID, TAX, "loan", K.BESCHEID),
]
ADDRESS = ("street", "house_number", "postal_code")


def test_the_table_covers_every_subtype():
    tables = (CATALOGUE, EXISTING, FINANCING)
    assert {row[0] for table in tables for row in table} == set(S)
    # 40 new subtypes, the Einkommensteuerbescheid and the payment proof (phase 3 D143)
    assert len(CATALOGUE) == 42


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
    # D139: a Wohnflächenberechnung also lists its dwellings; no other subtype asks them.
    if subtype is S.BUILDING_RECORDS:
        expected = (*expected, "dwellings")
    assert asked == expected
    assert ("\ndwellings:" in field_list(K.GENERAL, subtype)) == (subtype is S.BUILDING_RECORDS)
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
        "fee_net",
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


# --- the dedicated types win over a catalogue subtype (D131, decided 10 Nov 2026) ---------

DEDICATED = [
    (T.LOHNSTEUERBESCHEINIGUNG, K.LOHNSTEUERBESCHEINIGUNG),
    (T.GEHALTSABRECHNUNG, K.GEHALTSABRECHNUNG),
    (T.ZINSBESCHEINIGUNG, K.ZINSBESCHEINIGUNG),
    (T.WIRTSCHAFTSPLAN, K.WIRTSCHAFTSPLAN),
    (T.HAUSGELDABRECHNUNG, K.HAUSGELDABRECHNUNG),
]


def _classified(type_, subtype):
    return parse_classification(
        json.dumps(
            {
                "type": type_.value,
                "subtype": subtype.value if subtype is not None else None,
                "contains_tenant_data": False,
                "language": "de",
                "confidence": "high",
            }
        )
    )


@pytest.mark.parametrize(("type_", "reader"), DEDICATED)
@pytest.mark.parametrize(
    "subtype",
    [S.EMPLOYER_CERTIFICATE, S.LOAN_STATEMENT, S.WEG_MINUTES, S.DEPOT_REPORT, S.UTILITY_BILL],
)
def test_a_dedicated_type_drops_a_catalogue_subtype(type_, reader, subtype):
    classification = _classified(type_, subtype)
    assert classification.type is type_
    assert classification.subtype is None
    assert extraction_kind(classification.type, classification.subtype) is reader


def test_the_first_recordings_misread_is_read_as_a_wage_certificate():
    classification = _classified(T.LOHNSTEUERBESCHEINIGUNG, S.EMPLOYER_CERTIFICATE)
    assert classification.subtype is None
    assert extraction_kind(classification.type, None) is K.LOHNSTEUERBESCHEINIGUNG


@pytest.mark.parametrize(("type_", "reader"), DEDICATED)
@pytest.mark.parametrize("subtype", sorted(TENANCY_GROUP))
def test_a_tenancy_subtype_is_kept_on_a_dedicated_type(type_, reader, subtype):
    """A tenant's payslip stays a tenant document: the subtype is the tenant gate's signal,
    and read as the owners' payslip it would propose an owner's pay."""
    classification = _classified(type_, subtype)
    assert classification.subtype is subtype
    assert subtype in TENANT_SUBTYPES
    assert extraction_kind(classification.type, subtype) is K.GENERAL


@pytest.mark.parametrize(("type_", "reader"), DEDICATED)
@pytest.mark.parametrize("subtype", sorted({row[0] for row in EXISTING}))
def test_the_subtypes_before_the_catalogue_are_kept(type_, reader, subtype):
    assert _classified(type_, subtype).subtype is subtype


@pytest.mark.parametrize(
    ("type_", "subtype", "reader"),
    [
        (T.STATEMENT, S.DEPOT_STATEMENT, K.GENERAL),
        (T.STATEMENT, S.LOAN_STATEMENT, K.GENERAL),
        (T.BESCHEID, S.PREPAYMENT_NOTICE, K.GENERAL),
        (T.INVOICE, S.CHILDCARE_INVOICE, K.GENERAL),
        (T.LETTER, S.RENT_INCREASE, K.GENERAL),
        (T.CONTRACT, S.MANAGEMENT_CONTRACT, K.MANAGEMENT_CONTRACT),
        (T.OTHER, S.ENERGY_CERTIFICATE, K.GENERAL),
    ],
)
def test_a_catalogue_subtype_still_overrides_the_other_types(type_, subtype, reader):
    classification = _classified(type_, subtype)
    assert classification.subtype is subtype
    assert extraction_kind(classification.type, classification.subtype) is reader


def test_the_catalogue_is_the_forty_new_subtypes_and_the_financing_bill():
    # 40 from D131, financing_invoice from D138, payment_proof from phase 3 D143
    assert len(CATALOGUE_SUBTYPES) == 42
    catalogue = {row[0] for row in CATALOGUE} - {S.EINKOMMENSTEUERBESCHEID}
    assert catalogue | {S.FINANCING_INVOICE} == CATALOGUE_SUBTYPES


# --- the financing bill (D138) ---------------------------------------------------------------


@pytest.mark.parametrize(("subtype", "type_", "retention", "link", "reader"), FINANCING)
def test_a_financing_bill_is_in_the_loan_group_and_kept_as_a_bill(
    subtype, type_, retention, link, reader
):
    assert subtype in LOAN_GROUP
    assert GENERAL_LINK[subtype] == link
    # read by its type's reader, so its cost lines are sorted like any bill's (D135)
    assert extraction_kind(type_, subtype) is reader
    got = document_retention(
        DocumentRetentionInput(
            type=type_,
            subtype=subtype,
            received_on=date(2026, 11, 11),
            tax_year=2026,
            ownership_end_dates=[date(2040, 6, 30)],
            tenancy_end_dates=[date(2030, 6, 30)],
        ),
        RetentionPolicy(),
    )
    assert got.retention_class is retention  # 10 years after the tax year
    assert got.retain_until == date(2036, 12, 31)


def test_the_classifier_never_answers_the_payment_proof():
    """Phase 3 D143: a payment proof is typed by its upload from a bill's to-do only."""
    schema = classify_schema()
    assert S.PAYMENT_PROOF.value not in json.dumps(schema)
    assert S.PAYMENT_PROOF in CODE_ONLY_SUBTYPES
    assert S.PAYMENT_PROOF.value not in load_prompt("classify").text
    for type_ in (T.OTHER, T.STATEMENT, T.LETTER):
        assert _classified(type_, S.PAYMENT_PROOF).subtype is None


def test_the_classifier_never_answers_the_financing_bill():
    schema = classify_schema()
    assert S.FINANCING_INVOICE.value not in json.dumps(schema)
    assert S.FINANCING_INVOICE in CODE_ONLY_SUBTYPES
    assert S.FINANCING_INVOICE.value not in load_prompt("classify").text
    # a model answer of it is dropped, whatever the type
    for type_ in (T.INVOICE, T.BESCHEID, T.LETTER):
        assert _classified(type_, S.FINANCING_INVOICE).subtype is None
