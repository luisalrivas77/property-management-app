"""Edge cases for hausbuch.rules.retention beyond retention.yaml."""

from datetime import date

from hausbuch.rules.retention import document_retention
from hausbuch.schemas.document import DocumentRetentionInput, RetentionPolicy, RetentionResult
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, RetentionClass, RetentionHold

POLICY = RetentionPolicy()


def test_untyped_is_held_even_with_a_subtype():
    got = document_retention(
        DocumentRetentionInput(
            type=None,
            subtype=DocumentSubtype.PURCHASE_CONTRACT,
            received_on=date(2026, 11, 5),
            ownership_end_dates=[date(2030, 1, 31)],
        ),
        POLICY,
    )
    assert got == RetentionResult(retention_class=None, hold=RetentionHold.UNTYPED)


def test_loan_contract_for_two_properties_waits_for_the_later_sale():
    got = document_retention(
        DocumentRetentionInput(
            type=DocumentType.CONTRACT,
            subtype=DocumentSubtype.LOAN_CONTRACT,
            received_on=date(2026, 11, 5),
            ownership_end_dates=[date(2031, 12, 31), date(2033, 1, 1)],
        ),
        POLICY,
    )
    assert got == RetentionResult(
        retention_class=RetentionClass.OWNERSHIP_RECORDS, retain_until=date(2043, 12, 31)
    )


def test_one_property_still_owned_holds_the_document():
    got = document_retention(
        DocumentRetentionInput(
            type=DocumentType.CONTRACT,
            subtype=DocumentSubtype.LOAN_CONTRACT,
            received_on=date(2026, 11, 5),
            ownership_end_dates=[date(2031, 12, 31), None],
        ),
        POLICY,
    )
    assert got.hold is RetentionHold.WHILE_OWNED


def test_zero_years_ends_with_the_base_year():
    got = document_retention(
        DocumentRetentionInput(
            type=DocumentType.INVOICE, tax_year=2026, received_on=date(2027, 1, 2)
        ),
        RetentionPolicy(tax_records_years=0),
    )
    assert got.retain_until == date(2026, 12, 31)


def test_grundsteuerbescheid_is_a_tax_record():
    """Spec amendment 3: the new subtype stays a 10-year tax record, even while owned."""
    got = document_retention(
        DocumentRetentionInput(
            type=DocumentType.BESCHEID,
            subtype=DocumentSubtype.GRUNDSTEUERBESCHEID,
            tax_year=2025,
            received_on=date(2026, 11, 6),
            ownership_end_dates=[None],
        ),
        POLICY,
    )
    assert got == RetentionResult(
        retention_class=RetentionClass.TAX_RECORDS, retain_until=date(2035, 12, 31)
    )
