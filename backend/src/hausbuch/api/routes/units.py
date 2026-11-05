"""Units.

Endpoints: docs/phases/phase-1-step-3.md §4.3.
"""

from uuid import UUID

from sqlalchemy import Connection

from hausbuch.schemas.api import UnitPatch
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.property import Unit, UnitCreate
from hausbuch.schemas.tenancy import Tenancy
from hausbuch.services.tenancies import list_tenancies
from hausbuch.services.units import create_unit, get_unit, update_unit

from ..deps import Cmd, domain_router, found, require

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
