"""Every Pydantic enum equals the CHECK constraint on the column it is stored in."""

import re
from enum import StrEnum

import pytest
from db.db_support import OWNER

from hausbuch.schemas import enums as E
from hausbuch.schemas.ai import AiJob
from hausbuch.schemas.allocation import AllocationCategory, AllocationMethod, SplitMethod
from hausbuch.schemas.bank import StatementFormat
from hausbuch.schemas.bank_sync import SyncStatus, SyncTrigger
from hausbuch.schemas.inbound_mail import InboundDecision
from hausbuch.schemas.reminders import (
    NotificationChannel,
    NotificationStatus,
    ReminderAckAction,
    ReminderScope,
)
from hausbuch.schemas.tax import FormStatus, TaxExportKind, TaxInputKind

COLUMNS: dict[tuple[str, str], type[StrEnum]] = {
    ("property", "kind"): E.PropertyKind,
    ("property_acquisition_cost", "kind"): E.AcquisitionCostKind,
    ("property_price_split", "source"): E.PriceSplitSource,
    ("tenancy", "rent_model"): E.RentModel,
    ("tenancy", "deposit_form"): E.DepositForm,
    ("tenancy_tenant", "role"): E.TenancyRole,
    ("rent_schedule", "reason"): E.RentChangeReason,
    ("document", "source"): E.DocumentSource,
    ("document", "type"): E.DocumentType,
    ("document", "subtype"): E.DocumentSubtype,
    ("document_extraction", "source"): E.ExtractionSource,
    ("event", "actor_type"): E.ActorType,
    ("event", "entity_type"): E.EntityType,
    ("auth_invite", "purpose"): E.InvitePurpose,
    # Phase 2
    ("document_extraction", "kind"): E.ExtractionKind,
    ("ai_call", "job"): AiJob,
    ("proposal", "status"): E.ProposalStatus,
    ("proposal", "target_mode"): E.ProposalTargetMode,
    ("account", "source"): E.AccountSource,
    ("statement_import", "format"): StatementFormat,
    ("statement_import", "balance_source"): E.BalanceSource,
    ("statement_import", "status"): E.ImportStatus,
    ("transaction", "currency"): E.Currency,
    ("possible_duplicate", "status"): E.DuplicateStatus,
    ("allocation", "category"): AllocationCategory,
    ("allocation", "method"): AllocationMethod,
    ("allocation", "status"): E.AllocationStatus,
    ("matching_rule", "split_method"): SplitMethod,
    ("matching_rule", "category"): AllocationCategory,
    # Phase 3
    ("tenancy", "operating_costs_terms"): E.OperatingCostsTerms,
    ("tax_input", "kind"): TaxInputKind,
    ("tax_export", "kind"): TaxExportKind,
    ("tax_export", "form_status"): FormStatus,
    # Phase 4
    ("owner", "tax_state"): E.StateCode,
    ("income_input", "kind"): E.IncomeInputKind,
    # Phase 4b
    ("return_input", "kind"): E.ReturnInputKind,
    ("questionnaire_answer", "answer"): E.QuestionnaireAnswerValue,
    # Phase 5
    ("bank_sync", "trigger"): SyncTrigger,
    ("bank_sync", "status"): SyncStatus,
    ("bank_sync", "balance_source"): E.BalanceSource,
    ("inbound_mail", "decision"): InboundDecision,
    ("reminder_ack", "scope_type"): ReminderScope,
    ("reminder_ack", "action"): ReminderAckAction,
    ("notification", "channel"): NotificationChannel,
    ("notification", "status"): NotificationStatus,
}


def _allowed_values(conn, table: str, column: str) -> set[str]:
    defs = [
        r[0]
        for r in conn.execute(
            """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.contype = 'c' AND c.conrelid = %s::regclass AND a.attname = %s
          AND array_length(c.conkey, 1) = 1
    """,
            (table, column),
        )
    ]
    # Postgres renders IN ('a', 'b') as = ANY (ARRAY[...]), and IN ('a') as = 'a'.
    single = re.compile(rf"CHECK \(\({column} = '[^']*'::text\)\)")
    lists = [d for d in defs if "ANY (ARRAY[" in d or single.fullmatch(d)]
    assert len(lists) == 1, f"expected one value-list CHECK on {table}.{column}, got {defs}"
    return set(re.findall(r"'([^']*)'::text", lists[0]))


@pytest.mark.parametrize(("table", "column"), sorted(COLUMNS), ids=lambda v: str(v))
def test_enum_matches_check_constraint(connect, table, column):
    with connect(OWNER) as conn:
        assert _allowed_values(conn, table, column) == {m.value for m in COLUMNS[(table, column)]}


def test_proposal_kind_is_every_extraction_kind_but_manual(connect):
    # A proposal is made from a reading; a manual extraction never is pre-filled, so its CHECK
    # lists every ExtractionKind but `manual`: a new kind (D130's general, management_contract)
    # must reach both constraints.
    with connect(OWNER) as conn:
        allowed = _allowed_values(conn, "proposal", "kind")
    assert allowed == {m.value for m in E.ExtractionKind} - {E.ExtractionKind.MANUAL.value}


def test_link_targets_match_document_link_columns(connect):
    with connect(OWNER) as conn:
        cols = {
            r[0]
            for r in conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'document_link' AND column_name LIKE '%%\\_id'
        """)
        }
    # The document target's column is target_document_id (phase 3 D143, migration 0023):
    # document_id is the link's own document.
    expected = {
        "target_document_id" if t is E.LinkTarget.DOCUMENT else f"{t}_id" for t in E.LinkTarget
    }
    assert cols - {"id", "workspace_id", "document_id"} == expected
