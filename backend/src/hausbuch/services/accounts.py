"""Bank account commands (docs/phases/phase-2.md §4.3, §7, D21).

- The IBAN is stored as mask + keyed HMAC; the plaintext is never stored, returned or logged.
  It is unique per workspace and cannot be changed: a different IBAN is a different account.
- opening_balance on opening_balance_on is the balance anchor every import of the account
  reconciles against. Moving it re-checks those imports (statement.reconciled/unreconciled
  for each one whose result changes).
- An owner account (is_owner_account) only serves to recognize owner transfers by IBAN; it
  never holds imports, so an account with imports cannot become one.
- Phase 6 step 0 (D156, migration 0025): an account has a kind, a purpose and a provider.
  Migration 0025 sets is_owner_account on every account and keys the rental account by
  `purpose = rental`, so every read that meant "the rental account" now reads purpose (the
  same accounts as before; step 1 removes the owner-account guards). create_account takes the
  bank kinds only until the depot (step 4) and the exchange (step 5) are built, and refuses a
  wallet (phase 7) with account.kind_not_available.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import create_model
from sqlalchemy import Connection, Row, exists, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import ACCOUNT_KIND_NOT_AVAILABLE, RuleError
from hausbuch.schemas.bank import Account, AccountCreate
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import BANK_ACCOUNT_KINDS, AccountPurpose, EntityType
from hausbuch.schemas.events import AccountCreated, EventType, Updated

from .context import CommandContext
from .errors import ConflictError, violated_constraint
from .events import append_event, diff_changes
from .iban import protect_iban
from .lookup import require_row
from .owners import UNSET, Unset
from .statements import recheck_account

# Every column the Account read shape has; the HMACs are for matching and never leave.
_HMAC_COLUMNS = {"iban_hmac", "external_ref_hmac"}
_READ_COLUMNS = [c for c in account_table.c if c.name not in _HMAC_COLUMNS]
_IBAN_TAKEN = "account_workspace_id_iban_hmac_key"
_ANCHOR_FIELDS = {"opening_balance", "opening_balance_on"}
# Write-only inputs (stored masked + HMAC'd), and the fields an update cannot change yet
# (phase 6 step 1 adds kind, purpose and provider to PATCH /accounts/{id}).
_WRITE_ONLY = {"iban", "external_ref"}
_NOT_UPDATED = _WRITE_ONLY | {"kind", "purpose", "provider"}
# AccountCreate's rules for the fields an update may change (everything but the IBAN).
_AccountFields = create_model(
    "_AccountFields",
    __base__=Schema,
    **{
        name: (field.annotation, field)
        for name, field in AccountCreate.model_fields.items()
        if name not in _NOT_UPDATED
    },
)


def create_account(ctx: CommandContext, data: AccountCreate, *, iban_key: bytes) -> Account:
    if data.kind not in BANK_ACCOUNT_KINDS or data.iban is None:
        raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, f"accounts of kind {data.kind} come later")
    iban = protect_iban(data.iban, iban_key)
    purpose = data.purpose or (
        AccountPurpose.PRIVATE if data.is_owner_account else AccountPurpose.RENTAL
    )
    try:
        row = ctx.conn.execute(
            insert(account_table)
            .values(
                workspace_id=ctx.workspace_id,
                **data.model_dump(exclude=_WRITE_ONLY | {"purpose"}),
                purpose=purpose,
                iban_masked=iban.masked,
                iban_hmac=iban.hmac,
                created_at=ctx.now,
                updated_at=ctx.now,
            )
            .returning(*_READ_COLUMNS)
        ).one()
    except IntegrityError as exc:
        if violated_constraint(exc) == _IBAN_TAKEN:
            raise ConflictError(
                "the workspace already has an account with that IBAN", constraint=_IBAN_TAKEN
            ) from exc
        raise
    created = _to_account(row)
    append_event(
        ctx,
        EventType.ACCOUNT_CREATED,
        created.id,
        AccountCreated(
            label=created.label,
            bank=created.bank,
            iban_masked=created.iban_masked,
            opening_balance=created.opening_balance,
            opening_balance_on=created.opening_balance_on,
            is_owner_account=created.is_owner_account,
            kind=created.kind,
            purpose=created.purpose,
            provider=created.provider,
        ),
    )
    if created.is_owner_account:
        _rematch_waiting(ctx, created.id)
    return created


def update_account(
    ctx: CommandContext,
    account_id: UUID,
    *,
    label: str | Unset = UNSET,
    bank: str | Unset = UNSET,
    opening_balance: Decimal | Unset = UNSET,
    opening_balance_on: date | Unset = UNSET,
    is_owner_account: bool | Unset = UNSET,
) -> Account:
    """Change only the fields passed. No-op if nothing changes. A moved anchor re-checks
    every import of the account in the same transaction."""
    requested = {
        name: value
        for name, value in (
            ("label", label),
            ("bank", bank),
            ("opening_balance", opening_balance),
            ("opening_balance_on", opening_balance_on),
            ("is_owner_account", is_owner_account),
        )
        if value is not UNSET
    }
    # The account row is the lock every import, declaration and merge of it takes first.
    current = require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)
    fields = set(requested)
    old = _to_account(current).model_dump(include=fields)
    validated = _AccountFields.model_validate(
        _to_account(current).model_dump(include=set(_AccountFields.model_fields)) | requested
    )
    new_values = validated.model_dump(include=fields)
    changes = diff_changes(old, new_values)
    if not changes:
        return _to_account(current)
    if (
        "is_owner_account" in changes
        and new_values["is_owner_account"]
        and _has_imports(ctx.conn, account_id)
    ):
        raise ConflictError("an account with imported statements cannot be an owner account")
    stored = dict(new_values)
    if "is_owner_account" in changes:
        # Until phase 6 step 1 adds purpose to the PATCH, the flag a person sets still decides
        # which reads treat the account as the household's (they read purpose since 0025).
        stored["purpose"] = (
            AccountPurpose.PRIVATE if new_values["is_owner_account"] else AccountPurpose.RENTAL
        ).value

    row = ctx.conn.execute(
        update(account_table)
        .where(account_table.c.id == account_id)
        .values(**stored, updated_at=ctx.now)
        .returning(*_READ_COLUMNS)
    ).one()
    updated = _to_account(row)
    append_event(ctx, EventType.ACCOUNT_UPDATED, account_id, Updated(changes=changes))
    if _ANCHOR_FIELDS & set(changes):
        recheck_account(ctx, account_id)
    if "is_owner_account" in changes and updated.is_owner_account:
        _rematch_waiting(ctx, account_id)
    return updated


def _rematch_waiting(ctx: CommandContext, account_id: UUID) -> None:
    """Queue rematch_waiting: transfers from a new owner account wait in the inbox, and the
    owner-transfer matcher books them now (phase-2.md §8.1). An account's IBAN never changes
    after creation, so only becoming an owner account counts."""
    enqueue.enqueue_rematch_waiting(
        ctx.conn, workspace_id=ctx.workspace_id, kind=enqueue.TRIGGER_ACCOUNT, entity_id=account_id
    )


def get_account(conn: Connection, account_id: UUID) -> Account | None:
    row = conn.execute(select(*_READ_COLUMNS).where(account_table.c.id == account_id)).one_or_none()
    return None if row is None else _to_account(row)


def list_accounts(conn: Connection) -> list[Account]:
    """Every account of the workspace: the rental accounts first, then owner accounts, each
    by label."""
    rows = conn.execute(
        select(*_READ_COLUMNS).order_by(
            account_table.c.purpose != AccountPurpose.RENTAL.value,
            account_table.c.label,
            account_table.c.created_at,
            account_table.c.id,
        )
    )
    return [_to_account(r) for r in rows]


def _has_imports(conn: Connection, account_id: UUID) -> bool:
    return conn.execute(
        select(exists().where(import_table.c.account_id == account_id))
    ).scalar_one()


def _to_account(row: Row) -> Account:
    values: dict[str, Any] = {k: v for k, v in row._mapping.items() if k not in _HMAC_COLUMNS}
    return Account.model_validate(values)
