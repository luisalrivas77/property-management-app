"""Owners.

Endpoints: docs/phases/phase-1-step-3.md §4.1. Every mutating route is exactly one service
command (Commands.run); reads are one read-only transaction (Commands.read).
"""

from uuid import UUID

from hausbuch.schemas.api import OwnerPatch
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.property import Owner, OwnerCreate
from hausbuch.services.owners import create_owner, get_owner, list_owners, update_owner

from ..deps import Cmd, domain_router, found

router = domain_router()


@router.get("/owners")
def owners_list(cmd: Cmd) -> list[Owner]:
    return cmd.read(list_owners)


@router.post("/owners", status_code=201)
def owner_create(body: OwnerCreate, cmd: Cmd) -> Owner:
    return cmd.run(create_owner, body)


@router.get("/owners/{owner_id}")
def owner_get(owner_id: UUID, cmd: Cmd) -> Owner:
    return found(cmd.read(get_owner, owner_id), EntityType.OWNER, owner_id)


@router.patch("/owners/{owner_id}")
def owner_update(owner_id: UUID, body: OwnerPatch, cmd: Cmd) -> Owner:
    return cmd.run(update_owner, owner_id, **body.changes())
