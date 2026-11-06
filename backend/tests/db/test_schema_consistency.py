"""Every Pydantic enum equals the CHECK constraint on the column it is stored in."""

import re
from enum import StrEnum

import pytest
from db.db_support import OWNER

from hausbuch.schemas import enums as E
from hausbuch.schemas.ai import AiJob
from hausbuch.schemas.allocation import AllocationCategory, AllocationMethod, SplitMethod
from hausbuch.schemas.bank import StatementFormat

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


def test_link_targets_match_document_link_columns(connect):
    with connect(OWNER) as conn:
        cols = {
            r[0]
            for r in conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'document_link' AND column_name LIKE '%%\\_id'
        """)
        }
    assert cols - {"id", "workspace_id", "document_id"} == {f"{t}_id" for t in E.LinkTarget}
