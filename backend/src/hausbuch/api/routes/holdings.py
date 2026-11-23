"""Lots, the Krypto tab and the year-end holdings statement (docs/phases/phase-7.md D188,
D190, §13, §14; step 6). services/holdings.py computes everything on read.

GET /holdings/crypto is the Anlagen › Krypto tab; GET /wallets/holdings is D190's statement
per wallet and exchange account; GET /wallets/{id}/lots an account's open lots. `on` is a day
on the Berlin calendar, today by default. Every answer carries masked addresses only.

Registered before `wallets`, so /wallets/holdings is not read as /wallets/{account_id}.
"""

from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query, Response

from hausbuch.schemas.chain import HoldingsStatement
from hausbuch.schemas.holdings import (
    CryptoHoldings,
    CryptoLot,
    HoldingsExport,
    HoldingsExportCreate,
    HoldingsFileFormat,
)
from hausbuch.services import holdings, holdings_export
from hausbuch.services.documents import LOCAL_TIME_ZONE
from hausbuch.services.errors import NotFoundError
from hausbuch.services.todo_texts import english

from ..deps import Cmd, SettingsDep, StoreDep, domain_router
from .documents import content_disposition

router = domain_router()


def _today() -> date:
    return datetime.now(LOCAL_TIME_ZONE).date()


@router.get("/holdings/crypto")
def crypto_holdings(cmd: Cmd, on: date | None = None) -> CryptoHoldings:
    """Every wallet and exchange account with its coins and their open lots (FIFO per account
    and coin), each lot with its day, units, cost and „steuerfrei ab“, or the prices whose
    absence leaves it without a cost („Kurs fehlt“); the four cards of the tab."""
    today = _today()  # D291: a price read on demand values today only
    return english(cmd.read(holdings.crypto_holdings, on or today, today=today))


@router.get("/wallets/holdings")
def wallets_holdings(cmd: Cmd, on: date | None = None) -> HoldingsStatement:
    """D190's statement at the end of the Berlin day `on`: per account its addresses' units,
    its positions with their open lots and value, the spam tokens (never valued) and the
    addresses whose last sync on or before the day did not reconcile."""
    return cmd.read(holdings.statement, on or _today())


@router.get("/wallets/{account_id}/lots")
def wallet_lots(
    account_id: UUID, cmd: Cmd, asset: UUID | None = None, on: date | None = None
) -> list[CryptoLot]:
    """The account's open lots on `on`, of one coin with `asset`, in FIFO order. An account
    with nothing held (or of another workspace) answers an empty list."""
    return cmd.read(holdings.account_lots, account_id, on or _today(), asset)


@router.post("/exports/holdings", status_code=201)
def holdings_export_create(
    body: HoldingsExportCreate, cmd: Cmd, settings: SettingsDep, store: StoreDep
) -> list[HoldingsExport]:
    """„Bestandsnachweis 31.12.<Jahr> erstellen“: one immutable row per wallet and exchange
    account held on 31 Dec of `year`, its PDF and CSV stored, with signed download links. A
    second call writes new rows. 422 holdings_export.year_open before 1 Jan of the next year,
    holdings_export.nothing_held when no such account had a confirmed movement by then."""
    return cmd.run(holdings_export.write_exports, store, body, key=settings.session_key)


@router.get("/exports/holdings")
def holdings_exports_list(
    cmd: Cmd,
    settings: SettingsDep,
    year: Annotated[int | None, Query(ge=2009, le=2100)] = None,
) -> list[HoldingsExport]:
    """The stored statements, newest first, each with download links valid a few minutes."""
    return cmd.read(
        holdings_export.list_exports, key=settings.session_key, now=datetime.now(UTC), year=year
    )


@router.get(
    "/exports/holdings/{export_id}/file",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}, "text/csv": {}},
            "description": "The statement's bytes",
        }
    },
)
def holdings_export_file(
    export_id: UUID,
    cmd: Cmd,
    settings: SettingsDep,
    store: StoreDep,
    format: Annotated[HoldingsFileFormat, Query()],
    expires: Annotated[int, Query()],
    signature: Annotated[str, Query(max_length=128)],
) -> Response:
    """The PDF or CSV, SHA-256 verified, as an attachment. A wrong or expired signature answers
    404, like an export of another workspace."""
    valid = holdings_export.signature_valid(
        settings.session_key,
        cmd.auth.workspace_id,
        export_id,
        format,
        expires=expires,
        signature=signature,
        now=datetime.now(UTC),
    )
    if not valid:
        raise NotFoundError(holdings_export.HOLDINGS_EXPORT, export_id)
    data, media_type, filename = cmd.read(holdings_export.read_file, store, export_id, format)
    return Response(
        content=data,
        headers={
            "Content-Type": media_type,
            "Content-Disposition": content_disposition("attachment", filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
