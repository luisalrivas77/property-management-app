"""Loading a row that must exist in the current workspace, or raising NotFoundError."""

from uuid import UUID

from sqlalchemy import Connection, Row, Table, select

from hausbuch.db.tables import document as document_table
from hausbuch.schemas.enums import EntityType

from .errors import NotFoundError


def require_row(
    conn: Connection,
    table: Table,
    entity_type: EntityType | str,
    row_id: UUID,
    *,
    for_update: bool = False,
) -> Row:
    """The row with this id, locked FOR UPDATE if asked. RLS hides other workspaces' rows."""
    query = select(table).where(table.c.id == row_id)
    if for_update:
        query = query.with_for_update()
    row = conn.execute(query).one_or_none()
    if row is None:
        raise NotFoundError(entity_type, row_id)
    return row


def require_document(conn: Connection, document_id: UUID | None) -> None:
    """A referenced document (evidence, invoice) must exist; None means no document."""
    if document_id is not None:
        require_row(conn, document_table, EntityType.DOCUMENT, document_id)
