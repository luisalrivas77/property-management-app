"""Statement imports and possible duplicates (docs/phases/phase-2.md §7, §8.3).

The import itself is POST /accounts/{id}/imports (routes/accounts.py).
"""

from uuid import UUID

from hausbuch.schemas.bank import (
    DeclareBalanceBody,
    DuplicateDecisionBody,
    PossibleDuplicate,
    StatementImport,
)
from hausbuch.schemas.enums import DuplicateStatus, EntityType
from hausbuch.services.statements import (
    declare_balances,
    get_import,
    list_possible_duplicates,
    resolve_duplicate,
)

from ..deps import Cmd, domain_router, found

router = domain_router()


@router.get("/imports/{import_id}")
def import_get(import_id: UUID, cmd: Cmd) -> StatementImport:
    return found(cmd.read(get_import, import_id), EntityType.STATEMENT, import_id)


@router.post("/imports/{import_id}/balances")
def import_balances(import_id: UUID, body: DeclareBalanceBody, cmd: Cmd) -> StatementImport:
    """The balance read in online banking, for a file that states none (CSV). Reconciles the
    import; may be sent again to correct it. 422 statement.balance_from_file for a camt
    import, whose balances come from the file."""
    return cmd.run(declare_balances, import_id, body)


@router.get("/possible-duplicates")
def possible_duplicates_list(
    cmd: Cmd, status: DuplicateStatus | None = None
) -> list[PossibleDuplicate]:
    """Newest first; `status=open` for the ones still to decide."""
    return cmd.read(list_possible_duplicates, status=status)


@router.post("/possible-duplicates/{possible_duplicate_id}")
def possible_duplicate_decide(
    possible_duplicate_id: UUID, body: DuplicateDecisionBody, cmd: Cmd
) -> PossibleDuplicate:
    """kept_both, or merged: the later-imported line leaves every balance and the account's
    imports are re-checked. 409 when already decided; 422 statement.duplicate_excludes_both
    or statement.duplicate_accounts_differ."""
    return cmd.run(resolve_duplicate, possible_duplicate_id, body.status)
