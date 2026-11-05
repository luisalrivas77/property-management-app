"""Errors shared by the service commands."""

from uuid import UUID

from hausbuch.schemas.enums import EntityType


class NotFoundError(LookupError):
    """The entity does not exist in the current workspace (or RLS hides it)."""

    def __init__(self, entity_type: EntityType, entity_id: UUID) -> None:
        super().__init__(f"{entity_type} {entity_id} not found")
        self.entity_type = entity_type
        self.entity_id = entity_id
