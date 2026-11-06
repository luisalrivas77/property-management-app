"""Tenants.

Endpoints: docs/phases/phase-1-step-3.md §4.4. Tenants are personal data: the IBAN appears
only in request bodies (every read shape carries iban_masked), and neither it nor a name
ever appears in an error body or a log line (§3.6).
"""

from uuid import UUID

from sqlalchemy import Connection

from hausbuch.schemas.api import ErasureBody, TenantPatch
from hausbuch.schemas.document import Document
from hausbuch.schemas.enums import EntityType, LinkTarget
from hausbuch.schemas.tenancy import Tenancy, Tenant, TenantCreate
from hausbuch.services.tenancies import list_tenancies_of_tenant
from hausbuch.services.tenants import (
    create_tenant,
    erase_tenant,
    get_tenant,
    list_tenants,
    update_tenant,
)

from ..deps import Cmd, SettingsDep, domain_router, found, require
from .documents import linked_documents

router = domain_router()


@router.get("/tenants")
def tenants_list(cmd: Cmd) -> list[Tenant]:
    """Every tenant; erased ones are included, flagged by erased_at."""
    return cmd.read(list_tenants)


@router.post("/tenants", status_code=201)
def tenant_create(body: TenantCreate, cmd: Cmd, settings: SettingsDep) -> Tenant:
    return cmd.run(create_tenant, body, iban_key=settings.iban_hmac_key)


@router.get("/tenants/{tenant_id}")
def tenant_get(tenant_id: UUID, cmd: Cmd) -> Tenant:
    return found(cmd.read(get_tenant, tenant_id), EntityType.TENANT, tenant_id)


@router.patch("/tenants/{tenant_id}")
def tenant_update(tenant_id: UUID, body: TenantPatch, cmd: Cmd, settings: SettingsDep) -> Tenant:
    return cmd.run(update_tenant, tenant_id, **body.changes(), iban_key=settings.iban_hmac_key)


@router.post("/tenants/{tenant_id}/erase")
def tenant_erase(tenant_id: UUID, body: ErasureBody, cmd: Cmd) -> Tenant:
    """409 retention_hold while the tenancy runs or its retention period has not passed."""
    return cmd.run(erase_tenant, tenant_id, on_request=body.on_request)


@router.get("/tenants/{tenant_id}/documents")
def tenant_documents(tenant_id: UUID, cmd: Cmd) -> list[Document]:
    return cmd.read(linked_documents(LinkTarget.TENANT, get_tenant), tenant_id)


def _tenancies(conn: Connection, tenant_id: UUID) -> list[Tenancy]:
    require(conn, get_tenant, EntityType.TENANT, tenant_id)
    return list_tenancies_of_tenant(conn, tenant_id)


@router.get("/tenants/{tenant_id}/tenancies")
def tenant_tenancies(tenant_id: UUID, cmd: Cmd) -> list[Tenancy]:
    """The tenancies the tenant is a party of now (any role), ended ones included, by move-in
    date. A tenant removed from a tenancy is no longer listed for it."""
    return cmd.read(_tenancies, tenant_id)
