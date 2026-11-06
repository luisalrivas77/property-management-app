"""Bank accounts, their statement imports and transactions (docs/phases/phase-2.md §7).

The IBAN appears only in the create body; every read shape carries iban_masked, and no error
body or log line quotes an IBAN, a name or a purpose text.
"""

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import File, Response, UploadFile
from sqlalchemy import Connection
from starlette.concurrency import run_in_threadpool

from hausbuch.schemas.api import AccountPatch
from hausbuch.schemas.bank import Account, AccountCreate, StatementImport, Transaction
from hausbuch.schemas.enums import EntityType
from hausbuch.services.accounts import create_account, get_account, list_accounts, update_account
from hausbuch.services.context import CommandContext
from hausbuch.services.statements import import_statement, list_imports, list_transactions
from hausbuch.storage import BlobStore

from ..deps import Cmd, SettingsDep, StoreDep, domain_router, found, require
from ..limits import too_large
from .documents import upload_media_type

router = domain_router()


@router.get("/accounts")
def accounts_list(cmd: Cmd) -> list[Account]:
    """Rental accounts first, then the owners' own accounts, each by label."""
    return cmd.read(list_accounts)


@router.post("/accounts", status_code=201)
def account_create(body: AccountCreate, cmd: Cmd, settings: SettingsDep) -> Account:
    """409 when the workspace already has an account with this IBAN."""
    return cmd.run(create_account, body, iban_key=settings.iban_hmac_key)


@router.get("/accounts/{account_id}")
def account_get(account_id: UUID, cmd: Cmd) -> Account:
    return found(cmd.read(get_account, account_id), EntityType.ACCOUNT, account_id)


@router.patch("/accounts/{account_id}")
def account_update(account_id: UUID, body: AccountPatch, cmd: Cmd) -> Account:
    """Moving the anchor re-checks every import of the account."""
    return cmd.run(update_account, account_id, **body.changes())


def _import(
    ctx: CommandContext, store: BlobStore, account_id: UUID, data: bytes, **kwargs: Any
) -> tuple[StatementImport, bool]:
    """import_statement, and whether it created the import (the same bytes again return the
    existing one)."""
    imported = import_statement(ctx, store, account_id, data, **kwargs)
    return imported, imported.created_at == ctx.now


@router.post(
    "/accounts/{account_id}/imports",
    status_code=201,
    responses={200: {"model": StatementImport, "description": "These bytes were already imported"}},
)
async def account_import(
    account_id: UUID,
    file: Annotated[UploadFile, File()],
    cmd: Cmd,
    store: StoreDep,
    settings: SettingsDep,
    response: Response,
) -> StatementImport:
    """Import a camt.052/053 file, a ZIP of camt reports or a Sparkasse CSV-CAMT export. The
    format is sniffed from the bytes. 201 with a new import, 200 with the existing one for
    the same bytes; 422 rule_violation with a statement.* code when the file cannot be
    imported (nothing is stored then); 413 / 415 as for POST /documents."""
    media_type = upload_media_type(file.content_type, settings.upload_media_types)
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise too_large()
    imported, created = await run_in_threadpool(
        cmd.run,
        _import,
        store,
        account_id,
        data,
        media_type=media_type,
        iban_key=settings.iban_hmac_key,
        original_filename=file.filename or None,
    )
    if not created:
        response.status_code = 200
    return imported


def _imports(conn: Connection, account_id: UUID) -> list[StatementImport]:
    require(conn, get_account, EntityType.ACCOUNT, account_id)
    return list_imports(conn, account_id)


@router.get("/accounts/{account_id}/imports")
def account_imports(account_id: UUID, cmd: Cmd) -> list[StatementImport]:
    """Newest first."""
    return cmd.read(_imports, account_id)


def _transactions(conn: Connection, account_id: UUID, **filters: Any) -> list[Transaction]:
    require(conn, get_account, EntityType.ACCOUNT, account_id)
    return list_transactions(conn, account_id, **filters)


@router.get("/accounts/{account_id}/transactions")
def account_transactions(
    account_id: UUID,
    cmd: Cmd,
    import_id: UUID | None = None,
    booked_from: date | None = None,
    booked_to: date | None = None,
) -> list[Transaction]:
    """Newest booking day first; excluded duplicates included, flagged by
    excluded_as_duplicate_of. Filters: one import's lines, a booking-date range."""
    return cmd.read(
        _transactions,
        account_id,
        import_id=import_id,
        booked_from=booked_from,
        booked_to=booked_to,
    )
