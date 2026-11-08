"""Where each bank line stands on Konten › Umsätze: Transaction.allocation_status and
allocation_categories, computed on read by statements.transaction_select (one query) from
the line, its import, an open possible duplicate and its current allocation rows (D23), as
hausbuch_app under RLS."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import TENANT_IBAN, World
from sqlalchemy import insert, update

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import possible_duplicate as duplicate_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import ConfirmAllocationsBody
from hausbuch.schemas.bank import TransactionAllocationStatus
from hausbuch.schemas.enums import ImportStatus
from hausbuch.services import matching, review
from hausbuch.services.statements import list_transactions

pytestmark = pytest.mark.db

C = AllocationCategory
S = TransactionAllocationStatus


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _answer(world: World, tx_id, *parts: tuple[str, AllocationCategory]):
    answer = AllocationSet(
        transaction_id=tx_id,
        allocations=[
            AllocationDraft(amount=Decimal(amount), category=category) for amount, category in parts
        ],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )
    return world.cmd(
        review.allocate_transaction,
        tx_id,
        ConfirmAllocationsBody(allocation_set=answer, learn_rule=False),
        iban_key=TEST_IBAN_KEY,
    )


def _second_import(world: World, status: ImportStatus) -> uuid.UUID:
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
                error_code="statement.unreconciled",
                balance_source="file",
                declared_closing=Decimal("0.00"),
                computed_closing=Decimal("-5.00"),
                created_at=world.now,
                updated_at=world.now,
            )
            .returning(import_table.c.id)
        ).scalar_one()


def _listed(world: World):
    lines = world.query(list_transactions, world.account_id)
    return {line.id: (line.allocation_status, line.allocation_categories) for line in lines}


def test_each_line_lists_its_status_and_current_categories(world):
    world.tenancy()
    unmatched = world.line(date(2025, 1, 2), "-17.50", name="Unbekannt GmbH")

    short_rent = world.line(date(2025, 1, 3), "1260.00", iban=TENANT_IBAN, purpose="Miete")
    world.cmd(matching.match_line, short_rent, iban_key=TEST_IBAN_KEY, ai_available=False)

    single = world.line(date(2025, 1, 4), "-50.00", name="Versicherung")
    _answer(world, single, ("-50.00", C.INSURANCE))

    # A correction: the first answer's row is superseded and no longer counts; the new split
    # lists both categories, the larger amount first.
    corrected = world.line(date(2025, 1, 5), "-50.00", name="Handwerker")
    _answer(world, corrected, ("-50.00", C.OTHER_EXPENSE))
    result = _answer(world, corrected, ("-10.00", C.BANK_FEES), ("-40.00", C.REPAIRS))
    assert result.transaction.allocation_status is S.CONFIRMED  # the panel's read shape too
    assert result.transaction.allocation_categories == [C.REPAIRS, C.BANK_FEES]

    first = world.line(date(2025, 1, 6), "-30.00", name="Doppelt")
    merged = world.line(date(2025, 1, 6), "-30.00", name="Doppelt")
    awaiting = world.line(date(2025, 1, 6), "-30.00", name="Doppelt")
    with workspace_transaction(world.engine, world.ws) as conn:
        conn.execute(
            update(transaction_table)
            .where(transaction_table.c.id == merged)
            .values(excluded_as_duplicate_of=first)
        )
        conn.execute(
            insert(duplicate_table).values(
                workspace_id=world.ws,
                transaction_id=first,
                other_transaction_id=awaiting,
                created_at=world.now,
            )
        )

    unreconciled = world.line(
        date(2025, 1, 7),
        "-5.00",
        name="Bank",
        import_id=_second_import(world, ImportStatus.UNRECONCILED),
    )

    listed = _listed(world)
    assert listed[unmatched] == (S.OPEN, [])
    status, categories = listed[short_rent]
    assert status is S.PROPOSED and categories
    assert listed[single] == (S.CONFIRMED, [C.INSURANCE])
    assert listed[corrected] == (S.CONFIRMED, [C.REPAIRS, C.BANK_FEES])
    assert listed[first] == (S.OPEN, [])
    assert listed[merged] == (S.EXCLUDED, [])
    assert listed[awaiting] == (S.DUPLICATE_OPEN, [])
    assert listed[unreconciled] == (S.NOT_RECONCILED, [])
    assert len(listed) == 8


def test_a_split_rent_lists_both_categories_largest_first(world):
    world.tenancy()
    rent = world.line(date(2025, 1, 3), "1280.00", iban=TENANT_IBAN, purpose="Miete 01/2025")
    world.cmd(matching.match_line, rent, iban_key=TEST_IBAN_KEY, ai_available=False)
    assert _listed(world)[rent] == (S.CONFIRMED, [C.RENT_COLD, C.OPERATING_COSTS_PREPAYMENT])
