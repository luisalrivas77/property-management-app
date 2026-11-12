"""The FinTS sync of an account (docs/phases/phase-5.md §4, §10, §11; D115-D120).

- POST /accounts/{id}/bank-connection: create the connection (BLZ, https FinTS URL, login).
  409 when the account has one; 422 statement.owner_account for an owner account; 422
  invalid when the body's account_id is not the path's.
- PATCH /accounts/{id}/bank-connection: the switch, the URL or the login. A new URL or login
  drops the stored client state (the next run asks for an approval again).
- GET /accounts/{id}/bank-connection: the login masked, the state only as `state_present`;
  404 when the account has none.
- POST /accounts/{id}/syncs: 202 with the queued (or restarted) run. 422 rule_violation with
  bank.not_configured, bank.rate_limited (a fifth manual start within the hour),
  bank.sync_running or bank.window_invalid.
- GET /accounts/{id}/syncs: the latest runs, newest first; GET /syncs/{id}: one run (the
  page polls it every 2 s while `queued`, `running` or `needs_approval`).
- GET /accounts/{id}/bank-statements: the PDF statements the bank delivered (D154), newest
  first by year and number, each with its document id; 404 for an unknown account.

The PIN and the product id are never part of a request or a response (D119).
"""

from uuid import UUID

from sqlalchemy import Connection

from hausbuch.schemas.api import ErrorKind
from hausbuch.schemas.bank_sync import (
    BankConnection,
    BankConnectionCreate,
    BankConnectionPatch,
    BankStatement,
    BankSync,
)
from hausbuch.schemas.enums import EntityType
from hausbuch.services.accounts import get_account
from hausbuch.services.bank_sync import (
    create_connection,
    get_connection,
    get_sync,
    list_statements,
    list_syncs,
    start_sync,
    update_connection,
)

from ..deps import Cmd, SettingsDep, domain_router, found, require
from ..errors import ApiError

router = domain_router()


@router.post("/accounts/{account_id}/bank-connection", status_code=201)
def bank_connection_create(
    account_id: UUID, body: BankConnectionCreate, cmd: Cmd
) -> BankConnection:
    if body.account_id != account_id:
        raise ApiError(422, ErrorKind.INVALID, "account_id must be the path's account")
    return cmd.run(create_connection, body)


@router.patch("/accounts/{account_id}/bank-connection")
def bank_connection_update(account_id: UUID, body: BankConnectionPatch, cmd: Cmd) -> BankConnection:
    return cmd.run(update_connection, account_id, body)


def _connection(conn: Connection, account_id: UUID) -> BankConnection | None:
    require(conn, get_account, EntityType.ACCOUNT, account_id)
    return get_connection(conn, account_id)


@router.get("/accounts/{account_id}/bank-connection")
def bank_connection_get(account_id: UUID, cmd: Cmd) -> BankConnection:
    return found(cmd.read(_connection, account_id), "bank_connection", account_id)


@router.post("/accounts/{account_id}/syncs", status_code=202)
def sync_start(account_id: UUID, cmd: Cmd, settings: SettingsDep) -> BankSync:
    return cmd.run(start_sync, account_id, settings=settings)


def _syncs(conn: Connection, account_id: UUID) -> list[BankSync]:
    require(conn, get_account, EntityType.ACCOUNT, account_id)
    return list_syncs(conn, account_id)


@router.get("/accounts/{account_id}/syncs")
def sync_list(account_id: UUID, cmd: Cmd) -> list[BankSync]:
    return cmd.read(_syncs, account_id)


@router.get("/syncs/{sync_id}")
def sync_get(sync_id: UUID, cmd: Cmd) -> BankSync:
    return found(cmd.read(get_sync, sync_id), "bank_sync", sync_id)


def _statements(conn: Connection, account_id: UUID) -> list[BankStatement]:
    require(conn, get_account, EntityType.ACCOUNT, account_id)
    return list_statements(conn, account_id)


@router.get("/accounts/{account_id}/bank-statements")
def bank_statement_list(account_id: UUID, cmd: Cmd) -> list[BankStatement]:
    return cmd.read(_statements, account_id)
