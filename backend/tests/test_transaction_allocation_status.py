"""The precedence of Transaction.allocation_status (schemas.bank.TransactionAllocationStatus),
without a database; tests/db/test_services_transaction_status.py checks the query."""

import pytest

from hausbuch.schemas.bank import TransactionAllocationStatus as S
from hausbuch.schemas.enums import ImportStatus
from hausbuch.services.statements import allocation_status

OPEN_LINE = {
    "excluded": False,
    "import_status": ImportStatus.RECONCILED,
    "awaiting_duplicate": False,
    "has_current_rows": False,
    "any_proposed": False,
}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, S.OPEN),
        ({"has_current_rows": True}, S.CONFIRMED),
        ({"has_current_rows": True, "any_proposed": True}, S.PROPOSED),
        ({"awaiting_duplicate": True}, S.DUPLICATE_OPEN),
        ({"import_status": ImportStatus.UNRECONCILED}, S.NOT_RECONCILED),
        ({"import_status": ImportStatus.IMPORTED}, S.NOT_RECONCILED),
        # An import that stops reconciling keeps its rows, but the line cannot be changed.
        ({"import_status": ImportStatus.UNRECONCILED, "has_current_rows": True}, S.NOT_RECONCILED),
        ({"excluded": True, "import_status": ImportStatus.UNRECONCILED}, S.EXCLUDED),
        ({"excluded": True, "has_current_rows": True}, S.EXCLUDED),
    ],
)
def test_the_first_status_that_applies_wins(changes, expected):
    assert allocation_status(**(OPEN_LINE | changes)) is expected
