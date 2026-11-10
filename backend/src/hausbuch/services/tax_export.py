"""Tax exports: the ELSTER field sheet (PDF) and the yearly archive (ZIP) of one property and
tax year (docs/phases/phase-3.md §10, §12, D59, D65, D68).

Exports are snapshots. A draft is computed on every read and never stored, but what was
handed over must stay reproducible: an export stores the draft it was built from (the
`tax_export.draft` JSON), the file's bytes content-addressed in `document_file` under the
workspace's blob prefix, and its `property.anlage_v_exported` event, all in one transaction
(write_export). The file has no `document` row: exports are listed on the Steuern screen, not
under Dokumente (Q14).

The flow:
1. POST /properties/{id}/anlage-v/exports (request_export): computes the draft now, so an
   unknown property is 404 and a year outside the property's years or without a rule set is
   422, exactly as the draft's own route; for an archive it sums the sizes of the originals it
   will carry and refuses one over 100 MiB (`tax_export.too_large`: split by hand). Then it
   queues the job `tax_export` (lock `export:{id}`) with a new export id and returns it.
2. The job (jobs/tax_export.py → write_export) computes the draft again, renders the bytes
   (services/tax_export_files.py), stores the blob, then writes document_file, the tax_export
   row and the event. A second run for the same id finds the row and writes nothing.
3. GET /tax-exports/{id} (export_state): `ready` with a short-lived signed download URL once
   the row exists; before that the job's status: `queued` while it waits or runs, `failed`
   with `tax_export.failed` when it failed, or with `tax_export.too_large` when it ended
   without a row (the only way it does: the archive outgrew the limit after the POST checked
   it). Procrastinate's tables have no row-level security, so the job is looked up by its
   lock and its workspace argument: another workspace's export id is 404 in every state.

Phase 4b (D104) adds one owner's Anlage N sheet: POST /owners/{id}/anlage-n/exports
(request_anlage_n_export) queues the same job with the owner instead of the property, and the
job writes a tax_export row naming the owner (property_id NULL), the AnlageNDraft as `draft`
and an `owner.anlage_n_exported` event. The yearly archive gains each owner's sheet and the
Gesamt card as a page, whenever they can be computed; an Anlage N or estimate that cannot be
computed leaves a line in the README and never fails the Anlage V archive.

The download URL is signed, not public: HMAC-SHA256 under HAUSBUCH_SESSION_KEY (with its own
domain prefix, so the key signs nothing else this way) over workspace, export id and expiry,
valid DOWNLOAD_TTL_S seconds. The file route also requires the session, as every domain route.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID, uuid4

from sqlalchemy import Connection, func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.db.tables import owner as owner_table
from hausbuch.db.tables import property_ as property_table
from hausbuch.db.tables import tax_export as tax_export_table
from hausbuch.jobs.enqueue import TAX_EXPORT_TASK, enqueue_tax_export, export_lock
from hausbuch.rules.errors import (
    TAX_EXPORT_FAILED,
    TAX_EXPORT_KIND_INVALID,
    TAX_EXPORT_TOO_LARGE,
    RuleError,
)
from hausbuch.rules.tax import registry
from hausbuch.rules.tax.draft import draft as compute_draft
from hausbuch.rules.tax.questionnaire import reminders as questionnaire_reminders
from hausbuch.schemas.common import Schema
from hausbuch.schemas.employment import AnlageNDraft, AnlageNRuleSet, QuestionnaireAnswer
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.events import AnlageNExported, AnlageVExported, EventType
from hausbuch.schemas.tax import (
    AnlageVDraft,
    DraftFacts,
    FormStatus,
    RuleSet,
    RuleSetStamp,
    TaxExport,
    TaxExportCreate,
    TaxExportKind,
)
from hausbuch.storage import BlobStore, blob_key

from .allocations import user_id
from .context import CommandContext
from .documents import (
    LOCAL_TIME_ZONE,
    OriginalIntegrityError,
    document_media_type,
    download_filename,
    get_document,
    read_original,
)
from .errors import NotFoundError
from .events import append_event
from .lookup import require_row
from .owners import list_owners
from .properties import list_properties
from .questionnaire import current_answers, questionnaire_progress
from .return_inputs import anlage_n_draft, household_return_estimate
from .tax import _year_checked, draft_facts
from .tax_export_anlage_n import (
    anlage_n_sheet_filename,
    estimate_page_filename,
    questions_open_text,
    render_anlage_n_sheet,
    render_estimate_page,
)
from .tax_export_files import (
    PDF_MEDIA_TYPE,
    ZIP_MEDIA_TYPE,
    ArchiveDocument,
    ExportStamp,
    archive_document_ids,
    archive_filename,
    draft_json,
    finanzamt_copy,
    render_archive,
    render_field_sheet,
    safe_filename,
)

# An archive over 100 MiB is split by hand (D77): it is built in memory in a 512 MB container.
MAX_ARCHIVE_BYTES = 100 << 20
DOWNLOAD_TTL_S = 300
TAX_EXPORT = "tax_export"  # NotFoundError's entity name: the row's events sit on the property
_DOMAIN = b"hausbuch.tax_export.download.v1\x00"  # domain separation under the session key
_ARCHIVE_OVERHEAD = 1 << 20  # sheet, CSVs, README and the ZIP's own headers, generously

ANLAGE_N_DIR = "einkommensteuer"  # the archive's folder for the Anlage N pages (D104)
ExportStatus = Literal["queued", "ready", "failed"]
WriteOutcome = Literal["written", "exists", "too_large"]


class TaxExportQueued(Schema):
    """POST /properties/{id}/anlage-v/exports or /owners/{id}/anlage-n/exports: the id to poll
    GET /tax-exports/{id} with."""

    export_id: UUID


class TaxExportState(Schema):
    """GET /tax-exports/{id}. `export` and `download_url` once ready; the URL is signed and
    valid DOWNLOAD_TTL_S seconds, so a client fetches it right before downloading. An Anlage
    N sheet names its owner, every other export its property (exactly one is set)."""

    id: UUID
    property_id: UUID | None = None
    owner_id: UUID | None = None
    tax_year: int
    kind: TaxExportKind
    status: ExportStatus
    error_code: str | None = None
    download_url: str | None = None
    export: TaxExport | None = None


# --- Building ------------------------------------------------------------------------


@dataclass(frozen=True)
class Built:
    facts: DraftFacts
    draft: AnlageVDraft
    rule_set: RuleSet


def build(conn: Connection, property_id: UUID, *, tax_year: int, today: date) -> Built:
    """The draft as services.tax.anlage_v_draft computes it, with the facts it came from (the
    AfA-Erläuterung prints the price, cost rows and split, which the draft only cites).
    NotFoundError for an unknown property; RuleError tax.year_not_available or tax.no_rule_set
    as the draft's own route."""
    checklist, documents, rule_set = _year_checked(conn, property_id, tax_year, today)
    facts = draft_facts(
        conn,
        property_id,
        tax_year=tax_year,
        today=today,
        checklist=checklist,
        documents=documents,
    )
    return Built(facts=facts, draft=compute_draft(facts, tax_year, rule_set), rule_set=rule_set)


def _originals_size(conn: Connection, document_ids: list[UUID]) -> int:
    if not document_ids:
        return 0
    d, f = document_table.c, file_table.c
    total = conn.execute(
        select(func.coalesce(func.sum(f.size_bytes), 0))
        .select_from(
            document_table.join(
                file_table, (f.workspace_id == d.workspace_id) & (f.sha256 == d.file_sha256)
            )
        )
        .where(d.id.in_(document_ids))
    ).scalar_one()
    return int(total)


def _local_date(moment: datetime) -> date:
    return moment.astimezone(LOCAL_TIME_ZONE).date()


# --- Commands ------------------------------------------------------------------------


def request_export(
    ctx: CommandContext, property_id: UUID, body: TaxExportCreate
) -> TaxExportQueued:
    """Check that the export can be built and queue its job; writes nothing else. The draft
    is computed here only for its checks (and, for an archive, the list of originals). An
    Anlage N sheet is an owner's export: RuleError tax_export.kind_invalid here."""
    if body.kind is TaxExportKind.ANLAGE_N_SHEET:
        raise RuleError(
            TAX_EXPORT_KIND_INVALID,
            "an Anlage N sheet is exported per owner (POST /owners/{id}/anlage-n/exports)",
        )
    built = build(ctx.conn, property_id, tax_year=body.tax_year, today=_local_date(ctx.now))
    if body.kind is TaxExportKind.ARCHIVE:
        size = _originals_size(ctx.conn, archive_document_ids(built.draft, built.facts))
        if size + _ARCHIVE_OVERHEAD > MAX_ARCHIVE_BYTES:
            raise RuleError(
                TAX_EXPORT_TOO_LARGE,
                f"the year's originals take {size} bytes; an archive holds at most "
                f"{MAX_ARCHIVE_BYTES} (split it by hand)",
            )
    export_id = uuid4()
    enqueue_tax_export(
        ctx.conn,
        workspace_id=ctx.workspace_id,
        export_id=export_id,
        property_id=property_id,
        tax_year=body.tax_year,
        kind=body.kind.value,
        created_by=user_id(ctx),
    )
    return TaxExportQueued(export_id=export_id)


def request_anlage_n_export(
    ctx: CommandContext, owner_id: UUID, body: TaxExportCreate
) -> TaxExportQueued:
    """POST /owners/{id}/anlage-n/exports (D104): check that the owner's sheet can be built
    (the draft's own refusals: 404 for an unknown owner, tax.no_rule_set, anlage_n.no_pay)
    and queue its job; writes nothing else. Only kind anlage_n_sheet, else
    tax_export.kind_invalid."""
    if body.kind is not TaxExportKind.ANLAGE_N_SHEET:
        raise RuleError(
            TAX_EXPORT_KIND_INVALID,
            f"an owner's export is the Anlage N sheet, not {body.kind.value}",
        )
    anlage_n_draft(ctx.conn, owner_id, tax_year=body.tax_year)
    export_id = uuid4()
    enqueue_tax_export(
        ctx.conn,
        workspace_id=ctx.workspace_id,
        export_id=export_id,
        owner_id=owner_id,
        tax_year=body.tax_year,
        kind=body.kind.value,
        created_by=user_id(ctx),
    )
    return TaxExportQueued(export_id=export_id)


# --- The Anlage N sheet and the archive's Anlage N pages (phase 4b D104, D110) ------------


@dataclass(frozen=True)
class AnlageNSheet:
    """Everything one owner's sheet prints, computed at one moment."""

    draft: AnlageNDraft
    rule_set: AnlageNRuleSet
    owner_name: str
    reminders: list[str]
    questions_open: int

    def render(self, stamp: ExportStamp) -> bytes:
        return render_anlage_n_sheet(
            self.draft,
            self.rule_set,
            stamp,
            owner_name=self.owner_name,
            reminders=self.reminders,
            questions_open=self.questions_open,
        )


def _owner_reminders(
    answers: list[QuestionnaireAnswer], owner_id: UUID, tax_year: int
) -> list[str]:
    """The "von Hand in ELSTER" sentences for one owner's sheet: the year's reminders of the
    questions answered yes for this owner, the household or a property, never for the other
    owner (D110)."""
    own = [a for a in answers if a.owner_id is None or a.owner_id == owner_id]
    return questionnaire_reminders(own, registry.questionnaire(tax_year))


def _questions_open(conn: Connection, tax_year: int) -> int:
    """The year's unanswered Jahresfragen, every scope (the return is joint)."""
    return len(questionnaire_progress(conn, tax_year).open)


def anlage_n_sheet(conn: Connection, owner_id: UUID, *, tax_year: int) -> AnlageNSheet:
    """One owner's sheet, computed now. The draft's own refusals, as its route: NotFoundError,
    RuleError tax.no_rule_set, anlage_n.no_pay."""
    draft = anlage_n_draft(conn, owner_id, tax_year=tax_year)
    owner = require_row(conn, owner_table, EntityType.OWNER, owner_id)
    return AnlageNSheet(
        draft=draft,
        rule_set=registry.anlage_n(tax_year),
        owner_name=owner.full_name,
        reminders=_owner_reminders(current_answers(conn, tax_year), owner_id, tax_year),
        questions_open=_questions_open(conn, tax_year),
    )


# Why the archive leaves an Anlage N page out (README.txt), by the refusal's code.
_LEFT_OUT = {
    "anlage_n.no_pay": "kein Arbeitslohn erfasst",
    "tax.no_rule_set": "kein Regelwerk für dieses Jahr",
}


def _left_out(exc: Exception) -> str:
    if isinstance(exc, RuleError):
        return _LEFT_OUT.get(exc.code, f"nicht berechenbar, {exc.code}")
    return "nicht gefunden"


@dataclass(frozen=True)
class ArchiveExtras:
    files: list[tuple[str, bytes]]  # (path in the ZIP, bytes)
    banner: list[str]  # README's top lines: the open Jahresfragen (D110)
    notes: list[str]  # README's Einkommensteuer lines: pages left out and why


def archive_extras(
    conn: Connection, stamp: ExportStamp, *, tax_year: int, today: date
) -> ArchiveExtras:
    """The yearly archive's Anlage N pages (D104): each owner's sheet and the Gesamt card as
    a page, each only when it can be computed now; a page left out gets a README line with
    the reason, and the Anlage V archive is built either way. Only the rules' refusals are
    caught: a database error still fails (and retries) the job, it is not a missing page."""
    files: list[tuple[str, bytes]] = []
    banner: list[str] = []
    notes: list[str] = []
    if tax_year not in registry.anlage_n_years():
        notes.append(
            f"Anlage N und Gesamtschätzung: für {tax_year} gibt es kein Regelwerk der Anlage N."
        )
        return ArchiveExtras(files=files, banner=banner, notes=notes)
    try:
        open_count = _questions_open(conn, tax_year)
    except (RuleError, NotFoundError) as exc:
        notes.append(f"Jahresfragen {tax_year}: nicht auswertbar ({_left_out(exc)}).")
        open_count = 0
    if open_count:
        banner.append(questions_open_text(open_count))
    owners = list_owners(conn)
    for owner in owners:
        try:
            sheet = anlage_n_sheet(conn, owner.id, tax_year=tax_year)
        except (RuleError, NotFoundError) as exc:
            notes.append(f"Anlage N {owner.full_name}: nicht enthalten ({_left_out(exc)}).")
            continue
        name = anlage_n_sheet_filename(tax_year, owner.full_name)
        files.append((f"{ANLAGE_N_DIR}/{name}", sheet.render(stamp)))
    try:
        estimate = household_return_estimate(conn, tax_year=tax_year, today=today)
    except (RuleError, NotFoundError) as exc:
        notes.append(f"Gesamtschätzung {tax_year}: nicht enthalten ({_left_out(exc)}).")
    else:
        page = render_estimate_page(
            estimate,
            stamp,
            owner_names={o.id: o.full_name for o in owners},
            property_labels={p.id: p.label for p in list_properties(conn)},
        )
        files.append((f"{ANLAGE_N_DIR}/{estimate_page_filename(tax_year)}", page))
    return ArchiveExtras(files=files, banner=banner, notes=notes)


def _archive_documents(
    conn: Connection, store: BlobStore, document_ids: list[UUID]
) -> list[ArchiveDocument]:
    """The originals, SHA-256 verified, numbered 001… in the order of their file names."""
    found: list[tuple[str, UUID, bytes, str]] = []
    for document_id in document_ids:
        document = get_document(conn, document_id)
        media_type = document_media_type(conn, document_id)
        if document is None or media_type is None:  # gone since the draft read it
            continue
        data = read_original(conn, store, document_id)
        name = safe_filename(download_filename(document, media_type))
        found.append((name, document_id, data, media_type))
    found.sort(key=lambda item: (item[0], str(item[1])))
    return [
        ArchiveDocument(
            document_id=document_id,
            number=f"{n:03d}",
            filename=name,
            data=data,
            finanzamt_copy=finanzamt_copy(data, media_type),
        )
        for n, (name, document_id, data, media_type) in enumerate(found, start=1)
    ]


def write_export(
    ctx: CommandContext,
    store: BlobStore,
    *,
    export_id: UUID,
    property_id: UUID | None,
    owner_id: UUID | None = None,
    tax_year: int,
    kind: TaxExportKind,
    created_by: UUID | None,
) -> WriteOutcome:
    """The job's work, in the caller's one transaction: compute the draft, render the file,
    store the blob, then document_file, the tax_export row and its event (D65). `exists` when
    the row is already there (a second run); `too_large` when the archive came out over
    MAX_ARCHIVE_BYTES, in which case nothing is written. An Anlage N sheet names its owner,
    every other kind its property (D104)."""
    conn = ctx.conn
    if conn.execute(
        select(tax_export_table.c.id).where(tax_export_table.c.id == export_id)
    ).first():
        return "exists"
    stamp = ExportStamp(export_id=export_id, created_at=ctx.now)
    if kind is TaxExportKind.ANLAGE_N_SHEET:
        if owner_id is None or property_id is not None:
            raise RuleError(TAX_EXPORT_KIND_INVALID, "an Anlage N sheet names its owner only")
        sheet = anlage_n_sheet(conn, owner_id, tax_year=tax_year)
        data = sheet.render(stamp)
        sha_bytes, sha_hex = _store_file(ctx, store, data, PDF_MEDIA_TYPE)
        _insert_row(
            ctx,
            export_id=export_id,
            subject={"owner_id": owner_id},
            tax_year=tax_year,
            kind=kind,
            stamp=sheet.draft.form,
            draft=draft_json(sheet.draft),
            sha=sha_bytes,
            size=len(data),
            created_by=created_by,
        )
        append_event(
            ctx,
            EventType.OWNER_ANLAGE_N_EXPORTED,
            owner_id,
            AnlageNExported(
                tax_export_id=export_id,
                tax_year=tax_year,
                kind=kind,
                rule_set_version=sheet.draft.form.version,
                form_year=sheet.draft.form.form_year,
                form_status=sheet.draft.form.form_status,
                file_sha256=sha_hex,
                size_bytes=len(data),
                result=sheet.draft.result,
            ),
        )
        return "written"

    if property_id is None or owner_id is not None:
        raise RuleError(TAX_EXPORT_KIND_INVALID, f"a {kind.value} names its property only")
    today = _local_date(ctx.now)
    built = build(conn, property_id, tax_year=tax_year, today=today)
    documents: list[ArchiveDocument] = []
    if kind is TaxExportKind.ARCHIVE:
        label = require_row(conn, property_table, EntityType.PROPERTY, property_id).label
        documents = _archive_documents(conn, store, archive_document_ids(built.draft, built.facts))
        extras = archive_extras(conn, stamp, tax_year=tax_year, today=today)
        data = render_archive(
            built.draft,
            built.rule_set,
            built.facts,
            stamp,
            property_label=label,
            documents=documents,
            extra_files=extras.files,
            banner=extras.banner,
            notes=extras.notes,
        )
        if len(data) > MAX_ARCHIVE_BYTES:
            return "too_large"
        media_type = ZIP_MEDIA_TYPE
    else:
        data = render_field_sheet(built.draft, built.rule_set, built.facts, stamp)
        media_type = PDF_MEDIA_TYPE

    sha_bytes, sha_hex = _store_file(ctx, store, data, media_type)
    stamp_ = built.draft.rule_set
    _insert_row(
        ctx,
        export_id=export_id,
        subject={"property_id": property_id},
        tax_year=tax_year,
        kind=kind,
        stamp=stamp_,
        draft=draft_json(built.draft),
        sha=sha_bytes,
        size=len(data),
        created_by=created_by,
    )
    append_event(
        ctx,
        EventType.PROPERTY_ANLAGE_V_EXPORTED,
        property_id,
        AnlageVExported(
            tax_export_id=export_id,
            tax_year=tax_year,
            kind=kind,
            rule_set_version=stamp_.version,
            form_year=stamp_.form_year,
            form_status=stamp_.form_status,
            file_sha256=sha_hex,
            size_bytes=len(data),
            result=built.draft.totals.result,
            document_count=len(documents) if kind is TaxExportKind.ARCHIVE else None,
        ),
        property_id=property_id,
    )
    return "written"


def _store_file(
    ctx: CommandContext, store: BlobStore, data: bytes, media_type: str
) -> tuple[bytes, str]:
    """The export's bytes into the blob store, then its document_file row (on conflict
    nothing: the same bytes stored before). Returns the SHA-256 as bytes and hex."""
    digest = hashlib.sha256(data)
    sha_bytes, sha_hex = digest.digest(), digest.hexdigest()
    key = blob_key(ctx.workspace_id, sha_hex)
    # Blob first, rows second (as upload_document), so every committed row points at a blob.
    # A rollback leaves an unreferenced blob that a retry does not reuse (the bytes carry the
    # creation time); accepted as rare and harmless, the retention sweeper's business.
    store.put(key, data, media_type)
    ctx.conn.execute(
        pg_insert(file_table)
        .values(
            workspace_id=ctx.workspace_id,
            sha256=sha_bytes,
            size_bytes=len(data),
            media_type=media_type,
            storage_key=key,
            created_at=ctx.now,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id", "sha256"])
    )
    return sha_bytes, sha_hex


def _insert_row(
    ctx: CommandContext,
    *,
    export_id: UUID,
    subject: dict[str, UUID],
    tax_year: int,
    kind: TaxExportKind,
    stamp: RuleSetStamp,
    draft: dict,
    sha: bytes,
    size: int,
    created_by: UUID | None,
) -> None:
    """The immutable tax_export row; `subject` is {"property_id": …} or {"owner_id": …}."""
    ctx.conn.execute(
        insert(tax_export_table).values(
            id=export_id,
            workspace_id=ctx.workspace_id,
            **subject,
            tax_year=tax_year,
            kind=kind.value,
            rule_set_version=stamp.version,
            form_year=stamp.form_year,
            form_status=stamp.form_status.value,
            draft=draft,
            file_sha256=sha,
            size_bytes=size,
            created_at=ctx.now,
            created_by=created_by,
        )
    )


# --- Reads ---------------------------------------------------------------------------


def _to_export(row) -> TaxExport:
    m = row._mapping
    return TaxExport(
        id=m["id"],
        workspace_id=m["workspace_id"],
        created_at=m["created_at"],
        property_id=m["property_id"],
        owner_id=m["owner_id"],
        tax_year=m["tax_year"],
        kind=TaxExportKind(m["kind"]),
        rule_set_version=m["rule_set_version"],
        form_year=m["form_year"],
        form_status=FormStatus(m["form_status"]),
        file_sha256=bytes(m["file_sha256"]).hex(),
        size_bytes=m["size_bytes"],
        created_by=m["created_by"],
    )


_EXPORT_COLUMNS = [c for c in tax_export_table.c if c.name != "draft"]


def get_export(conn: Connection, export_id: UUID) -> TaxExport | None:
    row = conn.execute(
        select(*_EXPORT_COLUMNS).where(tax_export_table.c.id == export_id)
    ).one_or_none()
    return None if row is None else _to_export(row)


def get_export_draft(conn: Connection, export_id: UUID) -> AnlageVDraft | AnlageNDraft | None:
    """The draft the export was built from, as stored (D59): an AnlageNDraft for an Anlage N
    sheet (D104), else an AnlageVDraft."""
    t = tax_export_table.c
    row = conn.execute(select(t.kind, t.draft).where(t.id == export_id)).one_or_none()
    if row is None:
        return None
    if TaxExportKind(row.kind) is TaxExportKind.ANLAGE_N_SHEET:
        return AnlageNDraft.model_validate(row.draft)
    return AnlageVDraft.model_validate(row.draft)


def _listed(conn: Connection, where, tax_year: int | None) -> list[TaxExport]:
    t = tax_export_table.c
    query = select(*_EXPORT_COLUMNS).where(where)
    if tax_year is not None:
        query = query.where(t.tax_year == tax_year)
    rows = conn.execute(query.order_by(t.created_at.desc(), t.id.desc()))
    return [_to_export(r) for r in rows]


def list_exports(conn: Connection, property_id: UUID, *, tax_year: int | None) -> list[TaxExport]:
    """The property's stored exports, newest first. NotFoundError for an unknown property."""
    require_row(conn, property_table, EntityType.PROPERTY, property_id)
    return _listed(conn, tax_export_table.c.property_id == property_id, tax_year)


def list_owner_exports(
    conn: Connection, owner_id: UUID, *, tax_year: int | None
) -> list[TaxExport]:
    """The owner's stored Anlage N sheets, newest first. NotFoundError for an unknown owner."""
    require_row(conn, owner_table, EntityType.OWNER, owner_id)
    return _listed(conn, tax_export_table.c.owner_id == owner_id, tax_year)


_JOB = text(
    "SELECT status, args FROM procrastinate_jobs"
    " WHERE task_name = :task AND lock = :lock AND args->>'workspace_id' = :workspace"
    " ORDER BY id DESC LIMIT 1"
)


def export_state(
    conn: Connection, export_id: UUID, *, workspace_id: UUID, key: bytes, now: datetime
) -> TaxExportState:
    """GET /tax-exports/{id}: the stored export with a signed URL, else its job's status.
    NotFoundError when neither this workspace's row nor its job exists."""
    export = get_export(conn, export_id)
    if export is not None:
        return TaxExportState(
            id=export.id,
            property_id=export.property_id,
            owner_id=export.owner_id,
            tax_year=export.tax_year,
            kind=export.kind,
            status="ready",
            download_url=download_url(key, workspace_id, export_id, now=now),
            export=export,
        )
    job = conn.execute(
        _JOB,
        {"task": TAX_EXPORT_TASK, "lock": export_lock(export_id), "workspace": str(workspace_id)},
    ).one_or_none()
    if job is None:
        raise NotFoundError(TAX_EXPORT, export_id)
    args = job.args
    status: ExportStatus
    if job.status in ("todo", "doing"):
        status, code = "queued", None
    elif job.status == "succeeded":  # ended without a row: the archive outgrew the limit
        status, code = "failed", TAX_EXPORT_TOO_LARGE
    else:  # failed, cancelled, aborted
        status, code = "failed", TAX_EXPORT_FAILED
    # A job queued before phase 4b has no owner_id argument; an owner's job has property_id "".
    property_arg, owner_arg = args.get("property_id") or "", args.get("owner_id") or ""
    return TaxExportState(
        id=export_id,
        property_id=UUID(property_arg) if property_arg else None,
        owner_id=UUID(owner_arg) if owner_arg else None,
        tax_year=int(args["tax_year"]),
        kind=TaxExportKind(args["kind"]),
        status=status,
        error_code=code,
    )


def export_filename(conn: Connection, export: TaxExport) -> str:
    if export.kind is TaxExportKind.ANLAGE_N_SHEET:
        assert export.owner_id is not None
        name = require_row(conn, owner_table, EntityType.OWNER, export.owner_id).full_name
        return anlage_n_sheet_filename(export.tax_year, name)
    assert export.property_id is not None
    label = require_row(conn, property_table, EntityType.PROPERTY, export.property_id).label
    if export.kind is TaxExportKind.ARCHIVE:
        return archive_filename(export.tax_year, label)
    return safe_filename(f"ausfuellhilfe-anlage-v-{export.tax_year}-{label}.pdf")


def read_export_file(conn: Connection, store: BlobStore, export_id: UUID) -> tuple[bytes, str, str]:
    """The export's bytes (SHA-256 verified), media type and download name. NotFoundError for
    an export this workspace does not have."""
    export = get_export(conn, export_id)
    if export is None:
        raise NotFoundError(TAX_EXPORT, export_id)
    f = file_table.c
    row = conn.execute(
        select(f.sha256, f.size_bytes, f.storage_key, f.media_type).where(
            f.sha256 == bytes.fromhex(export.file_sha256)
        )
    ).one()
    data = store.get(row.storage_key)
    if len(data) != row.size_bytes or hashlib.sha256(data).digest() != row.sha256:
        raise OriginalIntegrityError(
            f"export {export_id} does not match its SHA-256 ({row.storage_key})"
        )
    return data, row.media_type, export_filename(conn, export)


# --- The signed download URL ----------------------------------------------------------


def _signature(key: bytes, workspace_id: UUID, export_id: UUID, expires: int) -> str:
    message = _DOMAIN + f"{workspace_id}|{export_id}|{expires}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def download_url(key: bytes, workspace_id: UUID, export_id: UUID, *, now: datetime) -> str:
    """/api/tax-exports/{id}/file?expires=…&signature=…, valid DOWNLOAD_TTL_S seconds."""
    expires = int(now.timestamp()) + DOWNLOAD_TTL_S
    query = urlencode(
        {"expires": expires, "signature": _signature(key, workspace_id, export_id, expires)}
    )
    return f"/api/tax-exports/{export_id}/file?{query}"


def signature_valid(
    key: bytes,
    workspace_id: UUID,
    export_id: UUID,
    *,
    expires: int,
    signature: str,
    now: datetime,
) -> bool:
    if expires < int(now.timestamp()) or expires > int(now.timestamp()) + DOWNLOAD_TTL_S:
        return False
    if not signature.isascii():  # compare_digest raises on non-ASCII text: a 404, not a 500
        return False
    expected = _signature(key, workspace_id, export_id, expires)
    return hmac.compare_digest(expected, signature)
