"""Tenant commands. Tenants hold personal data, so (D5):

- No personal data in events: tenant.updated names the changed fields, never their values.
- The IBAN is stored as mask + keyed HMAC; the plaintext is never stored, returned or logged.
- Erasure nulls the personal columns in place and sets erased_at; the PII-free events stay.
  It respects retention holds (end of the last tenancy + 4 years by default).
- Tenant events carry no History scope: a tenant may rent in several properties, or none.

Validation errors never repeat the submitted values, so personal data stays out of logs.
"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Connection, Row, insert, select, update

from hausbuch.db.tables import tenancy as tenancy_table
from hausbuch.db.tables import tenant as tenant_table
from hausbuch.rules.retention import tenant_retention
from hausbuch.schemas.document import RetentionPolicy, RetentionResult, TenantRetentionInput
from hausbuch.schemas.enums import EntityType, RetentionClass, RetentionHold
from hausbuch.schemas.events import (
    Empty,
    ErasureBasis,
    EventType,
    TenantErased,
    TenantField,
    TenantUpdated,
)
from hausbuch.schemas.tenancy import Tenant, TenantCreate

from .context import CommandContext
from .erasure_scrub import scrub_for_erasure
from .errors import ConflictError, NotFoundError, RetentionHoldError
from .events import append_event
from .iban import protect_iban
from .lookup import require_row
from .owners import UNSET, Unset
from .parties import tenancies_ever_of

# Every column the Tenant read shape has; iban_hmac is for matching and never leaves.
_READ_COLUMNS = [c for c in tenant_table.c if c.name != "iban_hmac"]
_CONTACT_FIELDS = ("full_name", "email", "phone")
DEFAULT_RETENTION_POLICY = RetentionPolicy()  # frozen, so one shared instance is safe


def create_tenant(ctx: CommandContext, data: TenantCreate, *, iban_key: bytes) -> Tenant:
    iban = None if data.iban is None else protect_iban(data.iban, iban_key)
    row = ctx.conn.execute(
        insert(tenant_table)
        .values(
            workspace_id=ctx.workspace_id,
            **data.model_dump(include=set(_CONTACT_FIELDS)),
            iban_masked=None if iban is None else iban.masked,
            iban_hmac=None if iban is None else iban.hmac,
            created_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*_READ_COLUMNS)
    ).one()
    created = _to_tenant(row)
    append_event(ctx, EventType.TENANT_CREATED, created.id, Empty())
    return created


def update_tenant(
    ctx: CommandContext,
    tenant_id: UUID,
    *,
    full_name: str | Unset = UNSET,
    email: str | None | Unset = UNSET,
    phone: str | None | Unset = UNSET,
    iban: str | None | Unset = UNSET,
    iban_key: bytes | None = None,
) -> Tenant:
    """Change only the fields passed (None clears email, phone or iban). The event lists the
    changed field names only. No-op if nothing changes."""
    contact = {
        name: value
        for name, value in (("full_name", full_name), ("email", email), ("phone", phone))
        if value is not UNSET
    }
    _validate_contact(contact)
    new_iban = None
    if iban is not UNSET and iban is not None:
        if iban_key is None:
            raise ValueError("iban_key is required to change a tenant's IBAN")
        new_iban = protect_iban(iban, iban_key)

    current = require_row(ctx.conn, tenant_table, EntityType.TENANT, tenant_id, for_update=True)
    if current.erased_at is not None:
        raise ConflictError(f"tenant {tenant_id} was erased")

    values = {name: value for name, value in contact.items() if getattr(current, name) != value}
    if iban is not UNSET:
        new_hmac = None if new_iban is None else new_iban.hmac
        if new_hmac != current.iban_hmac:
            values["iban_masked"] = None if new_iban is None else new_iban.masked
            values["iban_hmac"] = new_hmac
    if not values:
        return _to_tenant(current)

    row = ctx.conn.execute(
        update(tenant_table)
        .where(tenant_table.c.id == tenant_id)
        .values(**values, updated_at=ctx.now)
        .returning(*_READ_COLUMNS)
    ).one()
    changed = [TenantField(name) for name in _CONTACT_FIELDS if name in values]
    if "iban_hmac" in values:
        changed.append(TenantField.IBAN)
    append_event(ctx, EventType.TENANT_UPDATED, tenant_id, TenantUpdated(changed_fields=changed))
    return _to_tenant(row)


def erase_tenant(
    ctx: CommandContext,
    tenant_id: UUID,
    *,
    on_request: bool = False,
    policy: RetentionPolicy = DEFAULT_RETENTION_POLICY,
) -> Tenant:
    """Null every personal column and set erased_at (GDPR erasure). Idempotent.

    Deletion respects retention holds (ARCHITECTURE.md, privacy):
    - Always refused while any of the tenant's tenancies runs: the data performs the lease,
      so there is no right to erasure yet. "The tenant's tenancies" are every tenancy they
      were ever a party of, including one they were later removed from (set_parties).
    - Otherwise refused until retain_until has passed (end of tenancy + 4 years by default),
      unless on_request: the tenant asked and the landlord chose to honour it. Keeping data
      for possible legal claims is allowed but not required, so that choice is the landlord's.
    - A tenant with no tenancy (an applicant, hold "unlinked") may always be erased.

    The tenant.erased event records the basis and the scrub counts, never any erased value.
    Documents linked to the tenant (e.g. the lease) keep their own retention and are not
    touched here; what the AI layer derived from them is scrubbed in the same transaction
    (erasure_scrub, phase-2.md D42 (3)). A refused erasure scrubs nothing.
    """
    current = require_row(ctx.conn, tenant_table, EntityType.TENANT, tenant_id, for_update=True)
    if current.erased_at is not None:
        return _to_tenant(current)
    # The FOR UPDATE above waits for any create_tenancy holding this tenant FOR KEY SHARE, so
    # a tenancy committed just before is seen here.
    # Every tenancy they were ever a party of, removed ones included (parties.py).
    tenancy_ids = tenancies_ever_of(ctx.conn, [tenant_id])
    end_dates = list(
        ctx.conn.execute(
            select(tenancy_table.c.end_date).where(tenancy_table.c.id.in_(tenancy_ids))
        ).scalars()
    )
    # "Running" is decided here, not by the retention hold: the retention rule treats a
    # recorded future end date as a known end (retention.yaml), but the tenant still lives in
    # the flat until then. The end date itself still runs (dates are inclusive).
    today = ctx.now.date()
    if any(end is None or end >= today for end in end_dates):
        raise RetentionHoldError(
            f"tenant {tenant_id} has a running tenancy; their data is retained",
            result=RetentionResult(
                retention_class=RetentionClass.TENANT_CONTACT, hold=RetentionHold.TENANCY_ACTIVE
            ),
        )
    result = tenant_retention(TenantRetentionInput(tenancy_end_dates=end_dates), policy)
    # The basis records what permitted the erasure: a request only when it was needed.
    if result.hold is RetentionHold.UNLINKED:
        basis = ErasureBasis.NO_TENANCY
    elif result.retain_until is not None and result.retain_until < today:
        basis = ErasureBasis.RETENTION_EXPIRED
    elif on_request:
        basis = ErasureBasis.DATA_SUBJECT_REQUEST
    else:
        raise RetentionHoldError(
            f"tenant {tenant_id}'s data is retained until {result.retain_until}", result=result
        )
    # The IBAN hash is read from the locked row before it is nulled below.
    iban_hmac = None if current.iban_hmac is None else bytes(current.iban_hmac)
    scrubbed = scrub_for_erasure(ctx, tenant_id, sorted(tenancy_ids), iban_hmac)
    row = ctx.conn.execute(
        update(tenant_table)
        .where(tenant_table.c.id == tenant_id)
        .values(
            full_name=None,
            email=None,
            phone=None,
            iban_masked=None,
            iban_hmac=None,
            erased_at=ctx.now,
            updated_at=ctx.now,
        )
        .returning(*_READ_COLUMNS)
    ).one()
    erased = TenantErased(
        basis=basis,
        proposals_scrubbed=scrubbed.proposals_scrubbed,
        ai_calls_scrubbed=scrubbed.ai_calls_scrubbed,
        rules_disabled=scrubbed.rules_disabled,
    )
    append_event(ctx, EventType.TENANT_ERASED, tenant_id, erased)
    return _to_tenant(row)


def require_unerased_tenants(conn: Connection, tenant_ids: list[UUID]) -> None:
    """The tenants exist and are not erased, locked FOR KEY SHARE until commit. A concurrent
    erase_tenant (FOR UPDATE on the tenant row) then either waits for this transaction and
    sees what it wrote (a tenancy's retention hold, an applied proposal naming the tenant),
    or locked first, and this read waits and sees erased_at: ConflictError."""
    found = {
        r.id: r
        for r in conn.execute(
            select(tenant_table.c.id, tenant_table.c.erased_at)
            .where(tenant_table.c.id.in_(tenant_ids))
            .with_for_update(read=True, key_share=True)
        )
    }
    for tenant_id in tenant_ids:
        if tenant_id not in found:
            raise NotFoundError(EntityType.TENANT, tenant_id)
        if found[tenant_id].erased_at is not None:
            raise ConflictError(f"tenant {tenant_id} was erased")


def get_tenant(conn: Connection, tenant_id: UUID) -> Tenant | None:
    row = conn.execute(select(*_READ_COLUMNS).where(tenant_table.c.id == tenant_id)).one_or_none()
    return None if row is None else _to_tenant(row)


def list_tenants(conn: Connection) -> list[Tenant]:
    """Every tenant of the workspace, by name; erased ones (no name, erased_at set) last."""
    rows = conn.execute(
        select(*_READ_COLUMNS).order_by(
            tenant_table.c.full_name.asc().nulls_last(),
            tenant_table.c.created_at,
            tenant_table.c.id,
        )
    )
    return [_to_tenant(r) for r in rows]


def _validate_contact(contact: dict[str, str | None]) -> None:
    """Check the contact fields with TenantCreate's rules without echoing their values."""
    probe = {"full_name": "x"} | contact
    try:
        TenantCreate.model_validate(probe)
    except ValidationError as exc:
        fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
        raise ValueError(f"invalid tenant fields: {', '.join(fields)}") from None


def _to_tenant(row: Row) -> Tenant:
    values = {k: v for k, v in row._mapping.items() if k != "iban_hmac"}
    return Tenant.model_validate(values)
