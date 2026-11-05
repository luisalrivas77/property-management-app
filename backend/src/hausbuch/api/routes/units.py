"""Units.

Endpoints: docs/phases/phase-1-step-3.md §4.3.
"""

from uuid import UUID

from sqlalchemy import Connection

from hausbuch.schemas.api import RelatedDocument, UnitPatch
from hausbuch.schemas.enums import EntityType, LinkTarget
from hausbuch.schemas.property import Unit, UnitCreate
from hausbuch.schemas.tenancy import Tenancy
from hausbuch.services.tenancies import list_tenancies
from hausbuch.services.units import create_unit, get_unit, update_unit

from ..deps import Cmd, domain_router, found, require
from .documents import related_documents

router = domain_router()


@router.post("/units", status_code=201)
def unit_create(body: UnitCreate, cmd: Cmd) -> Unit:
    return cmd.run(create_unit, body)


@router.get("/units/{unit_id}")
def unit_get(unit_id: UUID, cmd: Cmd) -> Unit:
    return found(cmd.read(get_unit, unit_id), EntityType.UNIT, unit_id)


@router.patch("/units/{unit_id}")
def unit_update(unit_id: UUID, body: UnitPatch, cmd: Cmd) -> Unit:
    return cmd.run(update_unit, unit_id, **body.changes())


def _tenancies(conn: Connection, unit_id: UUID) -> list[Tenancy]:
    require(conn, get_unit, EntityType.UNIT, unit_id)
    return list_tenancies(conn, unit_id)


@router.get("/units/{unit_id}/tenancies")
def unit_tenancies(unit_id: UUID, cmd: Cmd) -> list[Tenancy]:
    return cmd.read(_tenancies, unit_id)


@router.get("/units/{unit_id}/documents")
def unit_documents(unit_id: UUID, cmd: Cmd) -> list[RelatedDocument]:
    """The documents linked to the unit or to one of its tenancies, each once with every
    matching link in `via`. Unlike the tenancy, loan and tenant `/documents` reads, this rolls
    up children; tenant links are not."""
    return cmd.read(related_documents(LinkTarget.UNIT, get_unit), unit_id)
