"""The bytes of the Anlage N exports (docs/phases/phase-4b.md D104, D110, Q-O15). Pure, like
services/tax_export_files.py, whose look and helpers it shares: a draft or an estimate and the
export's id and time in, bytes out; no database, no blob store, no clock.

- render_anlage_n_sheet: "ELSTER-Ausfüllhilfe Anlage N <year>" for one owner: the header
  (owner, year, rule set, the provisional banner while the form mapping is a copy, the
  `tax_export.questions_open` banner while Jahresfragen are unanswered), one row per draft
  line with Zeile, Kennzahl and amount in the draft's own order (a Kennzahl may repeat: Kz
  110–114 stand both on the pay lines and on each commute block, so rows are never merged by
  Kennzahl), the totals "zur Kontrolle", the Einzelangaben for Z54–55 and Z62–63, the warnings,
  and a last page "Von Hand in ELSTER" with the year's reminders (D110).
- render_estimate_page: the Gesamt card as one PDF page for the yearly archive, every figure as
  the whole-return estimate computed it, marked "Schätzung".

Every figure is one the rules computed (principle 1): these functions format and arrange, they
add nothing up. The sheet names the owner it belongs to (it is that owner's form); it carries
no tax identifier, address or employer number, since the draft holds none (D103).
"""

import io
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from uuid import UUID
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from hausbuch.schemas.employment import AnlageNDraft, AnlageNRuleSet, ReturnEstimate
from hausbuch.schemas.tax import DraftLine, DraftWarning, FormStatus, WarningCode

from .tax_export_files import (
    _BODY,
    _CELL,
    _GRID,
    _H1,
    _H2,
    _NUM,
    _SMALL,
    ExportStamp,
    _date,
    _euros,
    _local,
    _money,
    _para,
    _percent,
    safe_filename,
    warning_text,
)

DISCLAIMER_N = (
    "Ein Entwurf zum Prüfen, keine Steuerberatung. Diese Ausfüllhilfe sagt, was in welche Zeile "
    "der Anlage N gehört; maßgeblich ist, was Sie selbst in ELSTER eintragen. Jede Zahl führt in "
    "Hausbuch zu ihren Bescheinigungen, Abrechnungen und Angaben."
)
DISCLAIMER_ESTIMATE = (
    "Eine Schätzung zum Planen, keine Steuerberatung und kein Bescheid. Sie rechnet mit den in "
    "Hausbuch erfassten Zahlen; was die App nicht berechnet, steht unter „Nicht berücksichtigt“."
)
GROSS_SOURCE = {
    "certificate": "aus der Lohnsteuerbescheinigung",
    "payslip": "Hochrechnung aus den Gehaltsabrechnungen",
    "expected": "aus dem erwarteten Bruttolohn (Eigentümer)",
}
DRAFT_STATUS = {"certified": "bescheinigt", "estimate": "Schätzung", "none": "kein Arbeitslohn"}
RETURN_STATUS = {"estimate": "Schätzung", "certified": "laut Bescheinigungen"}
# What the estimate leaves out (services/return_inputs.NOT_MODELLED; labels.ts NOT_MODELLED).
NOT_MODELLED = {
    "extraordinary_burden": "außergewöhnliche Belastungen",
    "riester": "Riester",
    "maintenance": "Unterhalt",
    "prepayments": "Vorauszahlungen",
    "capital_income": "Kapitalerträge (ab Phase 8)",
}
# Lines whose amount is a count (days, km), not euros (anlageN.tsx COUNT_LINE).
COUNT_LINE = re.compile(
    r"^(commute\.route_\d+\.(days|km|km_car|km_collective|km_other)|home_office\.days_[a-z_]+)$"
)
_ROUTE = re.compile(r"^commute\.route_(\d+)\.")
# Not form lines: the draft's own totals, printed below the lines (§4.1 "zur Kontrolle").
CONTROL_KEYS = ("wk.itemised", "wk.allowance", "wk.applied")


def anlage_n_sheet_filename(tax_year: int, owner_name: str) -> str:
    return safe_filename(f"Anlage-N_{tax_year}_{owner_name}.pdf")


def estimate_page_filename(tax_year: int) -> str:
    return f"Gesamt_{tax_year}_Schaetzung.pdf"


def questions_open_text(count: int) -> str:
    """The `tax_export.questions_open` banner: "N Jahresfragen offen" (D110)."""
    warning = DraftWarning(code=WarningCode.TAX_EXPORT_QUESTIONS_OPEN, params={"count": str(count)})
    return warning_text(warning)


def _count(value: Decimal) -> str:
    """90 → "90", 12.5 → "12,5": a number of days or kilometres."""
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value.normalize():f}".replace(".", ",")


def _zeile(line: DraftLine) -> str:
    if line.zeile is None:
        return ""
    if line.zeile_from is not None and line.zeile_from != line.zeile:
        return f"{line.zeile_from}–{line.zeile}"
    return str(line.zeile)


def _label(line: DraftLine) -> str:
    match = _ROUTE.match(line.key)
    return f"Arbeitsweg {match.group(1)}: {line.label}" if match else line.label


def _notes(line: DraftLine) -> list[str]:
    notes = list(line.notes)
    if line.einzelangaben:
        notes.append("Einzelangaben: " + ", ".join(line.einzelangaben))
    if line.provisional:
        notes.append("vorläufig: ein Beleg fehlt noch")
    return notes


def form_status_text_n(draft: AnlageNDraft, rule_set: AnlageNRuleSet) -> str:
    stamp = draft.form
    if stamp.form_status is FormStatus.PUBLISHED:
        return f"Formular {stamp.form_year} ({rule_set.form.version_stamp}), veröffentlicht"
    return (
        f"VORLÄUFIG: Anlage N {draft.tax_year} noch nicht veröffentlicht; Zeilen und Kennzahlen "
        f"nach dem Formular {stamp.form_year} ({rule_set.form.version_stamp})"
    )


def _line_rows(lines: Sequence[DraftLine], rule_set: AnlageNRuleSet) -> list[list]:
    """Betrag: the draft's amount to the cent. Eintrag: what goes into ELSTER: a line the
    form rounds (the Werbungskosten, in whole euros) its rounded amount; a line it does not
    round (pay and withheld taxes, typed to the cent as certified) the amount itself."""
    rows: list[list] = [["Zeile", "Kz", "Bezeichnung", "Betrag", "Eintrag"]]
    for line in lines:
        count = COUNT_LINE.match(line.key) is not None
        text = escape(_label(line)) + "".join(f"<br/><i>{escape(n)}</i>" for n in _notes(line))
        if count:
            amount, entry = _count(line.amount), _count(line.amount)
        else:
            form = rule_set.form.lines.get(line.key)
            rounded = form is not None and form.rounding != "none"
            amount = _money(line.amount)
            entry = _euros(line.amount_rounded) if rounded else amount
        rows.append(
            [
                _zeile(line),
                line.kennzahl or "",
                Paragraph(text, _CELL),
                Paragraph(escape(amount), _NUM),
                Paragraph(f"<b>{escape(entry)}</b>", _NUM),
            ]
        )
    return rows


def _form_lines(draft: AnlageNDraft) -> list[DraftLine]:
    """The lines with a Zeile, by Zeile; lines of one Zeile keep the draft's order (a stable
    sort), and nothing is merged: two lines with the same Kennzahl are two rows (Q-O15)."""
    printed = [line for line in draft.lines if line.zeile is not None]
    return sorted(printed, key=lambda line: line.zeile or 0)


def _table(rows: list[list], widths: Sequence[float]) -> Table:
    table = Table(rows, colWidths=list(widths), repeatRows=1)
    table.setStyle(_GRID)
    return table


def _items_rows(draft: AnlageNDraft) -> list[tuple[str, list[tuple[str, Decimal]]]]:
    """The Einzelangaben the form asks for by name: Arbeitsmittel (Z54–55) and Sonstiges
    (Z62–63, the telecom share among them), each item as the rules computed it."""
    c = draft.components
    out: list[tuple[str, list[tuple[str, Decimal]]]] = []
    if c.equipment is not None and c.equipment.items:
        items = [(item.label, item.amount) for item in c.equipment.items]
        out.append(("Arbeitsmittel (Zeile 54–55)", items))
    other: list[tuple[str, Decimal]] = []
    if c.telecom is not None:
        other.append(("Telekommunikation", c.telecom.amount))
    if c.other is not None:
        other += [(item.label, item.amount) for item in c.other.items]
    if other:
        out.append(("Weitere Werbungskosten (Zeile 62–63)", other))
    if c.dues is not None and c.dues.items:
        dues = [
            (f"{item.label} (Gewerkschaft)" if item.is_union else item.label, item.amount)
            for item in c.dues.items
        ]
        out.append(("Beiträge zu Berufsverbänden (Zeile 53)", dues))
    if c.training is not None and c.training.items:
        out.append(
            ("Fortbildungskosten (Zeile 60)", [(i.label, i.amount) for i in c.training.items])
        )
    return out


def _build(story: list, title: str, stamp: ExportStamp, *, provisional: bool, subject: str):
    created = _local(stamp.created_at)
    footer = f"{title} · Export {stamp.export_id} · {created:%d.%m.%Y}"

    def on_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(15 * mm, 10 * mm, footer)
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Seite {doc.page}")
        if provisional:
            canvas.drawString(15 * mm, A4[1] - 10 * mm, "VORLÄUFIG – Formularstand prüfen")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="Hausbuch",
        creator="Hausbuch",
        subject=subject,
        invariant=1,
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


def _questions_banner(count: int) -> list:
    if count <= 0:
        return []
    return [Paragraph(f"<b>Hinweis:</b> {escape(questions_open_text(count))}", _BODY)]


def render_anlage_n_sheet(
    draft: AnlageNDraft,
    rule_set: AnlageNRuleSet,
    stamp: ExportStamp,
    *,
    owner_name: str,
    reminders: Sequence[str] = (),
    questions_open: int = 0,
) -> bytes:
    """One owner's ELSTER-Ausfüllhilfe Anlage N as PDF bytes; the same inputs give the same
    bytes. `reminders`: the year's "von Hand in ELSTER" sentences (D110), printed on the last
    page; `questions_open`: the year's unanswered Jahresfragen at export time."""
    title = f"ELSTER-Ausfüllhilfe Anlage N {draft.tax_year}"
    provisional = draft.form.form_status is not FormStatus.PUBLISHED
    story: list = [_para(title, _H1)]
    story.append(_para(f"Eigentümer: {owner_name} · Steuerjahr {draft.tax_year}", _BODY))
    story.append(_para(f"Formularstand: {form_status_text_n(draft, rule_set)}", _BODY))
    story.append(_para(f"Regelwerk {draft.form.version} (Steuerjahr {draft.form.tax_year})"))
    if provisional:
        story.append(
            Paragraph(
                "<b>Vorläufig:</b> vor dem Abtippen Zeilen und Kennzahlen mit dem Formular in "
                "ELSTER vergleichen.",
                _BODY,
            )
        )
    story += _questions_banner(questions_open)

    source = GROSS_SOURCE.get(draft.gross_source or "", "kein Arbeitslohn erfasst")
    story.append(_para("Arbeitslohn und Werbungskosten", _H2))
    story.append(_para(f"Stand: {DRAFT_STATUS[draft.status]}, Arbeitslohn {source}.", _BODY))
    lines = _form_lines(draft)
    if lines:
        widths = (14 * mm, 10 * mm, 96 * mm, 30 * mm, 30 * mm)
        story.append(_table(_line_rows(lines, rule_set), widths))
    else:
        story.append(_para("Keine Formularzeilen."))
    story.append(
        _para(
            "Eintrag: Arbeitslohn und Steuerabzüge in Euro und Cent wie bescheinigt, "
            "Werbungskosten in vollen Euro (aufgerundet); Tage und Kilometer als Zahl. "
            "Dieselbe Kennzahl kann auf zwei Zeilen stehen (etwa Kz 110 bei Arbeitslohn "
            "und Arbeitsweg): maßgeblich ist die Zeile.",
            _SMALL,
        )
    )

    by_key = {line.key: line for line in draft.lines}
    control: list[list] = [["Zur Kontrolle (keine Formularzeile)", "Betrag"]]
    for key in CONTROL_KEYS:
        line = by_key.get(key)
        if line is not None:
            control.append([_para(line.label, _CELL), _money(line.amount)])
    if draft.wk_union:
        control.append(
            [_para("Gewerkschaftsbeiträge neben dem Pauschbetrag", _CELL), _money(draft.wk_union)]
        )
    control.append([_para("Einkünfte aus nichtselbständiger Arbeit", _CELL), _money(draft.result)])
    table = _table(control, (140 * mm, 40 * mm))
    table.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT")]))
    story.append(KeepTogether([_para("Ergebnis", _H2), table]))

    items = _items_rows(draft)
    if items:
        story.append(_para("Einzelangaben", _H2))
        for heading, entries in items:
            rows: list[list] = [[heading, "Betrag"]]
            rows += [[_para(label, _CELL), _money(amount)] for label, amount in entries]
            block = _table(rows, (140 * mm, 40 * mm))
            block.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT")]))
            story.append(KeepTogether([block, Spacer(1, 4)]))

    story.append(_para("Hinweise und offene Punkte", _H2))
    warnings = [warning_text(w) for w in draft.warnings]
    if questions_open > 0:
        warnings.append(questions_open_text(questions_open))
    if not warnings:
        story.append(_para("Keine."))
    story += [_para(f"– {text}") for text in warnings]

    created = _local(stamp.created_at)
    meta = [
        f"Formularstand: {form_status_text_n(draft, rule_set)}",
        f"Regelwerk {draft.form.version} (Steuerjahr {draft.form.tax_year})",
        f"Export {stamp.export_id}, erstellt {created:%d.%m.%Y %H:%M} Uhr",
    ]
    story.append(
        KeepTogether(
            [_para("Herkunft", _H2), *(_para(m, _SMALL) for m in meta), Spacer(1, 4)]
            + [_para(DISCLAIMER_N, _BODY)]
        )
    )

    story.append(PageBreak())
    story.append(_para("Von Hand in ELSTER", _H1))
    story.append(
        _para(
            f"Was Hausbuch für {draft.tax_year} nicht berechnet, nach den Antworten auf die "
            "Jahresfragen. Diese Angaben bitte selbst in ELSTER eintragen.",
            _BODY,
        )
    )
    story += _questions_banner(questions_open)
    if reminders:
        story += [_para(f"– {text}") for text in reminders]
    else:
        story.append(_para("Keine: keine der beantworteten Jahresfragen verlangt das."))

    return _build(
        story, title, stamp, provisional=provisional, subject=f"Anlage N {draft.tax_year}"
    )


def _estimate_rows(
    estimate: ReturnEstimate,
    owner_names: Mapping[UUID, str],
    property_labels: Mapping[UUID, str],
) -> list[list]:
    a = estimate.assessed
    rows: list[list] = [["Einkünfte und Steuer", "Betrag"]]

    def add(label: str, amount: Decimal, *, strong: bool = False) -> None:
        text = f"<b>{escape(label)}</b>" if strong else escape(label)
        rows.append([Paragraph(text, _CELL), Paragraph(escape(_money(amount)), _NUM)])

    for n in estimate.income_n:
        none = " (kein Arbeitslohn erfasst)" if n.status == "none" else ""
        add(f"Anlage N {owner_names.get(n.owner_id, 'Eigentümer')}{none}", n.result)
    for v in estimate.income_v:
        add(f"Anlage V {property_labels.get(v.property_id, 'Immobilie')}", v.result)
    add("Summe der Einkünfte", estimate.total_income, strong=True)
    add("Vorsorgeaufwendungen", estimate.vorsorge_total)
    if estimate.church_tax_paid:
        add("Kirchensteuer gezahlt", estimate.church_tax_paid)
    if estimate.donations:
        add("Spenden", estimate.donations)
    if estimate.childcare:
        add("Kinderbetreuungskosten", estimate.childcare)
    add("Sonderausgaben gesamt (mindestens der Pauschbetrag)", estimate.special_expenses)
    add("Zu versteuerndes Einkommen (zvE)", estimate.zve, strong=True)
    if a.progression is not None:
        add(
            "Einkommensteuer mit Progressionsvorbehalt (besonderer Steuersatz "
            f"{_percent(a.progression.rate, 4)} auf Lohnersatzleistungen "
            f"{_money(a.progression.benefits)})",
            a.est,
        )
    else:
        add("Einkommensteuer", a.est)
    if a.children is not None:
        ch = a.children
        if ch.favourable == "kindergeld":
            text = (
                f"Kinder ({ch.months} Monate): Kindergeld {_money(ch.kindergeld)} günstiger als "
                f"die Freibeträge (Ersparnis {_money(ch.saving)})"
            )
        else:
            text = (
                f"Kinder ({ch.months} Monate): Freibeträge {_money(ch.allowances)} günstiger als "
                f"das Kindergeld {_money(ch.kindergeld)} (Ersparnis {_money(ch.saving)})"
            )
        rows.append([Paragraph(f"<i>{escape(text)}</i>", _CELL), ""])
    add("Steuerermäßigung § 35a", a.credit_35a)
    if a.credit_35a:
        add("Einkommensteuer nach Ermäßigung", a.est_final)
    add("Solidaritätszuschlag", a.soli)
    add("Kirchensteuer", a.church)
    add("Festzusetzen", a.total, strong=True)
    add("Einbehalten (Lohnsteuer, Soli, Kirchensteuer laut Nr. 4–7)", estimate.withheld.total)
    outcome = "Ergebnis (Erstattung)" if estimate.result < 0 else "Ergebnis (Nachzahlung)"
    add(outcome, estimate.result, strong=True)
    return rows


def _headline(result: Decimal) -> str:
    if result == 0:
        return "Weder Erstattung noch Nachzahlung"
    if result < 0:
        return f"Voraussichtliche Erstattung {_money(-result)}"
    return f"Voraussichtliche Nachzahlung {_money(result)}"


def render_estimate_page(
    estimate: ReturnEstimate,
    stamp: ExportStamp,
    *,
    owner_names: Mapping[UUID, str],
    property_labels: Mapping[UUID, str],
) -> bytes:
    """The Gesamt card of `estimate.tax_year` as a PDF (one page as a rule), marked
    "Schätzung"; every figure as GET /tax/return-estimate computes it."""
    year = estimate.tax_year
    title = f"Einkommensteuer {year} · Gesamt (Schätzung)"
    open_count = len(estimate.questionnaire.open)
    story: list = [_para(title, _H1)]
    story.append(
        Paragraph(
            f"<b>{escape(_headline(estimate.result))}</b> · "
            f"{escape(RETURN_STATUS[estimate.status])}",
            _BODY,
        )
    )
    filing = estimate.filing_required
    reasons = "; ".join(filing.reasons)
    if filing.required:
        story.append(_para(f"Abgabe pflichtig ({reasons}), Frist {_date(filing.deadline)}"))
    else:
        story.append(_para(f"Abgabe freiwillig ({reasons})"))
    progress = estimate.questionnaire
    story.append(
        _para(f"Jahresfragen {year}: {progress.answered} von {progress.total} beantwortet")
    )
    story += _questions_banner(open_count)
    story.append(Spacer(1, 4))
    table = _table(_estimate_rows(estimate, owner_names, property_labels), (140 * mm, 40 * mm))
    story.append(table)
    if estimate.reminders:
        story.append(_para("Von Hand in ELSTER", _H2))
        story += [_para(f"– {text}") for text in estimate.reminders]
    shown = [
        w
        for w in estimate.warnings
        if w.code not in (WarningCode.RETURN_FILING_REQUIRED, WarningCode.RETURN_QUESTIONNAIRE_OPEN)
    ]
    if shown:
        story.append(_para("Hinweise", _H2))
        story += [_para(f"– {warning_text(w)}") for w in shown]
    if estimate.not_modelled:
        labels = ", ".join(NOT_MODELLED.get(code, code) for code in estimate.not_modelled)
        story.append(_para(f"Nicht berücksichtigt: {labels}", _SMALL))
    if estimate.tariff.reused_from is not None:
        story.append(
            _para(
                f"Tarif {estimate.tariff.reused_from} (für {year} noch keiner veröffentlicht)",
                _SMALL,
            )
        )
    created = _local(stamp.created_at)
    story.append(Spacer(1, 6))
    story.append(_para(f"Export {stamp.export_id}, erstellt {created:%d.%m.%Y %H:%M} Uhr", _SMALL))
    story.append(_para(DISCLAIMER_ESTIMATE, _BODY))
    return _build(story, title, stamp, provisional=False, subject=f"Einkommensteuer {year}")
