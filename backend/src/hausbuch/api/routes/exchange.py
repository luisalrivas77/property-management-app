"""An exchange account's exports, balances and answers (docs/phases/phase-6.md D166 as amended,
D178; step 5a). No response or error quotes a cell of an export: a Binance file carries the
account's User ID and the withdrawal addresses, which are never stored (D178 (2))."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import File, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from hausbuch.schemas.household import (
    AnswerMovementBody,
    DeclareHoldingsBody,
    ExchangeImport,
    ExchangeMovementRow,
    ExchangeOverview,
    ExchangeUpload,
    MovementAnswered,
    WithdrawalsConfirmed,
)
from hausbuch.services.context import CommandContext
from hausbuch.services.exchange import imports
from hausbuch.storage import BlobStore

from ..deps import Cmd, SettingsDep, StoreDep, domain_router
from ..limits import too_large
from .documents import upload_media_type

router = domain_router()


def _upload(
    ctx: CommandContext, store: BlobStore, account_id: UUID, data: bytes, **kwargs: Any
) -> tuple[ExchangeUpload, bool]:
    """import_export, and whether it created an import."""
    upload = imports.import_export(ctx, store, account_id, data, **kwargs)
    created = upload.movement_import is not None and upload.movement_import.created_at == ctx.now
    return upload, created


@router.post(
    "/accounts/{account_id}/movement-imports",
    status_code=201,
    responses={
        200: {
            "model": ExchangeUpload,
            "description": "These bytes were imported already, or a withdrawal history",
        }
    },
)
async def movement_import_create(
    account_id: UUID,
    file: Annotated[UploadFile, File()],
    cmd: Cmd,
    store: StoreDep,
    settings: SettingsDep,
    response: Response,
) -> ExchangeUpload:
    """An exchange's export, the CSV or the ZIP holding it, recognised by its header: BISON's
    transaction export, Binance's transaction history (201 with its import; 200 with the
    stored one for the same bytes), or Binance's withdrawal history (200: how many
    withdrawals it completed). 422 rule_violation with exchange.unreadable,
    exchange.binance_spot_history, exchange.account_mismatch, exchange.not_an_exchange,
    or exchange.transactions_first (nothing is stored then);
    413 / 415 as for POST /documents."""
    media_type = upload_media_type(file.content_type, settings.upload_media_types)
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise too_large()
    upload, created = await run_in_threadpool(
        cmd.run,
        _upload,
        store,
        account_id,
        data,
        media_type=media_type,
        original_filename=file.filename or None,
    )
    if not created:
        response.status_code = 200
    return upload


@router.get("/accounts/{account_id}/exchange")
def exchange_overview(account_id: UUID, cmd: Cmd) -> ExchangeOverview:
    """The exchange account's page: positions per coin, imports with their balances, the
    to-dos. 422 exchange.not_an_exchange for another kind of account."""
    return cmd.read(imports.exchange_overview, account_id)


@router.get("/accounts/{account_id}/movements")
def exchange_movements(account_id: UUID, cmd: Cmd) -> list[ExchangeMovementRow]:
    """The exchange account's current movements, newest first."""
    return cmd.read(imports.exchange_movements, account_id)


@router.post("/movement-imports/{import_id}/holdings")
def movement_import_holdings(
    import_id: UUID, body: DeclareHoldingsBody, cmd: Cmd
) -> ExchangeImport:
    """„Bestand laut Börse eingeben“: the balance per coin the exchange shows; may be sent
    again to correct it. 422 exchange.balance_missing, exchange.balance_unknown_asset."""
    return cmd.run(imports.declare_holdings, import_id, body)


@router.post("/movement-imports/{import_id}/withdrawals/confirm")
def movement_import_withdrawals(import_id: UUID, cmd: Cmd) -> WithdrawalsConfirmed:
    """„Alle Auszahlungen gingen an eigene Wallets“ (D178 (4))."""
    return cmd.run(imports.confirm_withdrawals, import_id)


@router.post("/movements/{movement_id}/confirm", status_code=204)
def movement_confirm(movement_id: UUID, cmd: Cmd) -> Response:
    """The proposed movement is right as it stands. 422 exchange.movement_done."""
    cmd.run(imports.confirm_movement, movement_id)
    return Response(status_code=204)


@router.post("/movements/{movement_id}/answer")
def movement_answer(movement_id: UUID, body: AnswerMovementBody, cmd: Cmd) -> MovementAnswered:
    """What the proposed movement really was, and the id of the movement that now stands for
    it. 422 exchange.answer_invalid, exchange.movement_done."""
    return MovementAnswered(movement_id=cmd.run(imports.answer_movement, movement_id, body))
