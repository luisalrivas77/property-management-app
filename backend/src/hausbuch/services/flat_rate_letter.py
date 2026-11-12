"""The letter that tells a tenant of a rise or a fall of their Nebenkostenpauschale
(docs/phases/phase-4.md D155 (7)).

Every figure comes from rules.performance.flat_rate.flat_rate_letter (fixture
tests/fixtures/phase4/flat_rate_letter.yaml), never from a model. The service resolves what the
letter prints besides the figures: the tenants' names, the property's address, and the title and
date of the document each reason's rate came from (never an IBAN, a counterparty or a purpose
text; every free string is IBAN-masked before it is printed). The PDF is returned, not stored,
and nothing is written: the signed letter as sent becomes the Beleg of the „Mietänderung“ that
records the new Pauschale. The sender's name and address come with the request and are printed
only.

The layout reuses the ELSTER field sheet's styles and formatting helpers
(services/tax_export_files.py) so both PDFs look alike.
"""

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table
from sqlalchemy import Connection

from hausbuch.rules.errors import FLAT_RATE_NO_LETTER, RuleError
from hausbuch.rules.iban import mask_ibans_in_text
from hausbuch.rules.performance.flat_rate import flat_rate_letter
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import EntityType
from hausbuch.schemas.performance import (
    FlatRateLetterFigures,
    FlatRateLetterRequest,
    FlatRateVerdict,
)
from hausbuch.schemas.property import Address
from hausbuch.schemas.tax import DraftFacts, SourceRef

from .documents import get_document
from .errors import NotFoundError
from .tax import draft_facts
from .tax_export_files import (
    _BODY,
    _CELL,
    _GRID,
    _H2,
    _NUM,
    _SMALL,
    PDF_MEDIA_TYPE,
    _date,
    _money,
)
from .tenancies import get_tenancy
from .tenants import get_tenant
from .units import get_unit

__all__ = [
    "PDF_MEDIA_TYPE",
    "LetterContent",
    "flat_rate_letter_figures",
    "flat_rate_letter_pdf",
    "render_flat_rate_letter",
]

# The reason's names as a tenant reads them (§560 Abs. 1 S. 2 BGB: the reason named).
REASON_LABEL = {
    AllocationCategory.HAUSGELD_UMLAGEFAEHIG: (
        "Umlagefähige Kosten aus dem Hausgeld der Eigentümergemeinschaft"
    ),
    AllocationCategory.GRUNDSTEUER: "Grundsteuer",
    AllocationCategory.INSURANCE: "Versicherungen",
}
SUBJECT = {
    FlatRateVerdict.RAISE: (
        "Erhöhung der Nebenkostenpauschale (§ 560 Abs. 1 BGB, Ziffer 4.1 des Mietvertrags)"
    ),
    FlatRateVerdict.LOWER: "Herabsetzung der Nebenkostenpauschale (§ 560 Abs. 3 BGB)",
}
_MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


@dataclass(frozen=True)
class LetterContent:
    """Everything the letter prints. `source_labels` names each reason's source by
    (kind, id): a document's title and date, else the bank line's date; `enclosed` are the
    sources named by a document, whose copies the letter lists as enclosed (a bank line is
    named in the table only)."""

    figures: FlatRateLetterFigures
    request: FlatRateLetterRequest
    tenants: Sequence[str]
    address: Address
    source_labels: Mapping[tuple[str, UUID], str]
    enclosed: frozenset[tuple[str, UUID]] = frozenset()


def _resolve(
    conn: Connection, tenancy_id: UUID, today: date, sent_on: date
) -> tuple[FlatRateLetterFigures, DraftFacts, list[str]]:
    """The figures, the property's facts and the tenants' names. NotFoundError for an unknown
    tenancy (or one of another workspace); RuleError flat_rate.no_letter when there is no rise
    above 10 € or fall to tell."""
    tenancy = get_tenancy(conn, tenancy_id)
    unit = None if tenancy is None else get_unit(conn, tenancy.unit_id)
    if tenancy is None or unit is None:
        raise NotFoundError(EntityType.TENANCY, tenancy_id)
    facts = draft_facts(conn, unit.property_id, tax_year=today.year, today=today)
    figures = flat_rate_letter(facts, today, tenancy_id, sent_on)
    if figures is None:
        raise RuleError(
            FLAT_RATE_NO_LETTER,
            "no rise above 10 € a month or fall of the Nebenkostenpauschale to tell",
        )
    names = []
    for party in sorted(tenancy.parties, key=lambda p: p.role != "primary"):
        tenant = get_tenant(conn, party.tenant_id)
        if tenant is not None and tenant.full_name:
            names.append(tenant.full_name)
    return figures, facts, names


def flat_rate_letter_figures(
    conn: Connection, tenancy_id: UUID, *, today: date, sent_on: date
) -> FlatRateLetterFigures:
    """GET /tenancies/{id}/flat-rate-letter: the figures the dialog shows before the PDF."""
    return _resolve(conn, tenancy_id, today, sent_on)[0]


def _source_labels(
    conn: Connection, facts: DraftFacts, refs: Sequence[SourceRef]
) -> tuple[dict[tuple[str, UUID], str], frozenset[tuple[str, UUID]]]:
    """Each source's label, and the sources named by a document."""
    allocations = {a.allocation_id: a for a in facts.allocations}
    tax_inputs = {t.id: t for t in facts.tax_inputs}
    labels: dict[tuple[str, UUID], str] = {}
    enclosed: set[tuple[str, UUID]] = set()
    for ref in refs:
        if (ref.kind, ref.id) in labels:
            continue
        document_id = None
        fallback = "Beleg"
        if ref.kind == "allocation" and ref.id in allocations:
            a = allocations[ref.id]
            document_id = a.document_id
            fallback = f"Kontoauszug, Zahlung vom {_date(a.booking_date)}"
        elif ref.kind == "tax_input" and ref.id in tax_inputs:
            document_id = tax_inputs[ref.id].document_id
            fallback = f"Grundsteuerbescheid {tax_inputs[ref.id].tax_year or ''}".strip()
        document = None if document_id is None else get_document(conn, document_id)
        labels[(ref.kind, ref.id)] = _document_label(document, fallback)
        if document is not None and document.typing is not None:
            enclosed.add((ref.kind, ref.id))
    return labels, frozenset(enclosed)


def _document_label(document, fallback: str) -> str:
    """A document's title and date, never its file name (which may hold anything)."""
    if document is None or document.typing is None:
        return fallback
    title = document.typing.title or fallback
    when = document.typing.document_date
    return title if when is None else f"{title} vom {_date(when)}"


def flat_rate_letter_pdf(
    conn: Connection,
    tenancy_id: UUID,
    request: FlatRateLetterRequest,
    *,
    today: date,
) -> tuple[bytes, str]:
    """POST /tenancies/{id}/flat-rate-letter: the letter as PDF bytes and a file name."""
    figures, facts, names = _resolve(conn, tenancy_id, today, request.sent_on)
    refs = [s for r in figures.reasons for s in (*r.then_sources, *r.now_sources)]
    labels, enclosed = _source_labels(conn, facts, refs)
    content = LetterContent(
        figures=figures,
        request=request,
        tenants=names,
        address=facts.property.address,
        source_labels=labels,
        enclosed=enclosed,
    )
    kind = "Erhoehung" if figures.kind is FlatRateVerdict.RAISE else "Herabsetzung"
    unit = "".join(ch if ch.isalnum() else "-" for ch in figures.unit_label).strip("-")
    filename = f"Nebenkostenpauschale-{kind}-{unit or 'Mietverhaeltnis'}-{request.sent_on}.pdf"
    return render_flat_rate_letter(content), filename


# --- The PDF -------------------------------------------------------------------------------


def _text(value: str) -> str:
    """Free text as printed: IBAN-masked, then escaped for a reportlab Paragraph; „§ 560“
    never breaks after the sign."""
    return escape(mask_ibans_in_text(value)).replace("§ ", "§\u00a0")


def _p(text: str, style=_BODY) -> Paragraph:
    return Paragraph(_text(text), style)


def _signed(value: Decimal) -> str:
    return _money(value) if value <= 0 else f"+{_money(value)}"


def _percent(value: Decimal) -> str:
    return f"{value:.1f}".replace(".", ",") + " %"


def _labels(content: LetterContent, refs: Sequence[SourceRef]) -> list[str]:
    seen: list[str] = []
    for ref in refs:
        label = content.source_labels.get((ref.kind, ref.id))
        if label and label not in seen:
            seen.append(label)
    return seen


def _enclosures(content: LetterContent) -> list[str]:
    """The documents behind the reasons, in the table's order, each once."""
    seen: list[str] = []
    for r in content.figures.reasons:
        for ref in (*r.then_sources, *r.now_sources):
            label = content.source_labels.get((ref.kind, ref.id))
            if (ref.kind, ref.id) in content.enclosed and label and label not in seen:
                seen.append(label)
    return seen


def render_flat_rate_letter(content: LetterContent) -> bytes:
    """The letter as PDF bytes; the same content gives the same bytes."""
    f = content.figures
    req = content.request
    raise_ = f.kind is FlatRateVerdict.RAISE
    addressees = " und ".join(content.tenants)
    story: list = []

    sender = [req.sender_name, *req.sender_address]
    story.append(_p(" · ".join(sender), _SMALL))
    story.append(Spacer(1, 6 * mm))
    a = content.address
    recipient = [
        addressees or "An die Mieterin oder den Mieter",
        f.unit_label,
        f"{a.street} {a.house_number}",
        f"{a.postal_code} {a.city}",
    ]
    story += [_p(line) for line in recipient if line]
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(_text(_date(req.sent_on)), _NUM))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"<b>{_text(SUBJECT[f.kind])}</b>", _BODY))
    story.append(Spacer(1, 4 * mm))
    story.append(_p(f"Guten Tag {addressees}," if addressees else "Guten Tag,"))
    story.append(Spacer(1, 2 * mm))
    if raise_:
        story.append(
            _p(
                f"die Betriebskosten der Wohnung sind gestiegen, seit Ihre Nebenkostenpauschale "
                f"am {_date(f.set_on)} festgelegt wurde. Nach § 560 Abs. 1 BGB und Ziffer 4.1 "
                "Ihres Mietvertrags lege ich den auf Sie entfallenden Teil der Erhöhung auf die "
                "Pauschale um. Die Gründe im Einzelnen, je Monat für die ganze Wohnung:"
            )
        )
    else:
        story.append(
            _p(
                f"die Betriebskosten der Wohnung sind gesunken, seit Ihre Nebenkostenpauschale am "
                f"{_date(f.set_on)} festgelegt wurde. Nach § 560 Abs. 3 BGB setze ich Ihre "
                "Pauschale um den auf Sie entfallenden Teil herab. Die Gründe im Einzelnen, je "
                "Monat für die ganze Wohnung:"
            )
        )
    story.append(Spacer(1, 2 * mm))

    rows: list[list] = [
        [
            _p("Kostenart", _CELL),
            _p(f"bei Festlegung ({_date(f.set_on)})", _CELL),
            _p(f"jetzt ({_date(f.as_of)})", _CELL),
            _p("Änderung", _CELL),
            _p("Grundlage", _CELL),
        ]
    ]
    for r in f.reasons:
        then_labels = _labels(content, r.then_sources)
        now_labels = _labels(content, r.now_sources)
        if then_labels == now_labels:
            basis = "; ".join(now_labels)
        else:
            basis = f"bisher: {'; '.join(then_labels)}; jetzt: {'; '.join(now_labels)}"
        rows.append(
            [
                _p(REASON_LABEL[r.category], _CELL),
                _p(_money(r.then), _NUM),
                _p(_money(r.now), _NUM),
                _p(_signed(r.change), _NUM),
                _p(basis, _CELL),
            ]
        )
    rows.append(
        [
            Paragraph("<b>Summe</b>", _CELL),
            _p(_money(sum((r.then for r in f.reasons), Decimal("0.00"))), _NUM),
            _p(_money(sum((r.now for r in f.reasons), Decimal("0.00"))), _NUM),
            Paragraph(f"<b>{_text(_signed(f.flat_rise))}</b>", _NUM),
            _p("", _CELL),
        ]
    )
    reasons = Table(rows, colWidths=[46 * mm, 26 * mm, 26 * mm, 22 * mm, 60 * mm], repeatRows=1)
    reasons.setStyle(_GRID)
    story.append(reasons)
    story.append(Spacer(1, 3 * mm))

    story.append(_p("Ihr Anteil", _H2))
    story.append(
        _p(
            f"Die Änderung für die ganze Wohnung beträgt {_signed(f.flat_rise)} im Monat. Sie "
            "wird nach dem Verhältnis der Pauschalen verteilt: Ihre Pauschale von "
            f"{_money(f.pauschale)} geteilt durch die Summe der Pauschalen aller Mietverhältnisse "
            f"der Wohnung von {_money(f.pauschalen_total)} ergibt einen Anteil von "
            f"{_percent(f.share_percent)}. Ihr Anteil: {_signed(f.flat_rise)} × "
            f"{_money(f.pauschale)} ÷ {_money(f.pauschalen_total)} = {_signed(f.rise)} im Monat "
            "(auf den Cent gerundet)."
        )
    )
    story.append(Spacer(1, 2 * mm))
    amounts = Table(
        [
            [_p("Bisherige Nebenkostenpauschale", _CELL), _p(_money(f.pauschale), _NUM)],
            [_p("Änderung", _CELL), _p(_signed(f.new_pauschale - f.pauschale), _NUM)],
            [
                Paragraph("<b>Neue Nebenkostenpauschale</b>", _CELL),
                Paragraph(f"<b>{_text(_money(f.new_pauschale))}</b>", _NUM),
            ],
            [_p("Nettokaltmiete", _CELL), _p(_money(f.cold_rent), _NUM)],
            [
                Paragraph("<b>Neue Monatsmiete insgesamt</b>", _CELL),
                Paragraph(f"<b>{_text(_money(f.new_total))}</b>", _NUM),
            ],
        ],
        colWidths=[80 * mm, 30 * mm],
    )
    amounts.setStyle(_GRID)
    story.append(amounts)
    story.append(Spacer(1, 3 * mm))

    if raise_:
        month = _MONTHS[req.sent_on.month - 1]
        story.append(
            _p(
                f"Die neue Pauschale gilt ab dem {_date(f.starts_on)}. Nach § 560 Abs. 2 BGB "
                "schulden Sie die Erhöhung vom Beginn des übernächsten Monats an, der auf den "
                "Zugang dieses Schreibens folgt; maßgeblich ist also der Tag, an dem Sie es "
                f"erhalten. Geht es Ihnen erst nach Ende {month} {req.sent_on.year} zu, verschiebt "
                "sich der Beginn "
                "entsprechend."
            )
        )
    else:
        story.append(
            _p(
                f"Die neue Pauschale gilt ab dem {_date(f.starts_on)}, dem Tag, an dem die Kosten "
                "gesunken sind (§ 560 Abs. 3 BGB)."
            )
        )
    story.append(Spacer(1, 3 * mm))
    enclosures = _enclosures(content)
    if enclosures:
        story.append(_p("Als Nachweis liegen Kopien dieser Unterlagen bei:"))
        story += [_p(f"– {label}") for label in enclosures]
        story.append(Spacer(1, 3 * mm))

    closing = [
        _p("Mit freundlichen Grüßen"),
        Spacer(1, 14 * mm),
        _p("______________________________"),
        _p(req.sender_name),
    ]
    story.append(KeepTogether(closing))

    def on_page(canvas, doc) -> None:
        if doc.page > 1:
            canvas.saveState()
            canvas.setFont("Helvetica", 7)
            canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Seite {doc.page}")
            canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=SUBJECT[f.kind],
        author="Hausbuch",
        creator="Hausbuch",
        invariant=1,
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()
