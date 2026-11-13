"""Bank account commands (docs/phases/phase-2.md §4.3, §7, D21).

- The IBAN is stored as mask + keyed HMAC; the plaintext is never stored, returned or logged.
  It is unique per workspace and cannot be changed: a different IBAN is a different account.
- opening_balance on opening_balance_on is the balance anchor every import of the account
  reconciles against. Moving it re-checks those imports (statement.reconciled/unreconciled
  for each one whose result changes).
- Phase 6 (D156, migration 0025): an account has a kind, a purpose and a provider.
  is_owner_account means "one of the household's own accounts, recognised by its IBAN" and is
  true on every account; the rental account is the one of purpose `rental`. Since step 1 a
  private account imports like the rental one, and PATCH changes kind, purpose and provider
  (account.kind_set): a bank account's kind stays a bank kind (its IBAN is its identity), and
  a purpose is refused while the account holds a booking of a category the new purpose does
  not allow (D159), so no booking ever sits on an account whose purpose refuses it.
  create_account takes the bank kinds only until the depot (step 4) and the exchange (step 5)
  are built, and refuses a wallet (phase 7) with account.kind_not_available.
- Phase 6 D157: owner shares per account, append-only (account_ownership): a new full set
  supersedes the current one; Σ = 1 and every owner known (rules.household.ownership). An
  account without shares is the to-do „Eigentümer festlegen“ (Account.ownership_missing).
"""

import hmac
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any
from uuid import UUID

from pydantic import create_model
from sqlalchemy import Connection, Row, and_, insert, select, update
from sqlalchemy.exc import IntegrityError

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import account_ownership as ownership_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs import enqueue
from hausbuch.rules.errors import (
    ACCOUNT_KIND_NOT_AVAILABLE,
    ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT,
    EXCHANGE_PROVIDER_UNKNOWN,
    RuleError,
)
from hausbuch.rules.exchange import EXCHANGE_PROVIDERS
from hausbuch.rules.household import ownership as ownership_rules
from hausbuch.rules.household.taxonomy import allowed_categories
from hausbuch.rules.statements import NO_PAYMENT_IBAN, OPENING_NOT_DERIVABLE
from hausbuch.schemas.bank import (
    Account,
    AccountCreate,
    ConfirmPaymentIbanBody,
    DepotIbanProposal,
    PaymentIbanConfirmed,
    PaymentIbanProposal,
    StatementFormat,
    StatementImport,
)
from hausbuch.schemas.common import Schema
from hausbuch.schemas.enums import (
    BANK_ACCOUNT_KINDS,
    AccountKind,
    AccountPurpose,
    AllocationStatus,
    EntityType,
)
from hausbuch.schemas.events import (
    AccountCreated,
    AccountKindSet,
    AccountOwnershipSet,
    AccountShare,
    EventType,
    Updated,
)
from hausbuch.schemas.household import AccountOwnership, OwnershipShare, SetOwnershipBody
from hausbuch.storage import BlobStore

from .bank import card_pdf, depot_pdf
from .context import CommandContext
from .documents import read_original
from .errors import ConflictError, NotFoundError, RuleViolationError, violated_constraint
from .events import append_event, diff_changes
from .iban import protect_iban, protect_ref
from .lookup import require_row
from .owners import UNSET, Unset
from .rules_learned import disable_rule, enabled_rules
from .statements import derived_opening, get_import, recheck_account

# Every column the Account read shape has; the HMACs are for matching and never leave.
_HMAC_COLUMNS = {"iban_hmac", "external_ref_hmac", "payment_iban_hmac"}
_READ_COLUMNS = [c for c in account_table.c if c.name not in _HMAC_COLUMNS]
_IBAN_TAKEN = "account_workspace_id_iban_hmac_key"
_REF_TAKEN = "account_workspace_id_external_ref_hmac_key"
_ANCHOR_FIELDS = {"opening_balance", "opening_balance_on"}
# Write-only inputs (stored masked + HMAC'd); an update cannot change them.
_WRITE_ONLY = {"iban", "external_ref"}
_NOT_UPDATED = _WRITE_ONLY
# The fields account.kind_set records (D156); every other change is account.updated.
_KIND_FIELDS = ("kind", "purpose", "provider")
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
    """A bank account (by its IBAN) or, since phase 6 step 2b, a credit card (D176): no IBAN,
    always private, its customer number optional (the first statement sets it) and stored
    masked + HMAC'd. Since step 4 a depot (D165 as amended): one per quirion account (the
    Verrechnungskonto and each strategy's Vermögensverwaltungskonto), always private, its
    account number optional (its first cash statement sets it) and its IBAN optional (typed
    here or confirmed from a statement, D156 amended), both masked + HMAC'd. Since step 5a an
    exchange (D178 (7)): Binance or BISON as its provider, always private, no IBAN and no
    number (Binance's User ID is never stored, D178 (2)). A wallet is refused
    (account.kind_not_available) until phase 7."""
    if data.kind is AccountKind.EXCHANGE:
        if data.purpose not in (None, AccountPurpose.PRIVATE):
            raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "an exchange is a private account")
        if data.external_ref is not None:
            raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "an exchange account takes no number")
        if data.provider not in EXCHANGE_PROVIDERS:
            raise RuleError(EXCHANGE_PROVIDER_UNKNOWN, "the exchange is Binance or BISON")
        purpose = AccountPurpose.PRIVATE
        identity = {}
    elif data.kind is AccountKind.DEPOT:
        if data.purpose not in (None, AccountPurpose.PRIVATE):
            raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "a depot is a private account")
        purpose = AccountPurpose.PRIVATE
        ref = protect_ref(data.external_ref, iban_key) if data.external_ref else None
        depot_iban = protect_iban(data.iban, iban_key) if data.iban else None
        identity = {
            "external_ref_masked": ref.masked if ref else None,
            "external_ref_hmac": ref.hmac if ref else None,
            "iban_masked": depot_iban.masked if depot_iban else None,
            "iban_hmac": depot_iban.hmac if depot_iban else None,
        }
    elif data.kind is AccountKind.CREDIT_CARD:
        if data.purpose not in (None, AccountPurpose.PRIVATE):
            raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "a credit card is a private account")
        purpose = AccountPurpose.PRIVATE
        ref = protect_ref(data.external_ref, iban_key) if data.external_ref else None
        identity = {
            "external_ref_masked": ref.masked if ref else None,
            "external_ref_hmac": ref.hmac if ref else None,
        }
    elif data.kind in BANK_ACCOUNT_KINDS and data.iban is not None:
        iban = protect_iban(data.iban, iban_key)
        purpose = data.purpose or (
            AccountPurpose.PRIVATE if data.is_owner_account else AccountPurpose.RENTAL
        )
        identity = {"iban_masked": iban.masked, "iban_hmac": iban.hmac}
    else:
        raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, f"accounts of kind {data.kind} come later")
    try:
        row = ctx.conn.execute(
            insert(account_table)
            .values(
                workspace_id=ctx.workspace_id,
                **data.model_dump(exclude=_WRITE_ONLY | {"purpose"}),
                purpose=purpose,
                **identity,
                created_at=ctx.now,
                updated_at=ctx.now,
            )
            .returning(*_READ_COLUMNS)
        ).one()
    except IntegrityError as exc:
        constraint = violated_constraint(exc)
        if constraint in (_IBAN_TAKEN, _REF_TAKEN):
            raise ConflictError(
                "the workspace already has an account with that IBAN or number",
                constraint=constraint,
            ) from exc
        raise
    created = _to_account(row, [])
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
            external_ref_masked=created.external_ref_masked,
        ),
    )
    # A new private account with an IBAN makes the lines on the other accounts that name it
    # transfers (D162): before, they waited with the model's answer or none.
    if created.is_owner_account or (
        created.purpose is AccountPurpose.PRIVATE and created.iban_masked is not None
    ):
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
    kind: AccountKind | Unset = UNSET,
    purpose: AccountPurpose | Unset = UNSET,
    provider: str | None | Unset = UNSET,
) -> Account:
    """Change only the fields passed. No-op if nothing changes. A moved anchor re-checks
    every import of the account in the same transaction.

    Phase 6 D156: is_owner_account no longer decides the purpose (the flag is true on every
    account since migration 0025), and an account with imports may be any purpose. kind,
    purpose and provider are recorded as account.kind_set with their new values; a new
    purpose or a new own account re-checks the lines waiting in the inbox."""
    requested = {
        name: value
        for name, value in (
            ("label", label),
            ("bank", bank),
            ("opening_balance", opening_balance),
            ("opening_balance_on", opening_balance_on),
            ("is_owner_account", is_owner_account),
            ("kind", kind),
            ("purpose", purpose),
            ("provider", provider),
        )
        if value is not UNSET
    }
    # The account row is the lock every import, declaration and merge of it takes first.
    current = require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)
    shares = _current_shares(ctx.conn, [account_id]).get(account_id, [])
    before = _to_account(current, shares)
    fields = set(requested)
    old = before.model_dump(include=fields)
    validated = _AccountFields.model_validate(
        before.model_dump(include=set(_AccountFields.model_fields)) | requested
    )
    new_values = validated.model_dump(include=fields)
    changes = diff_changes(old, new_values)
    if not changes:
        return before
    if "kind" in changes and (
        before.kind not in BANK_ACCOUNT_KINDS or new_values["kind"] not in BANK_ACCOUNT_KINDS
    ):
        # A bank account is identified by its IBAN, a depot or an exchange by its own number
        # (D156): a kind across that line would be another account.
        raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "a bank account stays a bank account")
    if "purpose" in changes:
        if before.kind is AccountKind.CREDIT_CARD:
            # D176: a credit card is always private, as create_account sets it (review F4).
            raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "a credit card is a private account")
        _require_bookings_fit(ctx.conn, account_id, AccountPurpose(new_values["purpose"]))

    row = ctx.conn.execute(
        update(account_table)
        .where(account_table.c.id == account_id)
        .values(**new_values, updated_at=ctx.now)
        .returning(*_READ_COLUMNS)
    ).one()
    updated = _with_statement_facts(ctx.conn, [_to_account(row, shares)])[0]
    plain = {name: change for name, change in changes.items() if name not in _KIND_FIELDS}
    if plain:
        append_event(ctx, EventType.ACCOUNT_UPDATED, account_id, Updated(changes=plain))
    if set(_KIND_FIELDS) & set(changes):
        append_event(
            ctx,
            EventType.ACCOUNT_KIND_SET,
            account_id,
            AccountKindSet(kind=updated.kind, purpose=updated.purpose, provider=updated.provider),
        )
    if _ANCHOR_FIELDS & set(changes):
        recheck_account(ctx, account_id)
    if "purpose" in changes or ("is_owner_account" in changes and updated.is_owner_account):
        _rematch_waiting(ctx, account_id)
    return updated


def opening_from_closing(ctx: CommandContext, import_id: UUID) -> StatementImport:
    """„Anfangssaldo daraus übernehmen“ (principle 7): set the account's opening balance, and
    for a file that states its opening the anchor day too, to what its earliest import
    implies (statements.derived_opening), through update_account,
    so account.updated is recorded and every import of the account is re-checked. The
    conditions are checked again under the account's lock (every import, declaration and
    merge takes it first); refused with statement.opening_not_derivable when they no longer
    hold (another import reconciled, an earlier import arrived, the closing was corrected)."""
    found = require_row(ctx.conn, import_table, EntityType.STATEMENT, import_id)
    account = require_row(
        ctx.conn, account_table, EntityType.ACCOUNT, found.account_id, for_update=True
    )
    derived = derived_opening(ctx.conn, account, import_id)
    if derived is None:
        raise RuleError(OPENING_NOT_DERIVABLE, "the import no longer implies the opening balance")
    opening, opening_on = derived
    update_account(ctx, account.id, opening_balance=opening, opening_balance_on=opening_on)
    result = get_import(ctx.conn, import_id)
    assert result is not None  # read in this transaction
    return result


def _require_bookings_fit(conn: Connection, account_id: UUID, purpose: AccountPurpose) -> None:
    """D159 for a change of purpose: every live booking of the account's lines must be one
    the new purpose allows, or the change would leave a rental cost on a private account (on
    the Anlage V from an account that is not the property's) or a household code on the
    rental account (on no line and in no to-do)."""
    allowed = {c.value for c in allowed_categories(purpose)}
    booked = set(
        conn.execute(
            select(allocation_table.c.category)
            .distinct()
            .join(transaction_table, transaction_table.c.id == allocation_table.c.transaction_id)
            .where(
                transaction_table.c.account_id == account_id,
                allocation_table.c.status != AllocationStatus.SUPERSEDED.value,
            )
        ).scalars()
    )
    refused = sorted(booked - allowed)
    if refused:
        raise RuleViolationError(
            [ALLOCATION_CATEGORY_NOT_FOR_ACCOUNT],
            f"the account holds bookings a {purpose.value} account does not allow: "
            + ", ".join(refused),
        )


# --- Ownership (D157) ----------------------------------------------------------------------


def set_account_ownership(ctx: CommandContext, account_id: UUID, body: SetOwnershipBody) -> Account:
    """POST /accounts/{id}/ownership: the full new set. Every owner must be one of the
    workspace's (ownership.owner_missing) and the shares sum to exactly 1
    (ownership.sum_not_one). The current rows are superseded, never deleted; the new rows
    take effect on body.effective_from, today when absent. The same set again (shares and
    reasons) is a no-op: no rows, no event."""
    _lock(ctx, account_id)
    owners = ctx.conn.execute(select(owner_table.c.id)).scalars().all()
    ownership_rules.validate(body.shares, owners)
    current = _ownership_rows(ctx.conn, [account_id]).get(account_id, [])
    if _same_set(current, body.shares):
        return _account(ctx.conn, account_id)
    effective_from = body.effective_from or ctx.now.date()
    inserted = ctx.conn.execute(
        insert(ownership_table).returning(ownership_table.c.id, ownership_table.c.owner_id),
        [
            {
                "workspace_id": ctx.workspace_id,
                "account_id": account_id,
                "owner_id": share.owner_id,
                "share_numerator": share.share.numerator,
                "share_denominator": share.share.denominator,
                "reason": _reason(share.reason),
                "effective_from": effective_from,
                "created_at": ctx.now,
            }
            for share in body.shares
        ],
    ).all()
    new_by_owner = {row.owner_id: row.id for row in inserted}
    superseded = ownership_rules.supersede(current, body.shares)
    by_id = {row.id: row for row in current}
    for row_id in superseded:
        # Superseded by the same owner's new row, else by the set's first row (a row of the
        # same account, as the foreign key asks).
        replacement = new_by_owner.get(by_id[row_id].owner_id, inserted[0].id)
        ctx.conn.execute(
            update(ownership_table)
            .where(ownership_table.c.id == row_id)
            .values(superseded_by=replacement)
        )
    append_event(
        ctx,
        EventType.ACCOUNT_OWNERSHIP_SET,
        account_id,
        AccountOwnershipSet(
            effective_from=effective_from,
            shares=[
                AccountShare(
                    owner_id=s.owner_id,
                    share_numerator=s.share.numerator,
                    share_denominator=s.share.denominator,
                )
                for s in body.shares
            ],
            superseded=superseded,
        ),
        effective_date=effective_from,
    )
    return _account(ctx.conn, account_id)


def get_account_ownership(conn: Connection, account_id: UUID) -> list[AccountOwnership]:
    """The current rows (superseded_by null), by owner."""
    return _ownership_rows(conn, [account_id]).get(account_id, [])


def _reason(text: str | None) -> str | None:
    """A blank reason is no reason (the column holds 1-500 characters)."""
    return " ".join((text or "").split()) or None


def _same_set(current: Sequence[AccountOwnership], shares: Sequence[OwnershipShare]) -> bool:
    def key(owner_id: UUID, share: Fraction, reason: str | None) -> tuple[UUID, Fraction, str]:
        return owner_id, share, _reason(reason) or ""

    return {
        key(r.owner_id, Fraction(r.share_numerator, r.share_denominator), r.reason) for r in current
    } == {key(s.owner_id, s.share, s.reason) for s in shares} and len(current) == len(shares)


def _ownership_rows(
    conn: Connection, account_ids: Sequence[UUID]
) -> dict[UUID, list[AccountOwnership]]:
    rows = conn.execute(
        select(ownership_table)
        .where(
            ownership_table.c.account_id.in_(account_ids),
            ownership_table.c.superseded_by.is_(None),
        )
        .order_by(ownership_table.c.account_id, ownership_table.c.owner_id)
    )
    found: dict[UUID, list[AccountOwnership]] = {}
    for row in rows:
        found.setdefault(row.account_id, []).append(AccountOwnership.model_validate(row._mapping))
    return found


def _current_shares(
    conn: Connection, account_ids: Sequence[UUID]
) -> dict[UUID, list[OwnershipShare]]:
    return {
        account_id: [
            OwnershipShare(
                owner_id=r.owner_id,
                share_numerator=r.share_numerator,
                share_denominator=r.share_denominator,
                reason=r.reason,
            )
            for r in rows
        ]
        for account_id, rows in _ownership_rows(conn, account_ids).items()
    }


def _lock(ctx: CommandContext, account_id: UUID) -> Row:
    return require_row(ctx.conn, account_table, EntityType.ACCOUNT, account_id, for_update=True)


def _account(conn: Connection, account_id: UUID) -> Account:
    account = get_account(conn, account_id)
    assert account is not None
    return account


def _rematch_waiting(ctx: CommandContext, account_id: UUID) -> None:
    """Queue rematch_waiting for the lines naming this account's IBAN that wait in the inbox
    (phase-2.md §8.1). Three triggers: a private account created with an IBAN (the lines on the
    other accounts that name it are transfers now, D162), an account becoming an owner account
    (the owner-transfer matcher books its transfers now; an account's IBAN never changes after
    creation), and a credit card's payment IBAN being confirmed (D176 (4): the debits to it
    are transfers now; matching.waiting_lines reads the payment IBAN for a card)."""
    enqueue.enqueue_rematch_waiting(
        ctx.conn, workspace_id=ctx.workspace_id, kind=enqueue.TRIGGER_ACCOUNT, entity_id=account_id
    )


def get_account(conn: Connection, account_id: UUID) -> Account | None:
    row = conn.execute(select(*_READ_COLUMNS).where(account_table.c.id == account_id)).one_or_none()
    if row is None:
        return None
    account = _to_account(row, _current_shares(conn, [account_id]).get(account_id, []))
    return _with_statement_facts(conn, [account])[0]


def list_accounts(conn: Connection) -> list[Account]:
    """Every account of the workspace: the rental accounts first, then the household's
    private ones, each by label; each with its current owner shares (D157)."""
    rows = conn.execute(
        select(*_READ_COLUMNS).order_by(
            account_table.c.purpose != AccountPurpose.RENTAL.value,
            account_table.c.label,
            account_table.c.created_at,
            account_table.c.id,
        )
    ).all()
    shares = _current_shares(conn, [r.id for r in rows])
    return _with_statement_facts(conn, [_to_account(r, shares.get(r.id, [])) for r in rows])


def _to_account(row: Row, shares: list[OwnershipShare]) -> Account:
    values: dict[str, Any] = {k: v for k, v in row._mapping.items() if k not in _HMAC_COLUMNS}
    return Account.model_validate(values | {"ownership": shares, "ownership_missing": not shares})


# --- The credit card (phase 6 D176) ----------------------------------------------------------


def confirm_payment_iban(
    ctx: CommandContext,
    store: BlobStore,
    account_id: UUID,
    body: ConfirmPaymentIbanBody,
    *,
    iban_key: bytes,
) -> PaymentIbanConfirmed:
    """POST /accounts/{id}/payment-iban (D176 (4)): a person confirms the payment IBAN a card
    statement of this account prints. The IBAN is read again from the statement's immutable
    original (principle 6), never from a request, and stored masked + HMAC'd, recorded as
    account.updated. The same IBAN again is a no-op. Refused for an account that is no credit
    card (account.kind_not_available) and for a statement that prints none
    (statement.no_payment_iban); 404 for an import of another account.

    Every enabled learned rule on that IBAN (typically the card bill answered as spending with
    „Künftig immer so zuordnen“ before the card account existed) is switched off
    (rules_learned.disable_rule, rule.disabled): a rule books before the transfer matcher, so
    it would keep the payment to the card as spending; the result counts them. Lines a rule
    booked before stay as they are."""
    account = _lock(ctx, account_id)
    if account.kind != AccountKind.CREDIT_CARD.value:
        raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "only a credit card has a payment IBAN")
    imported = ctx.conn.execute(
        select(import_table.c.document_id, import_table.c.format).where(
            import_table.c.id == body.import_id, import_table.c.account_id == account_id
        )
    ).one_or_none()
    if imported is None:
        raise NotFoundError(EntityType.STATEMENT, body.import_id)
    payment = None
    if imported.format == StatementFormat.CARD_PDF.value:
        data = read_original(ctx.conn, store, imported.document_id)
        payment = card_pdf.parse_card_pdf(data).statement.payment_iban
    if payment is None:
        raise RuleError(NO_PAYMENT_IBAN, "the statement prints no payment IBAN")
    iban = protect_iban(payment, iban_key)
    if account.payment_iban_hmac is not None and hmac.compare_digest(
        account.payment_iban_hmac, iban.hmac
    ):
        return PaymentIbanConfirmed(account=_account(ctx.conn, account_id))
    ctx.conn.execute(
        update(account_table)
        .where(account_table.c.id == account_id)
        .values(payment_iban_masked=iban.masked, payment_iban_hmac=iban.hmac, updated_at=ctx.now)
    )
    append_event(
        ctx,
        EventType.ACCOUNT_UPDATED,
        account_id,
        Updated(
            changes=diff_changes(
                {"payment_iban_masked": account.payment_iban_masked},
                {"payment_iban_masked": iban.masked},
            )
        ),
    )
    named = iban.hmac.hex()
    rules = [r for r in enabled_rules(ctx.conn) if r.matcher.counterparty_iban_hmac == named]
    for rule in rules:
        disable_rule(ctx, rule.id)
    # D176 (4): the debits to this IBAN waiting in Zu prüfen are transfers now.
    _rematch_waiting(ctx, account_id)
    return PaymentIbanConfirmed(account=_account(ctx.conn, account_id), rules_disabled=len(rules))


def _with_card_facts(conn: Connection, accounts: list[Account]) -> list[Account]:
    """A card account's last four digits and its payment IBAN proposal, from its card
    statements' statement.imported events, the latest statement first (by its last booking,
    then when it was imported). The proposal is the latest printed payment IBAN, offered
    while it is not the confirmed one."""
    cards = [a.id for a in accounts if a.kind is AccountKind.CREDIT_CARD]
    if not cards:
        return accounts
    rows = conn.execute(
        select(import_table.c.account_id, import_table.c.id, event_table.c.payload)
        .join(
            event_table,
            and_(
                event_table.c.entity_id == import_table.c.id,
                event_table.c.type == EventType.STATEMENT_IMPORTED.value,
            ),
        )
        .where(
            import_table.c.account_id.in_(cards),
            import_table.c.format == StatementFormat.CARD_PDF.value,
        )
        .order_by(
            import_table.c.last_booking_on.desc().nulls_last(),
            import_table.c.created_at.desc(),
            event_table.c.seq.desc(),
        )
    ).all()
    last4: dict[UUID, str] = {}
    printed: dict[UUID, PaymentIbanProposal] = {}
    for row in rows:
        if (digits := row.payload.get("card_last4")) and row.account_id not in last4:
            last4[row.account_id] = digits
        masked = row.payload.get("payment_iban_masked")
        if masked and row.account_id not in printed:
            printed[row.account_id] = PaymentIbanProposal(
                import_id=row.id, payment_iban_masked=masked
            )
    result = []
    for account in accounts:
        proposal = printed.get(account.id)
        if proposal is not None and proposal.payment_iban_masked == account.payment_iban_masked:
            proposal = None
        result.append(
            account.model_copy(
                update={"card_last4": last4.get(account.id), "payment_iban_proposal": proposal}
            )
        )
    return result


# --- The depot (phase 6 step 4) ----------------------------------------------------------------


def confirm_depot_iban(
    ctx: CommandContext,
    store: BlobStore,
    account_id: UUID,
    body: ConfirmPaymentIbanBody,
    *,
    iban_key: bytes,
) -> PaymentIbanConfirmed:
    """POST /accounts/{id}/iban (D156 amended in step 4): a person confirms the IBAN a depot's
    cash statement prints, the D176 (4) pattern. The IBAN is read again from the statement's
    immutable original (principle 6), never from a request, and stored masked + HMAC'd as the
    depot's iban, recorded as account.updated; it then joins the household's own IBANs, so a
    transfer from a bank account to it is linked (D162). The same IBAN again is a no-op.
    Refused for an account that is no depot (account.kind_not_available) and for a statement
    that prints none (statement.no_payment_iban); 404 for an import of another account; 409
    when another account of the workspace has that IBAN.

    Every enabled learned rule on that IBAN (a transfer to quirion answered as spending before
    the depot was set up) is switched off, as for the card's payment IBAN, and the lines
    waiting in Zu prüfen are matched again."""
    account = _lock(ctx, account_id)
    if account.kind != AccountKind.DEPOT.value:
        raise RuleError(ACCOUNT_KIND_NOT_AVAILABLE, "only a depot confirms a statement's IBAN")
    imported = ctx.conn.execute(
        select(import_table.c.document_id, import_table.c.format).where(
            import_table.c.id == body.import_id, import_table.c.account_id == account_id
        )
    ).one_or_none()
    if imported is None:
        raise NotFoundError(EntityType.STATEMENT, body.import_id)
    printed = None
    if imported.format == StatementFormat.DEPOT_PDF.value:
        data = read_original(ctx.conn, store, imported.document_id)
        printed = depot_pdf.parse_depot_pdf(data).statement.account_iban
    if printed is None:
        raise RuleError(NO_PAYMENT_IBAN, "the statement prints no IBAN")
    iban = protect_iban(printed, iban_key)
    if account.iban_hmac is not None and hmac.compare_digest(account.iban_hmac, iban.hmac):
        return PaymentIbanConfirmed(account=_account(ctx.conn, account_id))
    try:
        with ctx.conn.begin_nested():
            ctx.conn.execute(
                update(account_table)
                .where(account_table.c.id == account_id)
                .values(iban_masked=iban.masked, iban_hmac=iban.hmac, updated_at=ctx.now)
            )
    except IntegrityError as exc:
        if violated_constraint(exc) == _IBAN_TAKEN:
            raise ConflictError(
                "the workspace already has an account with that IBAN", constraint=_IBAN_TAKEN
            ) from exc
        raise
    append_event(
        ctx,
        EventType.ACCOUNT_UPDATED,
        account_id,
        Updated(
            changes=diff_changes({"iban_masked": account.iban_masked}, {"iban_masked": iban.masked})
        ),
    )
    named = iban.hmac.hex()
    rules = [r for r in enabled_rules(ctx.conn) if r.matcher.counterparty_iban_hmac == named]
    for rule in rules:
        disable_rule(ctx, rule.id)
    _rematch_waiting(ctx, account_id)
    return PaymentIbanConfirmed(account=_account(ctx.conn, account_id), rules_disabled=len(rules))


def _with_statement_facts(conn: Connection, accounts: list[Account]) -> list[Account]:
    return _with_depot_facts(conn, _with_card_facts(conn, accounts))


def _with_depot_facts(conn: Connection, accounts: list[Account]) -> list[Account]:
    """A depot's IBAN proposal: the IBAN its latest cash statement prints (by its last
    booking, then when it was imported), from the statement.imported events, offered while it
    is not the confirmed one."""
    depots = [a.id for a in accounts if a.kind is AccountKind.DEPOT]
    if not depots:
        return accounts
    rows = conn.execute(
        select(import_table.c.account_id, import_table.c.id, event_table.c.payload)
        .join(
            event_table,
            and_(
                event_table.c.entity_id == import_table.c.id,
                event_table.c.type == EventType.STATEMENT_IMPORTED.value,
            ),
        )
        .where(
            import_table.c.account_id.in_(depots),
            import_table.c.format == StatementFormat.DEPOT_PDF.value,
        )
        .order_by(
            import_table.c.last_booking_on.desc().nulls_last(),
            import_table.c.created_at.desc(),
            event_table.c.seq.desc(),
        )
    ).all()
    printed: dict[UUID, DepotIbanProposal] = {}
    for row in rows:
        masked = row.payload.get("account_iban_masked")
        if masked and row.account_id not in printed:
            printed[row.account_id] = DepotIbanProposal(import_id=row.id, iban_masked=masked)
    result = []
    for account in accounts:
        proposal = printed.get(account.id)
        if proposal is not None and proposal.iban_masked == account.iban_masked:
            proposal = None
        if proposal is not None:
            account = account.model_copy(update={"iban_proposal": proposal})
        result.append(account)
    return result
