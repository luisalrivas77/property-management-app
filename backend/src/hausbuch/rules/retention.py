"""Retention per document class and for tenant contact data (tests/fixtures/phase1/retention.yaml).

Every period ends on 31 Dec of (base year + N years) (D11): never earlier than "date + N
years", and the same as §147 AO for tax records. A date that cannot be known yet is a hold.
Conservative defaults, not legal advice.
"""

from collections.abc import Sequence
from datetime import date

from hausbuch.schemas.document import (
    DocumentRetentionInput,
    RetentionPolicy,
    RetentionResult,
    TenantRetentionInput,
)
from hausbuch.schemas.enums import DocumentSubtype, RetentionClass, RetentionHold

_CLASS_BY_SUBTYPE: dict[DocumentSubtype, RetentionClass] = {
    DocumentSubtype.PURCHASE_CONTRACT: RetentionClass.OWNERSHIP_RECORDS,
    DocumentSubtype.PRICE_SPLIT_EVIDENCE: RetentionClass.OWNERSHIP_RECORDS,
    DocumentSubtype.APPRAISAL: RetentionClass.OWNERSHIP_RECORDS,
    DocumentSubtype.LOAN_CONTRACT: RetentionClass.OWNERSHIP_RECORDS,
    DocumentSubtype.LEASE: RetentionClass.TENANCY_RECORDS,
    DocumentSubtype.HANDOVER_PROTOCOL: RetentionClass.TENANCY_RECORDS,
    DocumentSubtype.DEPOSIT_RECORD: RetentionClass.TENANCY_RECORDS,
}


def document_retention(input: DocumentRetentionInput, policy: RetentionPolicy) -> RetentionResult:
    """How long a document is kept, or why that cannot be computed yet."""
    if input.type is None:
        # Untyped wins even over a subtype: nothing is ever scheduled for deletion unseen.
        return RetentionResult(retention_class=None, hold=RetentionHold.UNTYPED)
    cls = _CLASS_BY_SUBTYPE.get(input.subtype, RetentionClass.TAX_RECORDS)
    match cls:
        case RetentionClass.OWNERSHIP_RECORDS:
            return _after_latest(
                cls,
                input.ownership_end_dates,
                policy.ownership_records_years,
                RetentionHold.WHILE_OWNED,
            )
        case RetentionClass.TENANCY_RECORDS:
            return _after_latest(
                cls,
                input.tenancy_end_dates,
                policy.tenancy_records_years,
                RetentionHold.TENANCY_ACTIVE,
            )
        case _:
            if input.tax_year is not None:
                base_year = input.tax_year
            elif input.document_date is not None:
                base_year = input.document_date.year
            else:
                base_year = input.received_on.year
            return RetentionResult(
                retention_class=cls, retain_until=_year_end(base_year, policy.tax_records_years)
            )


def tenant_retention(input: TenantRetentionInput, policy: RetentionPolicy) -> RetentionResult:
    """How long a tenant's contact data is kept after their last tenancy ends."""
    return _after_latest(
        RetentionClass.TENANT_CONTACT,
        input.tenancy_end_dates,
        policy.tenant_contact_years,
        RetentionHold.TENANCY_ACTIVE,
    )


def _after_latest(
    cls: RetentionClass,
    end_dates: Sequence[date | None],
    years: int,
    open_hold: RetentionHold,
) -> RetentionResult:
    """N years after the latest end date; held while any is open or when there are none."""
    if not end_dates:
        return RetentionResult(retention_class=cls, hold=RetentionHold.UNLINKED)
    known = [d for d in end_dates if d is not None]
    if len(known) < len(end_dates):
        return RetentionResult(retention_class=cls, hold=open_hold)
    return RetentionResult(retention_class=cls, retain_until=_year_end(max(known).year, years))


def _year_end(base_year: int, years: int) -> date:
    return date(base_year + years, 12, 31)
