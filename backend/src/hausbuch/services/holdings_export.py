"""The year-end holdings statement as an immutable snapshot per account (docs/phases/phase-7.md
D190; step 6): „Bestandsnachweis 31.12.<Jahr> erstellen“ on Anlagen › Krypto.

`write_exports` computes `services.holdings.statement` for 31 Dec of the year and, per wallet
and exchange account with a confirmed movement on or before it, renders a PDF (one page per
account: its masked addresses, positions, units, open lots) and a CSV, stores both as
`document_file`s (blob first, as phase 3's exports), then writes the `holdings_export` row with
the statement and its source movement ids, and an `account.holdings_exported` event. A later
export is a new row; the table takes no UPDATE or DELETE (forbid_mutation, 0041).

Readings (step 6, §15.1):
- It runs in the request, not as a job (phase 3's `tax_export` queues one): a statement is a
  few pages of text per account, no originals are bundled, and the files are small.
- Only after the day is over in Berlin (`holdings_export.year_open` before 1 Jan), and only
  when some account held something (`holdings_export.nothing_held`).
- No `document` row for the files (Q-O89, as phase 3 settled it for the archive): the export
  is listed on the Krypto tab, not in the vault.
- Privacy: addresses masked only (the full address is phase 8's Steuerreport's, by click);
  owners are not named; no movement's hash or reference is printed.

The download is signed like phase 3's (an HMAC under the session key, its own domain), valid
DOWNLOAD_TTL_S seconds; the file route also needs the session.
"""

import csv
import hashlib
import hmac
import io
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Context, Decimal
from urllib.parse import urlencode
from uuid import UUID, uuid4
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import Connection, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import document_file as file_table
from hausbuch.db.tables import holdings_export as export_table
from hausbuch.rules.errors import HOLDINGS_EXPORT_NOTHING_HELD, HOLDINGS_EXPORT_YEAR_OPEN, RuleError
from hausbuch.rules.lots import PRECISION
from hausbuch.schemas.chain import AccountHoldings, Lot, LotSource
from hausbuch.schemas.events import EventType, HoldingsExported
from hausbuch.schemas.holdings import HoldingsExport, HoldingsExportCreate, HoldingsFileFormat
from hausbuch.schemas.household import AssetKind, MovementStatus
from hausbuch.schemas.reminders import ReminderCryptoYear
from hausbuch.storage import BlobStore, blob_key

from .allocations import user_id
from .context import CommandContext
from .documents import LOCAL_TIME_ZONE, OriginalIntegrityError
from .errors import NotFoundError
from .events import append_event
from .holdings import CRYPTO_ACCOUNT_KINDS, berlin_day, statement

HOLDINGS_EXPORT = "holdings_export"  # NotFoundError's entity name
DOWNLOAD_TTL_S = 300
_DOMAIN = b"hausbuch.holdings_export.download.v1\x00"  # domain separation under the session key
_MEDIA = {"pdf": "application/pdf", "csv": "text/csv"}  # document_file.media_type: no parameters
KIND_LABEL = {"wallet": "Wallet", "exchange": "Börse"}
NOTE = (
    "Bestand zum Ende des Tages (Berliner Zeit) aus den bestätigten Bewegungen; offene Posten "
    "nach FIFO je Konto und Coin, Haltefrist nach §23 EStG. Ohne Kurs am Erwerbstag bleiben "
    "die Anschaffungskosten offen, nie 0 €. Adressen gekürzt. Eine Planungshilfe, keine "
    "Steuerberatung."
)


# --- Formatting --------------------------------------------------------------------------


_WIDE = Context(prec=PRECISION)


def _money(value: Decimal | None) -> str:
    if value is None:
        return "–"
    text = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{text} €"


def _units(value: Decimal) -> str:
    # At the lots' precision: the default context would round a 30-digit quantity.
    text = f"{value.normalize(_WIDE):f}"
    return text.replace(".", ",")


def _day(value: date | None) -> str:
    return "–" if value is None else value.strftime("%d.%m.%Y")


def _csv_amount(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}".replace(".", ",")


# --- The files ---------------------------------------------------------------------------

_STYLES = getSampleStyleSheet()
_BODY = ParagraphStyle("hx-body", parent=_STYLES["BodyText"], fontSize=8.5, leading=10.5)
_SMALL = ParagraphStyle("hx-small", parent=_BODY, fontSize=7.5, leading=9)
_CELL = ParagraphStyle("hx-cell", parent=_BODY, fontSize=8, leading=9.5)
_NUM = ParagraphStyle("hx-num", parent=_CELL, alignment=TA_RIGHT)
_H1 = ParagraphStyle("hx-h1", parent=_STYLES["Heading1"], fontSize=14, leading=17, spaceAfter=4)
_H2 = ParagraphStyle(
    "hx-h2", parent=_STYLES["Heading2"], fontSize=10.5, leading=12.5, spaceBefore=8, spaceAfter=3
)
_GRID = TableStyle(
    [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8efe9")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
)


def _p(text: str, style: ParagraphStyle = _CELL) -> Paragraph:
    return Paragraph(escape(text), style)


def _table(header: list[str], rows: list[list[str]], numeric: set[int], widths) -> Table:
    data = [[_p(h) for h in header]] + [
        [_p(cell, _NUM if i in numeric else _CELL) for i, cell in enumerate(row)] for row in rows
    ]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(_GRID)
    return table


UNKNOWN_ORIGIN = "Herkunft offen"
NO_SYNC = "kein Abgleich bis zum Stichtag"


def _sync_state(account: AccountHoldings, masked: str) -> str | None:
    """What the statement may say about an address: no sync finished by the day, a sync that
    did not reconcile, or (None) its last sync reconciled."""
    if masked in account.unsynced_addresses:
        return NO_SYNC
    if masked in account.unreconciled_addresses:
        return "nicht abgeglichen"
    return None


def _cost_hint(lot: Lot) -> str:
    """A lot whose origin no movement explains says so, whatever its cost: no price can
    resolve it, and its day is the day it arrived, not a known acquisition day (so no
    „Steuerfrei ab“ either). Only a priceable lot without a cost says „Kurs fehlt“."""
    if lot.source is LotSource.UNKNOWN:
        return UNKNOWN_ORIGIN
    return "" if lot.cost_eur is not None else "Kurs fehlt"


def _lot_rows(lots: list[Lot], label: str) -> list[list[str]]:
    return [
        [
            label,
            _day(lot.acquired_on),
            _units(lot.units_open),
            _money(lot.cost_eur) if lot.cost_eur is not None else _cost_hint(lot),
            UNKNOWN_ORIGIN if lot.source is LotSource.UNKNOWN else _day(lot.tax_free_from),
        ]
        for lot in lots
    ]


def render_pdf(
    account: AccountHoldings,
    as_of: date,
    labels: Mapping[UUID, str],
    *,
    export_id: UUID,
    created_at: datetime,
) -> bytes:
    """One account's statement: its masked addresses, positions and open lots. Deterministic
    for the same inputs (reportlab's invariant mode, standard fonts)."""
    title = f"Bestandsnachweis {_day(as_of)}"
    kind = KIND_LABEL.get(account.kind, account.kind)
    story: list = [
        _p(title, _H1),
        _p(
            f"{kind}: {account.label}" + (f" ({account.provider})" if account.provider else ""),
            _BODY,
        ),
        _p(f"Stand: Ende des {_day(as_of)}, Berliner Zeit", _SMALL),
    ]
    if account.addresses:
        story.append(_p("Adressen", _H2))
        story.append(
            _table(
                ["Adresse", "Chain", "Abgleich"],
                [
                    [
                        a.address_masked,
                        a.chain.value,
                        _sync_state(account, a.address_masked) or "abgeglichen",
                    ]
                    for a in account.addresses
                ],
                set(),
                [60 * mm, 50 * mm, 70 * mm],
            )
        )
    story.append(_p("Bestand", _H2))
    story.append(
        _table(
            ["Coin", "Einheiten", "Kurs", "Kurs vom", "Wert"],
            [
                [
                    labels.get(p.asset_id, p.symbol or p.name),
                    _units(p.units),
                    _money(p.price_eur.quantize(Decimal("0.01"))) if p.price_eur else "–",
                    _day(p.price_on),
                    _money(p.value_eur) if p.value_eur is not None else "kein Kurs",
                ]
                for p in account.positions
            ]
            or [["–", "", "", "", ""]],
            {1, 2, 4},
            [40 * mm, 40 * mm, 30 * mm, 30 * mm, 40 * mm],
        )
    )
    lots = [row for p in account.positions for row in _lot_rows(p.lots, labels.get(p.asset_id, ""))]
    if lots:
        story.append(_p("Offene Posten (FIFO)", _H2))
        story.append(
            _table(
                ["Coin", "Erworben am", "Einheiten", "Anschaffungskosten", "Steuerfrei ab"],
                lots,
                {2, 3},
                [36 * mm, 30 * mm, 40 * mm, 40 * mm, 34 * mm],
            )
        )
    if account.unvalued_tokens:
        story.append(_p("Nicht bewertete Token", _H2))
        story.append(
            _table(
                ["Token", "Einheiten", "Grund"],
                [
                    [labels.get(t.asset_id, ""), _units(t.units), "Spam, nie bewertet"]
                    for t in account.unvalued_tokens
                ],
                {1},
                [70 * mm, 50 * mm, 60 * mm],
            )
        )
    story += [Spacer(1, 6), _p(NOTE, _SMALL)]
    created = created_at.astimezone(LOCAL_TIME_ZONE)
    footer = f"{title} · {account.label} · Export {export_id} · {created:%d.%m.%Y %H:%M}"

    def on_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 10 * mm, footer)
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Seite {doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="Hausbuch",
        creator="Hausbuch",
        subject=title,
        invariant=1,
    ).build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


CSV_HEADER = [
    "Zeile",
    "Coin",
    "Adresse",
    "Einheiten",
    "Kurs EUR",
    "Kurs vom",
    "Wert EUR",
    "Erworben am",
    "Anschaffungskosten EUR",
    "Steuerfrei ab",
    "Hinweis",
]


def render_csv(account: AccountHoldings, labels: Mapping[UUID, str]) -> bytes:
    """One row per address and coin („Adresse“), per position („Bestand“), per open lot
    („Posten“) and per spam token („Nicht bewertet“); semicolons, decimal commas, UTF-8 with
    BOM, as phase 3's CSVs."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    blank = [""] * 7
    for a in account.addresses:
        hint = _sync_state(account, a.address_masked) or ""
        for asset_id, units in a.units_by_asset.items():
            label = labels.get(UUID(asset_id), "")
            writer.writerow(["Adresse", label, a.address_masked, _units(units), *blank[:6], hint])
    for p in account.positions:
        label = labels.get(p.asset_id, "")
        price = "" if p.price_eur is None else f"{p.price_eur:f}".replace(".", ",")
        writer.writerow(
            [
                "Bestand",
                label,
                "",
                _units(p.units),
                price,
                "" if p.price_on is None else p.price_on.isoformat(),
                _csv_amount(p.value_eur),
                "",
                "",
                "",
                "" if p.value_eur is not None else "kein Kurs",
            ]
        )
        for lot in p.lots:
            writer.writerow(
                [
                    "Posten",
                    label,
                    "",
                    _units(lot.units_open),
                    "",
                    "",
                    "",
                    lot.acquired_on.isoformat(),
                    _csv_amount(lot.cost_eur),
                    "" if lot.source is LotSource.UNKNOWN else lot.tax_free_from.isoformat(),
                    _cost_hint(lot),
                ]
            )
    for t in account.unvalued_tokens:
        writer.writerow(
            ["Nicht bewertet", labels.get(t.asset_id, ""), "", _units(t.units), *blank, "Spam"]
        )
    return ("﻿" + out.getvalue()).encode("utf-8")


# --- Commands ----------------------------------------------------------------------------


def _labels(conn: Connection, accounts: list[AccountHoldings]) -> dict[UUID, str]:
    ids = {p.asset_id for a in accounts for p in a.positions}
    ids |= {t.asset_id for a in accounts for t in a.unvalued_tokens}
    ids |= {UUID(k) for a in accounts for ad in a.addresses for k in ad.units_by_asset}
    if not ids:
        return {}
    a = asset_table.c
    rows = conn.execute(select(a.id, a.symbol, a.name).where(a.id.in_(ids))).all()
    return {r.id: r.symbol or r.name for r in rows}


def _store_file(ctx: CommandContext, store: BlobStore, data: bytes, media_type: str) -> bytes:
    """Blob first, then its document_file row (on conflict nothing), as phase 3's exports."""
    digest = hashlib.sha256(data)
    sha_bytes, sha_hex = digest.digest(), digest.hexdigest()
    key = blob_key(ctx.workspace_id, sha_hex)
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
    return sha_bytes


def write_exports(
    ctx: CommandContext, store: BlobStore, body: HoldingsExportCreate, *, key: bytes
) -> list[HoldingsExport]:
    """POST /exports/holdings: one row per wallet and exchange account held on 31 Dec of the
    year, with signed download links."""
    as_of = date(body.year, 12, 31)
    if as_of >= ctx.now.astimezone(LOCAL_TIME_ZONE).date():
        raise RuleError(HOLDINGS_EXPORT_YEAR_OPEN, "31 Dec of that year is not over yet")
    seen = statement(ctx.conn, as_of)
    if not seen.accounts:
        raise RuleError(HOLDINGS_EXPORT_NOTHING_HELD, "no wallet or exchange movement by then")
    labels = _labels(ctx.conn, seen.accounts)
    written: list[UUID] = []
    for account in seen.accounts:
        export_id = uuid4()
        pdf = render_pdf(account, as_of, labels, export_id=export_id, created_at=ctx.now)
        csv_bytes = render_csv(account, labels)
        pdf_sha = _store_file(ctx, store, pdf, _MEDIA["pdf"])
        csv_sha = _store_file(ctx, store, csv_bytes, _MEDIA["csv"])
        ctx.conn.execute(
            insert(export_table).values(
                id=export_id,
                workspace_id=ctx.workspace_id,
                account_id=account.account_id,
                as_of=as_of,
                pdf_sha256=pdf_sha,
                csv_sha256=csv_sha,
                statement={"on": as_of.isoformat(), "account": account.model_dump(mode="json")},
                created_at=ctx.now,
                created_by=user_id(ctx),
            )
        )
        append_event(
            ctx,
            EventType.ACCOUNT_HOLDINGS_EXPORTED,
            account.account_id,
            HoldingsExported(
                holdings_export_id=export_id,
                as_of=as_of,
                pdf_sha256=pdf_sha.hex(),
                csv_sha256=csv_sha.hex(),
            ),
            effective_date=as_of,
        )
        written.append(export_id)
    by_id = {e.id: e for e in list_exports(ctx.conn, key=key, now=ctx.now)}
    return [by_id[i] for i in written]


def list_exports(
    conn: Connection, *, key: bytes, now: datetime, year: int | None = None
) -> list[HoldingsExport]:
    """GET /exports/holdings: every stored export, newest first, with signed links."""
    x, a = export_table.c, account_table.c
    query = (
        select(
            x.id,
            x.workspace_id,
            x.account_id,
            a.label,
            x.as_of,
            x.pdf_sha256,
            x.csv_sha256,
            x.created_at,
        )
        .join(account_table, a.id == x.account_id)
        .order_by(x.created_at.desc(), a.label, x.id)
    )
    if year is not None:
        query = query.where(x.as_of == date(year, 12, 31))
    return [
        HoldingsExport(
            id=r.id,
            account_id=r.account_id,
            account_label=r.label,
            as_of=r.as_of,
            pdf_sha256=r.pdf_sha256.hex(),
            csv_sha256=r.csv_sha256.hex(),
            created_at=r.created_at,
            pdf_url=download_url(key, r.workspace_id, r.id, "pdf", now=now),
            csv_url=download_url(key, r.workspace_id, r.id, "csv", now=now),
        )
        for r in conn.execute(query).all()
    ]


def read_file(
    conn: Connection, store: BlobStore, export_id: UUID, fmt: HoldingsFileFormat
) -> tuple[bytes, str, str]:
    """The file's bytes (SHA-256 verified), media type and download name."""
    x, a = export_table.c, account_table.c
    row = conn.execute(
        select(x.as_of, x.pdf_sha256, x.csv_sha256, a.label)
        .join(account_table, a.id == x.account_id)
        .where(x.id == export_id)
    ).one_or_none()
    if row is None:
        raise NotFoundError(HOLDINGS_EXPORT, export_id)
    sha = row.pdf_sha256 if fmt == "pdf" else row.csv_sha256
    f = file_table.c
    stored = conn.execute(
        select(f.sha256, f.size_bytes, f.storage_key, f.media_type).where(f.sha256 == sha)
    ).one()
    data = store.get(stored.storage_key)
    if len(data) != stored.size_bytes or hashlib.sha256(data).digest() != stored.sha256:
        raise OriginalIntegrityError(f"holdings export {export_id} does not match its SHA-256")
    slug = "".join(c if c.isalnum() else "-" for c in row.label).strip("-") or "konto"
    media = stored.media_type + ("; charset=utf-8" if fmt == "csv" else "")
    return data, media, f"bestandsnachweis-{row.as_of.isoformat()}-{slug}.{fmt}"


def _signature(key: bytes, workspace_id: UUID, export_id: UUID, fmt: str, expires: int) -> str:
    message = _DOMAIN + f"{workspace_id}|{export_id}|{fmt}|{expires}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def download_url(
    key: bytes, workspace_id: UUID, export_id: UUID, fmt: str, *, now: datetime
) -> str:
    expires = int(now.timestamp()) + DOWNLOAD_TTL_S
    query = urlencode(
        {
            "format": fmt,
            "expires": expires,
            "signature": _signature(key, workspace_id, export_id, fmt, expires),
        }
    )
    return f"/api/exports/holdings/{export_id}/file?{query}"


def signature_valid(
    key: bytes,
    workspace_id: UUID,
    export_id: UUID,
    fmt: str,
    *,
    expires: int,
    signature: str,
    now: datetime,
) -> bool:
    if expires < int(now.timestamp()) or expires > int(now.timestamp()) + DOWNLOAD_TTL_S:
        return False
    if not signature.isascii():
        return False
    return hmac.compare_digest(_signature(key, workspace_id, export_id, fmt, expires), signature)


# --- The reminder's facts (D190) ----------------------------------------------------------


def crypto_years(conn: Connection, today: date, *, years_back: int) -> list[ReminderCryptoYear]:
    """Per closed year (at most `years_back` before today's), how many wallet and exchange
    accounts with a confirmed crypto movement on or before its 31 Dec have no holdings_export
    for that day: the accounts `write_exports` would write (services.holdings.statement)."""
    m, a, s = movement_table.c, account_table.c, asset_table.c
    rows = conn.execute(
        select(m.account_id, func.min(m.occurred_at).label("first_at"))
        .join(account_table, a.id == m.account_id)
        .join(asset_table, s.id == m.asset_id)
        .where(
            a.kind.in_([k.value for k in CRYPTO_ACCOUNT_KINDS]),
            s.kind == AssetKind.CRYPTO.value,
            m.status == MovementStatus.CONFIRMED.value,
            m.superseded_by.is_(None),
        )
        .group_by(m.account_id)
    ).all()
    if not rows:
        return []
    first = {r.account_id: berlin_day(r.first_at) for r in rows}
    x = export_table.c
    exported: dict[date, set[UUID]] = {}
    for r in conn.execute(select(x.account_id, x.as_of)).all():
        exported.setdefault(r.as_of, set()).add(r.account_id)
    out = []
    for year in range(today.year - years_back, today.year):
        end = date(year, 12, 31)
        held = {account for account, day in first.items() if day <= end}
        if held:
            missing = held - exported.get(end, set())
            out.append(ReminderCryptoYear(year=year, accounts_missing=len(missing)))
    return out
