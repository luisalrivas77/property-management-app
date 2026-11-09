"""Exports of the Anlage V draft: the ELSTER field sheet (PDF) and the yearly archive (ZIP)
(docs/phases/phase-3.md §10, §12, D59, D65).

POST queues the job and answers 202 with the export id; GET /tax-exports/{id} reports
queued / ready / failed and, once ready, a signed download URL valid for a few minutes; the
list shows the property's stored exports, newest first. services/tax_export.py holds the
logic. Error codes, all 422 with `code`: the draft's `tax.year_not_available` and
`tax.no_rule_set`, and `tax_export.too_large` for an archive whose originals exceed 1 GiB.

The download is an ordinary domain route (the session is required as everywhere), plus the
signature: a link copied out of the page stops working after DOWNLOAD_TTL_S. A wrong or
expired signature answers 404, like an export of another workspace.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query, Response

from hausbuch.schemas.tax import TaxExport, TaxExportCreate
from hausbuch.services.errors import NotFoundError
from hausbuch.services.tax_export import (
    TAX_EXPORT,
    TaxExportQueued,
    TaxExportState,
    export_state,
    list_exports,
    read_export_file,
    request_export,
    signature_valid,
)

from ..deps import Cmd, SettingsDep, StoreDep, domain_router
from .documents import content_disposition

router = domain_router()


@router.post("/properties/{property_id}/anlage-v/exports", status_code=202)
def tax_export_create(property_id: UUID, body: TaxExportCreate, cmd: Cmd) -> TaxExportQueued:
    """Queue a field sheet or an archive of the draft as it is now; poll
    GET /tax-exports/{export_id}. Writes nothing but the job: the export row, its file and
    its event appear together when the job has run."""
    return cmd.run(request_export, property_id, body)


@router.get("/properties/{property_id}/anlage-v/exports")
def tax_exports_list(
    property_id: UUID,
    cmd: Cmd,
    tax_year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[TaxExport]:
    """The property's stored exports (ready ones only), newest first."""
    return cmd.read(list_exports, property_id, tax_year=tax_year)


@router.get("/tax-exports/{export_id}")
def tax_export_get(export_id: UUID, cmd: Cmd, settings: SettingsDep) -> TaxExportState:
    """queued while the job waits or runs; ready with `export` and a signed `download_url`
    (fetch it right before downloading: it expires); failed with `error_code`
    (`tax_export.failed`, or `tax_export.too_large` when the archive outgrew 1 GiB)."""
    return cmd.read(
        export_state,
        export_id,
        workspace_id=cmd.auth.workspace_id,
        key=settings.session_key,
        now=datetime.now(UTC),
    )


@router.get(
    "/tax-exports/{export_id}/file",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}, "application/zip": {}},
            "description": "The export's bytes",
        }
    },
)
def tax_export_file(
    export_id: UUID,
    cmd: Cmd,
    settings: SettingsDep,
    store: StoreDep,
    expires: Annotated[int, Query()],
    signature: Annotated[str, Query(max_length=128)],
) -> Response:
    """The export's bytes, SHA-256 verified, as an attachment."""
    valid = signature_valid(
        settings.session_key,
        cmd.auth.workspace_id,
        export_id,
        expires=expires,
        signature=signature,
        now=datetime.now(UTC),
    )
    if not valid:
        raise NotFoundError(TAX_EXPORT, export_id)
    data, media_type, filename = cmd.read(read_export_file, store, export_id)
    return Response(
        content=data,
        headers={
            "Content-Type": media_type,
            "Content-Disposition": content_disposition("attachment", filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
