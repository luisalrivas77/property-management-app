"""The bytes of a tax export (docs/phases/phase-3.md §12, D60, D65, D68). Pure: a draft, its
facts and the export's id and time in, bytes out; no database, no blob store, no clock.

- render_field_sheet: "ELSTER-Ausfüllhilfe Anlage V <year>", a PDF written with reportlab: the
  header lines, the table Zeile | Kennzahl | Bezeichnung | Betrag (Cent) | Eintrag (volle
  Euro) | Quellen (the rows of rules.tax.field_sheet), the boxes to tick, the AfA-Erläuterung
  (Z33 asks for it in the first year), the warnings and open items, the Formularstand, the
  rule set version, the export id, the SHA-256 of the draft snapshot and the disclaimer.
- lines_csv, payments_csv, readme_text, render_archive: the yearly archive (ZIP).

Privacy (D5, §6 invariant). The sheet and both CSVs are built from the draft, the draft facts
and the rule set only, which carry no tenant or owner name, no full IBAN and no purpose text: a
counterparty is at most its masked IBAN (zahlungen.csv), the owners are the form's "Person A"
and "Person B" with their shares, and a Beleg is named in zahlungen.csv by its number in
dokumente/, never by its title (a title can name a tenant). The originals in dokumente/ are
the documents themselves, untouched (principle 6), and README.txt lists them by file name.

Determinism. The same inputs give the same bytes: reportlab runs in invariant mode (fixed
creation date and document id), only the standard Helvetica fonts are used, every ZIP entry
carries the export's own time and fixed attributes, and the entries come in a fixed order. Two
exports of unchanged data still differ, by design: each sheet prints its own export id and
creation time. The SHA-256 printed on the sheet is that of the draft snapshot (the canonical
JSON stored in tax_export.draft), since a file cannot carry its own hash.
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from hausbuch.rules.categories import CATEGORY_LABEL
from hausbuch.rules.tax.field_sheet import UNIT_ROWS, FieldSheetRow, field_sheet
from hausbuch.rules.tax.rounding import cents
from hausbuch.schemas.checklist import ChecklistStatus, RequirementCode
from hausbuch.schemas.employment import AnlageNDraft
from hausbuch.schemas.enums import AcquisitionCostKind, PriceSplitSource
from hausbuch.schemas.tax import (
    AnlageVDraft,
    DraftFacts,
    DraftLine,
    DraftWarning,
    FormStatus,
    RuleSet,
    SourceRef,
    WarningCode,
)

LOCAL_TIME_ZONE = ZoneInfo("Europe/Berlin")
PDF_MEDIA_TYPE = "application/pdf"
ZIP_MEDIA_TYPE = "application/zip"
DISCLAIMER = (
    "Ein Entwurf zum Prüfen, keine Steuerberatung. Diese Ausfüllhilfe sagt, was in welche Zeile "
    "der Anlage V gehört; maßgeblich ist, was Sie selbst in ELSTER eintragen. Jede Zahl führt in "
    "Hausbuch zu ihren Zahlungen und Belegen."
)
FinanzamtCopy = Literal["xfa", "annotations"]
NOT_ON_FORM = "nicht auf dem Formular"  # zahlungen.csv: a payment no line counts

# --- German labels (the frontend's labels.ts holds the screen's copies) ------------------

COST_KIND = {
    AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX: "Grunderwerbsteuer",
    AcquisitionCostKind.NOTARY: "Notar",
    AcquisitionCostKind.LAND_REGISTRY: "Grundbuchamt",
    AcquisitionCostKind.BROKER: "Makler",
    AcquisitionCostKind.OTHER: "Sonstige",
}
SPLIT_SOURCE = {
    PriceSplitSource.PURCHASE_CONTRACT: "Kaufvertrag",
    PriceSplitSource.BMF_TOOL: "BMF-Arbeitshilfe",
    PriceSplitSource.APPRAISAL: "Gutachten",
}
REQUIREMENT = {
    RequirementCode.PURCHASE_CONTRACT: "Kaufvertrag",
    RequirementCode.PRICE_SPLIT: "Beleg der Kaufpreisaufteilung",
    RequirementCode.ACQUISITION_COST_EVIDENCE: "Beleg der Anschaffungsnebenkosten",
    RequirementCode.GRUNDSTEUERBESCHEID: "Grundsteuerbescheid",
    RequirementCode.LOAN_CONTRACT: "Darlehensvertrag",
    RequirementCode.ZINSBESCHEINIGUNG: "Zinsbescheinigung",
    RequirementCode.LEASE: "Mietvertrag",
    RequirementCode.HANDOVER_PROTOCOL: "Übergabeprotokoll",
    RequirementCode.DEPOSIT_RECORD: "Kautionsnachweis",
    RequirementCode.WIRTSCHAFTSPLAN: "Wirtschaftsplan",
    RequirementCode.HAUSGELDABRECHNUNG: "Hausgeldabrechnung",
    RequirementCode.COST_RECEIPT: "Rechnung zu einer Zahlung",
    RequirementCode.MARKET_RENT: "Ortsübliche Miete (Mietspiegel)",
}
CHECKLIST_STATUS = {
    ChecklistStatus.MISSING: "fehlt",
    ChecklistStatus.EXPECTED: "erwartet",
    ChecklistStatus.INCOMPLETE: "Steuerjahr fehlt am Dokument",
}
COPY_REASON: dict[str, str] = {
    "xfa": "Adobe-Formular (XFA): nur Adobe Reader zeigt den Inhalt",
    "annotations": "Unterschriften oder Stempel als Anmerkungen (Ink, FreeText, Stamp)",
}


def _money(value: Decimal | None) -> str:
    """1234.5 → "1.234,50 €"."""
    if value is None:
        return "–"
    text = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{text} €"


def _euros(value: int | None) -> str:
    return "–" if value is None else f"{value:,}".replace(",", ".")


def _plain(value: Decimal) -> str:
    """A CSV amount: decimal comma, no thousands separator ("-7948,66")."""
    return f"{value:.2f}".replace(".", ",")


def _date(value: date | None) -> str:
    return "–" if value is None else value.strftime("%d.%m.%Y")


def _local(value: datetime) -> datetime:
    return value.astimezone(LOCAL_TIME_ZONE)


def _percent(value: Decimal, places: int = 2) -> str:
    text = f"{value * 100:.{places}f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} %"


def _p(params: dict[str, str], name: str = "amount") -> str:
    raw = params.get(name)
    if raw is None:
        return ""
    try:
        return _money(Decimal(raw))
    except ArithmeticError:
        return raw


def _count(params: dict[str, str], one: str, many: str) -> str:
    n = params.get("count")
    return f"{n or 'Einige'} {one if n == '1' else many}"


_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
    "Oktober", "November", "Dezember",
)  # fmt: skip


def _month_name(iso: str | None) -> str | None:
    """A month "2026-07" as "Juli 2026"; None for anything else."""
    match = re.fullmatch(r"([0-9]{4})-(0[1-9]|1[0-2])", iso or "")
    return f"{_MONTHS[int(match[2]) - 1]} {match[1]}" if match else None


def _payslip_gap_text(p: dict[str, str]) -> str:
    """anlage_n.payslip_gap with its months (labels.ts says the same, with the employer)."""
    month = _month_name(p.get("month"))
    if month is None:
        return "Zwischen den Gehaltsabrechnungen fehlt ein Monat."
    missing = [m for m in (_month_name(x) for x in p.get("missing", "").split(",")) if m]
    if missing:
        names = ", ".join(missing)
        verb = "fehlt" if len(missing) == 1 else "fehlen"
        return (
            f"Gehaltsabrechnung {names} {verb}: die Jahressumme im {month} passt nicht zur "
            "Summe der Abrechnung davor."
        )
    return (
        f"Gehaltsabrechnung {month} passt nicht zur Vormonatssumme: bitte die Jahreswerte prüfen."
    )


# The warning texts of labels.ts WARNING_TEXT, without any counterparty: the sheet carries no
# IBAN, not even a masked one.
WARNING_TEXT: dict[WarningCode, Callable[[dict[str, str]], str]] = {
    WarningCode.YEAR_END_STRADDLE: lambda p: (
        f"Eine Zahlung {_p(p)} ist im einen Jahr gebucht und im anderen wertgestellt; "
        "sie zählt im Jahr der Buchung."
    ),
    WarningCode.AFA_NO_SPLIT: lambda p: (
        "Keine Kaufpreisaufteilung gewählt: ohne Gebäudeanteil bleibt die AfA leer."
    ),
    WarningCode.AFA_COST_ROW_UNPAID: lambda p: (
        f"{_cost_kind(p.get('kind'))} {_p(p)}: keine Zahlung vom Mietkonto gefunden (vielleicht "
        "von einem anderen Konto bezahlt). Sie zählt trotzdem zur AfA-Basis."
    ),
    WarningCode.AFA_ACQUISITION_COST_WITHOUT_ROW: lambda p: (
        f"Zahlung von Anschaffungsnebenkosten über {_p(p)} ohne passenden Eintrag unter "
        "Nebenkosten: sie zählt erst zur AfA-Basis, wenn sie dort erfasst ist."
    ),
    WarningCode.AFA_PURCHASE_PRICE_MISMATCH: lambda p: (
        f"Vom Konto bezahlter Kaufpreis {_p(p, 'paid')} weicht vom Kaufpreis {_p(p, 'price')} ab."
    ),
    WarningCode.AFA_USEFUL_LIFE_REPORT: lambda p: (
        f"AfA nach kürzerer tatsächlicher Nutzungsdauer ({p.get('years', '?')} Jahre ab "
        f"{p.get('from_year', '?')}): in Zeile 33 „laut Erläuterung“ angeben; das Finanzamt "
        "fragt meist nach dem Gutachten."
    ),
    WarningCode.AFA_MOVABLE_ASSET_NO_EVIDENCE: lambda p: (
        f"Der Wert der Einbauküche ({_p(p)}) ist ohne Beleg erfasst. Eine Schätzung ist "
        "zulässig; bitte die Grundlage (z. B. einen Ausdruck des Küchenplaners) aufbewahren "
        "und als Beleg hinterlegen."
    ),
    WarningCode.AFA_MOVABLE_ASSET_EXCEEDS_BUILDING: lambda p: (
        f"Die Einbauküche ({_p(p, 'deducted')} mit Nebenkosten) ist nicht kleiner als der "
        f"Gebäudeanteil ({_p(p, 'building')}): die Gebäude-AfA fällt auf 0. Bitte Wert der "
        "Küche oder Kaufpreisaufteilung prüfen."
    ),
    WarningCode.LIMIT_EXCEEDED: lambda p: (
        f"Die 15 %-Grenze ist {p.get('exceeded_in_year', '')} überschritten: die "
        "Renovierungskosten des Dreijahresfensters werden Herstellungskosten und laufen über "
        "die AfA."
    ),
    WarningCode.LIMIT_PRIOR_YEAR_AMENDMENT: lambda p: (
        f"Die 15 %-Grenze wurde {p.get('exceeded_in_year', 'später')} überschritten: {_p(p)} "
        "dieses Jahres werden nachträglich Herstellungskosten; der Bescheid für dieses Jahr "
        "wird geändert."
    ),
    WarningCode.LIMIT_ESTIMATED_NET: lambda p: (
        "Für mindestens eine Rechnung fehlt der Nettobetrag; die 15 %-Grenze rechnet dort mit "
        "geschätzter Umsatzsteuer."
    ),
    WarningCode.LIMIT_PRIVATE_COST_UNDATED: lambda p: (
        f"Für eine von einem anderen Konto bezahlte Reparatur ({p.get('label', '')}) fehlt das "
        "Leistungsdatum; sie zählt vorsichtshalber zur 15 %-Grenze der ersten drei Jahre nach "
        "dem Kauf."
    ),
    WarningCode.LIMIT_PRIVATE_COST_AFTER_WINDOW: lambda p: (
        f"Eine von einem anderen Konto bezahlte Reparatur ({p.get('label', '')}) ist kurz nach "
        "dem Dreijahresfenster datiert: wann wurde die Arbeit ausgeführt? Eine Leistung im "
        "Fenster zählt zur 15 %-Grenze."
    ),
    WarningCode.ANSCHAFFUNGSNAH_PERFORMANCE_DATE_CHECK: lambda p: (
        f"Kosten von {_p(p)}, bezahlt kurz nach dem Dreijahresfenster: wann wurde die Arbeit "
        "ausgeführt? Ein Leistungszeitraum im Fenster zählt zur 15 %-Grenze."
    ),
    WarningCode.RENOVATION_UNDECIDED: lambda p: (
        f"Renovierung {_p(p)} einordnen: Erhaltungsaufwand oder Herstellungskosten? Bis dahin "
        "nicht abgezogen."
    ),
    WarningCode.INTEREST_CERTIFICATE_MISMATCH: lambda p: (
        f"Zinsbescheinigung {_p(p, 'certified')}, gebucht {_p(p, 'booked')}: Unterschied "
        f"{_p(p, 'difference')}. Meist eine Rate, die die Zehn-Tage-Regel ins andere Jahr "
        "verschiebt."
    ),
    WarningCode.HAUSGELD_ABRECHNUNG_PENDING: lambda p: (
        "Die Hausgeldabrechnung fehlt noch: was die WEG aus der Rücklage ausgegeben hat, ist "
        "vorläufig nicht enthalten."
    ),
    WarningCode.INCOME_OTHER_REVIEW: lambda p: f"Sonstige Einnahme {_p(p)} prüfen.",
    WarningCode.INCOME_PRE_HANDOVER: lambda p: (
        f"{_count(p, 'Einnahme', 'Einnahmen')} vor dem Übergang ({_p(p)}): noch nicht "
        "festgelegt, wie sie zählen."
    ),
    WarningCode.INCOME_LIVING_AREA_MISSING: lambda p: (
        "Die Wohnfläche der Immobilie fehlt; Zeile 13 nimmt die Summe der Flächen der Einheiten."
    ),
    WarningCode.UMLAGEN_MIXED_TERMS: lambda p: (
        "Mietverhältnisse mit Nebenkosten inklusive und mit Vorauszahlung gemischt: das Kreuz "
        "in Zeile 24 bleibt leer."
    ),
    WarningCode.REDUCED_RENT_NO_MARKET_RENT: lambda p: (
        "Ortsübliche Miete eintragen, um die verbilligte Vermietung (66 %-Grenze) zu prüfen."
    ),
    WarningCode.REDUCED_RENT_PROGNOSIS_NEEDED: lambda p: (
        "Die Miete liegt zwischen 50 % und 66 % der ortsüblichen: das Finanzamt kann eine "
        "Überschussprognose verlangen."
    ),
    WarningCode.REDUCED_RENT_PARTIAL: lambda p: (
        "Die Miete liegt unter 50 % der ortsüblichen: die Werbungskosten sind nur anteilig "
        f"abziehbar (Kürzung um {p.get('cut_percent', '?')} %)."
    ),
    WarningCode.DRAFT_OPEN_LINES_EXCLUDED: lambda p: (
        f"{_count(p, 'Zeile', 'Zeilen')} in Zu prüfen ({_p(p, 'sum')}) "
        f"{'ist' if p.get('count') == '1' else 'sind'} im Entwurf noch nicht enthalten."
    ),
    WarningCode.DRAFT_FORM_PROVISIONAL: lambda p: (
        "Das Formular für dieses Jahr ist noch nicht veröffentlicht; Zeilen und Kennzahlen sind "
        "vorläufig."
    ),
    WarningCode.DRAFT_ALLOCATION_WITHOUT_PROPERTY: lambda p: (
        f"Eine Zahlung des Kontos {_p(p)} ist keiner Immobilie zugeordnet."
    ),
    WarningCode.ARCHIVE_FINANZAMT_COPY_NEEDED: lambda p: (
        "Ein Dokument öffnet nicht in jedem Programm (Adobe-Formular oder Anmerkungen): für das "
        "Finanzamt eine Kopie drucken (in Adobe Reader öffnen und als PDF drucken)."
    ),
    # Phase 4b (docs/phases/phase-4b.md §9).
    WarningCode.TAX_TRAVEL_FREQUENCY: lambda p: (
        "Mehr als 50 Fahrten zur Immobilie im Jahr: bei fast täglicher Anwesenheit kann "
        "nur die halbe Pauschale gelten. Bitte prüfen."
    ),
    WarningCode.TAX_PRIVATE_COST_PAID_FROM_ACCOUNT: lambda p: (
        "Eine Rechnung ist als von einem anderen Konto bezahlt eingetragen, wurde aber vom "
        "Mietkonto bezahlt: sie zählt nur einmal, mit der Buchung vom Mietkonto. Die Angabe "
        "bitte zurückziehen."
    ),
    WarningCode.ANLAGE_N_CERTIFICATE_MISSING: lambda p: (
        "Keine Lohnsteuerbescheinigung für dieses Jahr: die Zahlen beruhen auf "
        "Gehaltsabrechnungen oder der Schätzung."
    ),
    WarningCode.ANLAGE_N_CHURCH_MISMATCH: lambda p: (
        "Die Kirchensteuermerkmale der Bescheinigung passen nicht zur Angabe beim Eigentümer."
    ),
    WarningCode.ANLAGE_N_TAX_CLASS_6: lambda p: (
        "Steuerklasse 6: ein zweites Arbeitsverhältnis. Es steht in der eigenen Spalte "
        "für Steuerklasse 6."
    ),
    WarningCode.ANLAGE_N_PERIODS_OVERLAP: lambda p: (
        "Zwei Bescheinigungen desselben Arbeitgebers überschneiden sich im Zeitraum."
    ),
    WarningCode.ANLAGE_N_DAYS_IMPLAUSIBLE: lambda p: (
        "Die Zahl der Arbeitstage passt nicht zu den Tagen pro Woche und dem Zeitraum."
    ),
    WarningCode.ANLAGE_N_HOME_OFFICE_CAPPED: lambda p: (
        "Die Tagespauschale ist auf den Jahreshöchstbetrag begrenzt."
    ),
    WarningCode.ANLAGE_N_TOO_MANY_ROUTES: lambda p: (
        "Mehr Wege zur Arbeit, als das Formular Zeilen hat: die übrigen bitte von Hand eintragen."
    ),
    WarningCode.ANLAGE_N_RECEIPT_MISSING: lambda p: "Für eine Ausgabe ist kein Beleg hinterlegt.",
    WarningCode.ANLAGE_N_FORM_PROVISIONAL: lambda p: (
        "Die Anlage N für dieses Jahr ist noch nicht veröffentlicht; Zeilen und "
        "Kennzahlen sind vorläufig."
    ),
    WarningCode.ANLAGE_N_PAYSLIP_GAP: _payslip_gap_text,
    WarningCode.ANLAGE_N_PAYSLIP_NO_YTD: lambda p: (
        "Die Gehaltsabrechnung hat keine Jahreswerte: die Hochrechnung nutzt nur den Monatswert."
    ),
    WarningCode.ANLAGE_N_WORK_ROOM_RECORDS: lambda p: (
        "Für das Arbeitszimmer bitte Grundriss und Kosten aufbewahren."
    ),
    WarningCode.ANLAGE_N_WORK_ROOM_CENTRE: lambda p: (
        "Ein Arbeitszimmer zählt nur, wenn es der Mittelpunkt der gesamten Tätigkeit ist. "
        "Bitte prüfen."
    ),
    WarningCode.ANLAGE_N_WORK_ROOM_PARTIAL: lambda p: (
        f"Das Arbeitszimmer zählt für {p.get('months') or 'einige'} Monate: die "
        "Tagespauschale nur für Tage der übrigen Monate eintragen."
    ),
    WarningCode.ANLAGE_N_FIRST_WORKPLACE_CHECK: lambda p: (
        "Bitte prüfen, ob dies die erste Tätigkeitsstätte ist (Festlegung durch den Arbeitgeber)."
    ),
    WarningCode.RETURN_PROGRESSION_PROVISIONAL: lambda p: (
        "Lohnersatzleistungen sind noch nicht bescheinigt: der Steuersatz ist vorläufig."
    ),
    WarningCode.RETURN_BENEFIT_POSSIBLE_DOUBLE: lambda p: (
        "Eine Lohnersatzleistung steht in der Bescheinigung und als eigene Angabe: bitte "
        "nur einmal erfassen."
    ),
    WarningCode.RETURN_CHILDREN_MISMATCH: lambda p: (
        "Die Zahl der Kinderfreibeträge der Bescheinigung passt nicht zu den erfassten Kindern."
    ),
    WarningCode.RETURN_KINDERGELD_APPLY: lambda p: (
        "Für ein Kind ist noch kein Kindergeld erfasst: bitte bei der Familienkasse beantragen."
    ),
    WarningCode.RETURN_CHILD_OVER_18: lambda p: (
        "Ein Kind ist volljährig: es zählt nur bei Ausbildung oder Studium."
    ),
    WarningCode.RETURN_CHILDCARE_UNTIL_14: lambda p: (
        "Ein Kind wird in diesem Jahr 14: es zählen nur die Kinderbetreuungskosten bis "
        "zum 14. Geburtstag."
    ),
    WarningCode.RETURN_DONATION_OVER_CAP: lambda p: (
        "Die Spenden übersteigen die Höchstgrenze; der Rest zählt nicht in diesem Jahr."
    ),
    WarningCode.RETURN_FILING_REQUIRED: lambda p: (
        "Für dieses Jahr besteht eine Pflicht zur Abgabe der Steuererklärung."
    ),
    WarningCode.RETURN_QUESTIONNAIRE_OPEN: lambda p: "Es sind noch Fragen zum Steuerjahr offen.",
    WarningCode.RETURN_NOT_MODELLED: lambda p: (
        "Ein Teil der Erklärung wird von der App nicht berechnet; die Schätzung ist unvollständig."
    ),
    WarningCode.QUESTIONNAIRE_PROVISIONAL: lambda p: (
        "Die Fragen für dieses Jahr sind noch nicht festgelegt; es gelten die des Vorjahres."
    ),
    WarningCode.TAX_EXPORT_QUESTIONS_OPEN: lambda p: (
        f"{_count(p, 'Jahresfrage', 'Jahresfragen')} offen: beim Export noch nicht "
        "beantwortet, die Angaben können sich noch ändern."
    ),
}


def _cost_kind(kind: str | None) -> str:
    try:
        return COST_KIND[AcquisitionCostKind(kind)]
    except ValueError:
        return "Nebenkosten"


def warning_text(warning: DraftWarning) -> str:
    return " ".join(WARNING_TEXT[warning.code](warning.params).split())


# --- The snapshot ------------------------------------------------------------------------


def draft_json(draft: AnlageVDraft | AnlageNDraft) -> dict:
    """The draft as stored in tax_export.draft (D59, D65)."""
    return draft.model_dump(mode="json")


def draft_sha256(draft: AnlageVDraft) -> str:
    """SHA-256 of the canonical JSON of the snapshot (sorted keys, no spaces): recomputable from
    the stored jsonb, whose key order Postgres does not keep."""
    canonical = json.dumps(draft_json(draft), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def form_status_text(draft: AnlageVDraft, rule_set: RuleSet) -> str:
    stamp = draft.rule_set
    if stamp.form_status is FormStatus.PUBLISHED:
        return f"Formular {stamp.form_year} ({rule_set.form.version_stamp}), veröffentlicht"
    return (
        f"VORLÄUFIG: Formular {draft.tax_year} noch nicht veröffentlicht; Zeilen und Kennzahlen "
        f"nach dem Formular {stamp.form_year} ({rule_set.form.version_stamp})"
    )


# --- The field sheet (PDF) ---------------------------------------------------------------


@dataclass(frozen=True)
class ExportStamp:
    export_id: UUID
    created_at: datetime  # timezone-aware; printed in Berlin time


_STYLES = getSampleStyleSheet()
_BODY = ParagraphStyle("hb-body", parent=_STYLES["BodyText"], fontSize=8.5, leading=10.5)
_SMALL = ParagraphStyle("hb-small", parent=_BODY, fontSize=7.5, leading=9)
_CELL = ParagraphStyle("hb-cell", parent=_BODY, fontSize=8, leading=9.5)
_NUM = ParagraphStyle("hb-num", parent=_CELL, alignment=TA_RIGHT)
_H1 = ParagraphStyle("hb-h1", parent=_STYLES["Heading1"], fontSize=15, leading=18, spaceAfter=4)
_H2 = ParagraphStyle(
    "hb-h2", parent=_STYLES["Heading2"], fontSize=11, leading=13, spaceBefore=8, spaceAfter=3
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


def _para(text: str, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph(escape(text), style)


def _line_by_key(draft: AnlageVDraft) -> dict[str, DraftLine]:
    return {line.key: line for line in draft.lines}


def _sheet_label(row: FieldSheetRow, index: int, draft: AnlageVDraft, rule_set: RuleSet) -> str:
    form_line = rule_set.form.lines[row.key]
    if row.key == UNIT_ROWS:
        unit = draft.unit_rows[index]
        area = f"{unit.area_m2:.2f}".replace(".", ",")
        return f"{form_line.label}: {unit.label}, {area} m²"
    if row.key in ("result.owner_a", "result.owner_b"):
        n = 0 if row.key == "result.owner_a" else 1
        share = draft.owners[n].share if n < len(draft.owners) else None
        part = f" (Anteil {share.numerator}/{share.denominator})" if share else ""
        return f"{form_line.label}{part}"
    return form_line.label


def _row_notes(line: DraftLine | None, draft: AnlageVDraft) -> list[str]:
    if line is None:
        return []
    notes = []
    if line.einzelangaben:
        notes.append("Einzelangaben: " + ", ".join(line.einzelangaben))
    if line.key == "wk.afa" and draft.afa is not None and draft.afa.method_code is not None:
        method = "2 = laut Erläuterung (Anlage)" if draft.afa.method_code == 2 else "1 = linear"
        if draft.afa.method == "useful_life":  # D79: no rate, the remaining useful life
            rate = (
                f", kürzere Nutzungsdauer {draft.afa.useful_life_years} Jahre"
                f" ab {draft.afa.useful_life_from_year}"
            )
        else:
            rate = f", Satz {_percent(draft.afa.rate)}" if draft.afa.rate is not None else ""
        notes.append(f"Zeile 33: Methode {method}{rate}")
    if line.key == "wk.afa_movable":  # D80: Z42's method box, one entry per asset
        for asset in draft.movable_assets:
            if asset.method_code is not None:
                method = (
                    "2 = laut Erläuterung (Anlage)" if asset.method_code == 2 else "1 = wie Vorjahr"
                )
                notes.append(f"Zeile 42: Methode {method}")
                break
    if line.provisional:
        notes.append("vorläufig: ein Beleg fehlt noch")
    return notes


def _sources_count(row: FieldSheetRow, index: int, draft: AnlageVDraft, line) -> str:
    refs: Sequence[SourceRef] = (
        draft.unit_rows[index].sources if row.key == UNIT_ROWS else (line.sources if line else [])
    )
    return str(len(refs)) if refs else "–"


def _header_rows(draft: AnlageVDraft, rule_set: RuleSet) -> list[list]:
    h = draft.header
    lines = rule_set.form.lines

    def zeile(key: str) -> str:
        line = lines[key]
        if line.zeile_from is not None and line.zeile is not None:
            return f"{line.zeile_from}–{line.zeile}"
        return "" if line.zeile is None else str(line.zeile)

    address = (
        "–"
        if h.address is None
        else f"{h.address.street} {h.address.house_number}, {h.address.postal_code} "
        f"{h.address.city}"
    )
    area = None if h.living_area_m2 is None else f"{h.living_area_m2:.2f}".replace(".", ",")
    values = [
        ("header.address", address),
        ("header.aktenzeichen", h.aktenzeichen or "– (Grundsteuerbescheid fehlt)"),
        ("header.contract_date", _date(h.contract_date)),
        ("header.transfer_date", _date(h.transfer_date)),
        ("header.completed", str(h.completed)),
        ("header.sold", _date(h.sold) if h.sold else "– (nicht veräußert)"),
        ("header.living_area", f"{area} m²" if area else "–"),
    ]
    rows: list[list] = [["Zeile", "Kz", "Angabe", "Eintrag"]]
    for key, value in values:
        rows.append(
            [
                zeile(key),
                lines[key].kennzahl or "",
                _para(lines[key].label, _CELL),
                _para(value, _CELL),
            ]
        )
    return rows


def _amount_rows(draft: AnlageVDraft, rule_set: RuleSet) -> list[list]:
    by_key = _line_by_key(draft)
    rows: list[list] = [
        ["Zeile", "Kz", "Bezeichnung", "Betrag (Cent)", "Eintrag (volle Euro)", "Quellen"]
    ]
    unit_index = 0
    for row in field_sheet(draft, rule_set):
        index = unit_index if row.key == UNIT_ROWS else 0
        line = None if row.key == UNIT_ROWS else by_key.get(row.key)
        label = _sheet_label(row, index, draft, rule_set)
        notes = _row_notes(line, draft)
        text = escape(label) + "".join(f"<br/><i>{escape(n)}</i>" for n in notes)
        rows.append(
            [
                "" if row.zeile is None else str(row.zeile),
                row.kennzahl or "",
                Paragraph(text, _CELL),
                Paragraph(escape(_money(row.amount)), _NUM),
                Paragraph(f"<b>{escape(_euros(row.amount_rounded))}</b>", _NUM),
                _sources_count(row, index, draft, line),
            ]
        )
        if row.key == UNIT_ROWS:
            unit_index += 1
    return rows


def _ticked_rows(draft: AnlageVDraft) -> list[str]:
    return [
        f"Zeile {line.zeile} (Kz {line.kennzahl}): {line.label} – ankreuzen"
        if line.kennzahl
        else f"Zeile {line.zeile}: {line.label} – ankreuzen"
        for line in draft.lines
        if line.ticked
    ]


def _afa_annex(draft: AnlageVDraft, facts: DraftFacts) -> list:
    afa = draft.afa
    if afa is None:
        return []
    out: list = [_para("Anlage: Erläuterung zur AfA (Zeile 33)", _H2)]
    keep: list = []
    if afa.basis is None or facts.split is None:
        out.append(_para("Keine Kaufpreisaufteilung gewählt: die AfA bleibt leer."))
        return out
    prop = facts.property
    rows: list[list] = [["Posten", "Betrag"]]
    rows.append(["Kaufpreis", _money(prop.purchase_price)])
    cost_total = Decimal("0")
    for cost in facts.cost_rows:
        paid = f", bezahlt {_date(cost.paid_on)}" if cost.paid_on else ""
        rows.append([f"+ {COST_KIND[cost.kind]}{paid}", _money(cost.amount)])
        cost_total += cost.amount
    rows.append(["= Anschaffungskosten gesamt", _money(prop.purchase_price + cost_total)])
    share = facts.split.building_share
    rows.append(
        [
            f"× Gebäudeanteil {_percent(share, 4)} (Quelle: {SPLIT_SOURCE[facts.split.source]})",
            "",
        ]
    )
    if afa.movable_deducted:  # D80: the kitchen bought with the flat leaves the building
        building = cents((prop.purchase_price + cost_total) * share)
        rows.append(["= Anteil Gebäude", _money(building)])
        rows.append(
            [
                "− Einbauküche mit ihrem Anteil an den Nebenkosten (eigene AfA, Zeile 42–45)",
                _money(afa.movable_deducted),
            ]
        )
    rows.append(["= AfA-Bemessungsgrundlage Gebäude", _money(afa.basis)])
    if afa.additions:
        additions = {r.id for r in afa.sources if r.kind == "allocation"}
        for a in facts.allocations:
            if a.allocation_id in additions:
                rows.append(
                    [
                        f"+ nachträgliche Herstellungskosten, bezahlt {_date(a.booking_date)}",
                        _money(abs(a.amount)),
                    ]
                )
        # D137: a repair paid from another account that joined the basis (the 15 % crossing)
        private = {r.id: r for r in facts.tax_inputs}
        limit = draft.limit_15
        for b in [] if limit is None else limit.basis_additions:
            row = private.get(b.tax_input_id) if b.tax_input_id is not None else None
            if row is None or b.tax_year > draft.tax_year:
                continue
            label = getattr(row.value, "label", None) or "Kosten"
            rows.append(
                [
                    f"+ nachträgliche Herstellungskosten, von einem anderen Konto bezahlt: "
                    f"{label} (Steuerjahr {row.tax_year})",
                    _money(b.amount),
                ]
            )
        rows.append(["+ nachträgliche Herstellungskosten gesamt", _money(afa.additions)])
    if afa.method == "useful_life":  # D79: the book value over the remaining useful life
        rows.append(
            [
                "AfA nach kürzerer tatsächlicher Nutzungsdauer (§ 7 Abs. 4 S. 2 EStG): "
                f"{afa.useful_life_years} Jahre ab {afa.useful_life_from_year} laut Gutachten",
                "",
            ]
        )
        rows.append(
            [
                f"Restwert zu Jahresbeginn {draft.tax_year} (Bemessungsgrundlage abzüglich "
                "bisheriger AfA)",
                _money(afa.book_value_start),
            ]
        )
        rows.append(
            [
                f"× {afa.months} Monate / {afa.remaining_months} Monate Restnutzungsdauer "
                "zu Jahresbeginn (im letzten Jahr der ganze Restwert)",
                "",
            ]
        )
    else:
        rate = "–" if afa.rate is None else _percent(afa.rate)
        rows.append(
            [
                f"× AfA-Satz {rate} (fertig gestellt {prop.build_year}, § 7 Abs. 4 EStG) "
                f"× {afa.months}/12 Monate",
                "",
            ]
        )
    rows.append([f"= AfA {draft.tax_year}", _money(afa.amount)])
    table = Table(
        [[_para(str(c), _CELL) if i == 0 else c for i, c in enumerate(r)] for r in rows],
        colWidths=[130 * mm, 40 * mm],
        repeatRows=1,
    )
    table.setStyle(_GRID)
    table.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT")]))
    keep.append(table)
    keep.append(
        _para(
            f"Eigentumsübergang {_date(prop.purchase_date)}; Kaufvertrag "
            f"{_date(prop.notarized_on)}. Die Nebenkosten werden im Verhältnis der "
            "Kaufpreisaufteilung auf Gebäude und Grund verteilt.",
            _SMALL,
        )
    )
    return [KeepTogether(out + keep)]


MOVABLE_LABEL = {"einbaukueche": "Einbauküche"}


def _movable_annex(draft: AnlageVDraft, facts: DraftFacts) -> list:
    """D80: one table per movable asset with AfA this year: its basis, life and months."""
    out: list = []
    prop = facts.property
    cost_total = sum((c.amount for c in facts.cost_rows), Decimal("0"))
    for asset in draft.movable_assets:
        name = MOVABLE_LABEL.get(asset.label, asset.label)
        rows: list[list] = [["Posten", "Betrag"]]
        if asset.in_purchase_price:
            rows.append([f"{name}, Wert im Kaufpreis", _money(asset.price_amount)])
            rows.append(
                [
                    f"× (Kaufpreis {_money(prop.purchase_price)} + Nebenkosten "
                    f"{_money(cost_total)}) / Kaufpreis",
                    "",
                ]
            )
        else:
            rows.append([f"{name}, Anschaffungskosten", _money(asset.price_amount)])
        rows.append(["= AfA-Bemessungsgrundlage", _money(asset.basis)])
        rows.append(
            [
                f"Nutzungsdauer {asset.useful_life_years} Jahre ab {asset.from_year}; "
                f"Restwert zu Jahresbeginn {draft.tax_year}",
                _money(asset.book_value_start),
            ]
        )
        rows.append(
            [
                f"linear, {asset.months} Monate in {draft.tax_year} (im letzten Jahr der ganze "
                "Restwert)",
                "",
            ]
        )
        rows.append([f"= AfA {draft.tax_year}", _money(asset.amount)])
        table = Table(
            [[_para(str(c), _CELL) if i == 0 else c for i, c in enumerate(r)] for r in rows],
            colWidths=[130 * mm, 40 * mm],
            repeatRows=1,
        )
        table.setStyle(_GRID)
        table.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT")]))
        title = f"Anlage: Erläuterung zur AfA {name} (Zeile 42–45)"
        note = (
            "Bewegliches Wirtschaftsgut mit eigener AfA (§ 7 Abs. 1 EStG); im Kaufpreis "
            "enthalten, daher aus der Bemessungsgrundlage des Gebäudes herausgerechnet."
            if asset.in_purchase_price
            else "Bewegliches Wirtschaftsgut mit eigener AfA (§ 7 Abs. 1 EStG), gesondert "
            "angeschafft."
        )
        out.append(KeepTogether([_para(title, _H2), table, _para(note, _SMALL)]))
    return out


def _open_items(draft: AnlageVDraft) -> list[str]:
    out = []
    for item in draft.missing_documents:
        status = CHECKLIST_STATUS.get(item.status)
        if status is None:  # present
            continue
        due = f", fällig {_date(item.due_on)}" if item.due_on else ""
        out.append(f"{REQUIREMENT[item.code]}: {status}{due}")
    return out


def render_field_sheet(
    draft: AnlageVDraft,
    rule_set: RuleSet,
    facts: DraftFacts,
    stamp: ExportStamp,
    *,
    finanzamt_copies: int = 0,
) -> bytes:
    """The ELSTER-Ausfüllhilfe as PDF bytes; the same inputs give the same bytes."""
    title = f"ELSTER-Ausfüllhilfe Anlage V {draft.tax_year}"
    provisional = draft.rule_set.form_status is not FormStatus.PUBLISHED
    story: list = [_para(title, _H1)]
    story.append(_para(f"Formularstand: {form_status_text(draft, rule_set)}", _BODY))
    if provisional:
        story.append(
            Paragraph(
                "<b>Vorläufig:</b> vor dem Abtippen Zeilen und Kennzahlen mit dem Formular in "
                "ELSTER vergleichen.",
                _BODY,
            )
        )
    story.append(_para("Angaben zur Immobilie", _H2))
    header = Table(_header_rows(draft, rule_set), colWidths=[14 * mm, 10 * mm, 80 * mm, 66 * mm])
    header.setStyle(_GRID)
    story.append(header)

    story.append(_para("Einnahmen, Werbungskosten und Ergebnis", _H2))
    amounts = Table(
        _amount_rows(draft, rule_set),
        colWidths=[12 * mm, 10 * mm, 82 * mm, 26 * mm, 24 * mm, 16 * mm],
        repeatRows=1,
    )
    amounts.setStyle(_GRID)
    amounts.setStyle(TableStyle([("ALIGN", (5, 0), (5, -1), "RIGHT")]))
    story.append(amounts)
    story.append(
        _para(
            "Eintrag (volle Euro): Einnahmen abgerundet, Werbungskosten aufgerundet, Summen aus "
            "den gerundeten Zeilen. Quellen: Zahl der Zahlungen, Belege und Angaben, aus denen "
            "die Zeile in Hausbuch berechnet ist.",
            _SMALL,
        )
    )
    ticked = _ticked_rows(draft)
    if ticked:
        story.append(_para("Ankreuzen", _H2))
        story += [_para(t) for t in ticked]

    story += _afa_annex(draft, facts)
    story += _movable_annex(draft, facts)

    warnings = [warning_text(w) for w in draft.warnings]
    if finanzamt_copies:
        warnings.append(
            warning_text(
                DraftWarning(code=WarningCode.ARCHIVE_FINANZAMT_COPY_NEEDED, severity="info")
            )
            + f" Betrifft {finanzamt_copies} Dokument(e), siehe README.txt."
        )
    open_items = _open_items(draft)
    story.append(_para("Hinweise und offene Punkte", _H2))
    if not warnings and not open_items:
        story.append(_para("Keine."))
    story += [_para(f"– {text}") for text in warnings]
    story += [_para(f"– Unterlage {text}") for text in open_items]
    if draft.tax_free_from is not None:
        story.append(
            _para(f"§ 23 EStG: ein Verkauf ist steuerfrei ab {_date(draft.tax_free_from)}.")
        )

    created = _local(stamp.created_at)
    meta = [
        f"Formularstand: {form_status_text(draft, rule_set)}",
        f"Regelwerk {draft.rule_set.version} (Steuerjahr {draft.rule_set.tax_year})",
        f"Export {stamp.export_id}, erstellt {created:%d.%m.%Y %H:%M} Uhr",
        f"SHA-256 des Entwurfs: {draft_sha256(draft)}",
    ]
    story.append(
        KeepTogether(
            [_para("Herkunft", _H2), *(_para(m, _SMALL) for m in meta), Spacer(1, 4)]
            + [_para(DISCLAIMER, _BODY)]
        )
    )

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
        subject=f"Anlage V {draft.tax_year}",
        invariant=1,
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


# --- The CSVs ----------------------------------------------------------------------------


def _csv(rows: Sequence[Sequence[str]]) -> bytes:
    """Semicolon-separated UTF-8 with BOM (opens in a German spreadsheet), CRLF lines."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerows(rows)
    return ("﻿" + out.getvalue()).encode("utf-8")


def lines_csv(draft: AnlageVDraft, rule_set: RuleSet) -> bytes:
    """anlage-v-<year>.csv: one row per field sheet row (key, Zeile, Kennzahl, cents, rounded)."""
    rows = [["Schluessel", "Zeile", "Kennzahl", "Betrag", "Eintrag_volle_Euro"]]
    for row in field_sheet(draft, rule_set):
        rows.append(
            [
                row.key,
                "" if row.zeile is None else str(row.zeile),
                row.kennzahl or "",
                _plain(row.amount),
                str(row.amount_rounded),
            ]
        )
    return _csv(rows)


def _where(draft: AnlageVDraft) -> dict[UUID, str]:
    """Allocation → where the draft counts it: the first line citing it, else a block."""
    out: dict[UUID, str] = {}
    for unit in draft.unit_rows:
        for ref in unit.sources:
            if ref.kind == "allocation" and ref.id is not None:
                out.setdefault(ref.id, UNIT_ROWS)
    for line in draft.lines:
        for ref in line.sources:
            if ref.kind == "allocation" and ref.id is not None:
                out.setdefault(ref.id, line.key)
    for tracked in draft.tracked_not_deductible:
        for ref in tracked.sources:
            if ref.kind == "allocation" and ref.id is not None:
                out.setdefault(ref.id, "nicht abziehbar")
    for moved in draft.moved_to_other_year:
        out.setdefault(moved.allocation_id, f"Steuerjahr {moved.tax_year}")
    for ref in draft.pre_handover:
        if ref.kind == "allocation" and ref.id is not None:
            out.setdefault(ref.id, "vor dem Übergang")
    if draft.afa is not None:
        for ref in draft.afa.sources:
            if ref.kind == "allocation" and ref.id is not None:
                out.setdefault(ref.id, "wk.afa")
    return out


def year_allocations(draft: AnlageVDraft, facts: DraftFacts) -> list:
    """The payments zahlungen.csv lists: every confirmed allocation booked in the tax year or
    counted by the draft, in booking order."""
    cited = _where(draft)
    listed = [
        a
        for a in facts.allocations
        if a.booking_date.year == draft.tax_year or a.allocation_id in cited
    ]
    return sorted(listed, key=lambda a: (a.booking_date, str(a.allocation_id)))


def payments_csv(draft: AnlageVDraft, facts: DraftFacts, beleg_numbers: dict[UUID, str]) -> bytes:
    """zahlungen.csv: booking date, amount, category, where it counts, period, the Beleg's
    number in dokumente/ and the counterparty as a masked IBAN only (D5)."""
    where = _where(draft)
    rows = [
        [
            "Buchungstag",
            "Betrag",
            "Kategorie",
            "Kategorie_Code",
            "Zeile_Schluessel",
            "Zeitraum_von",
            "Zeitraum_bis",
            "Beleg_Nr",
            "Gegenkonto",
        ]
    ]
    for a in year_allocations(draft, facts):
        period = a.service_period
        rows.append(
            [
                a.booking_date.isoformat(),
                _plain(a.amount),
                CATEGORY_LABEL[a.category],
                a.category.value,
                where.get(a.allocation_id, NOT_ON_FORM),
                "" if period is None else period.start.isoformat(),
                "" if period is None else period.end.isoformat(),
                "" if a.document_id is None else beleg_numbers.get(a.document_id, ""),
                a.counterparty_iban_masked or "",
            ]
        )
    return _csv(rows)


# --- Documents and the archive ------------------------------------------------------------


def archive_document_ids(draft: AnlageVDraft, facts: DraftFacts) -> list[UUID]:
    """The originals the archive carries: every document the checklist found present for the
    year, every document a draft line, unit row, the AfA or a warning cites, and the Beleg of
    every payment zahlungen.csv lists. In first-seen order, each once."""
    seen: dict[UUID, None] = {}

    def add(refs: Sequence[SourceRef]) -> None:
        for ref in refs:
            if ref.kind == "document" and ref.id is not None:
                seen.setdefault(ref.id)

    for item in draft.missing_documents:
        if item.status is ChecklistStatus.PRESENT:
            for document_id in item.documents:
                seen.setdefault(document_id)
    for line in draft.lines:
        add(line.sources)
    for unit in draft.unit_rows:
        add(unit.sources)
    if draft.afa is not None:
        add(draft.afa.sources)
    for warning in draft.warnings:
        add(warning.refs)
    for a in year_allocations(draft, facts):
        if a.document_id is not None:
            seen.setdefault(a.document_id)
    return list(seen)


_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")
_NAME_MAX = 150


def safe_filename(name: str) -> str:
    """A name that stays one entry inside dokumente/: no separators, no control characters,
    no leading dot."""
    cleaned = " ".join(_UNSAFE.sub(" ", name).split()).lstrip(". ")
    if len(cleaned) > _NAME_MAX:
        stem, dot, ext = cleaned.rpartition(".")
        cleaned = (stem[: _NAME_MAX - len(ext) - 1] + dot + ext) if dot else cleaned[:_NAME_MAX]
    return cleaned or "dokument"


@dataclass(frozen=True)
class ArchiveDocument:
    document_id: UUID
    number: str  # "001": the Beleg number zahlungen.csv cites
    filename: str  # phase 2's download name, made safe
    data: bytes
    finanzamt_copy: FinanzamtCopy | None = None

    @property
    def path(self) -> str:
        return f"dokumente/{self.number} {self.filename}"


def annotation_subtypes(data: bytes) -> set[str]:
    """The annotation subtypes on the PDF's pages ("/Ink", …); empty for anything pypdf cannot
    read."""
    from pypdf import PdfReader

    found: set[str] = set()
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return found
        for page in reader.pages:
            annots = page.get("/Annots")
            if annots is None:
                continue
            for annot in annots.get_object():
                subtype = annot.get_object().get("/Subtype")
                if subtype is not None:
                    found.add(str(subtype))
    except Exception:  # pypdf raises a wide range of errors on damaged files
        return set()
    return found


_COPY_ANNOTATIONS = frozenset({"/Ink", "/FreeText", "/Stamp"})


def finanzamt_copy(data: bytes, media_type: str) -> FinanzamtCopy | None:
    """Why the document needs a copy for the Finanzamt (Q8): an XFA form only Adobe draws, or
    ink, free text or stamp annotations some viewers do not draw. None otherwise."""
    from hausbuch.ai.documents import is_adobe_only_form

    if media_type != PDF_MEDIA_TYPE:
        return None
    if is_adobe_only_form(data):
        return "xfa"
    if annotation_subtypes(data) & _COPY_ANNOTATIONS:
        return "annotations"
    return None


def _file_list(entries: Sequence[tuple[str, bytes]]) -> list[str]:
    return [f"  {path}  SHA-256 {hashlib.sha256(data).hexdigest()}" for path, data in entries]


def readme_text(
    draft: AnlageVDraft,
    rule_set: RuleSet,
    stamp: ExportStamp,
    *,
    property_label: str,
    files: Sequence[tuple[str, bytes]],
    documents: Sequence[ArchiveDocument],
    banner: Sequence[str] = (),
    notes: Sequence[str] = (),
    income_tax: bool = False,
) -> bytes:
    created = _local(stamp.created_at)
    h = draft.header
    address = (
        "–"
        if h.address is None
        else f"{h.address.street} {h.address.house_number}, {h.address.postal_code} "
        f"{h.address.city}"
    )
    copies = [d for d in documents if d.finanzamt_copy is not None]
    lines = [
        f"Hausbuch – Jahresarchiv Anlage V {draft.tax_year}",
        "",
        *(f"Hinweis: {text}" for text in banner),
        *([""] if banner else []),
        f"Immobilie: {property_label}, {address}",
        f"Steuerjahr: {draft.tax_year}",
        f"Formularstand: {form_status_text(draft, rule_set)}",
        f"Regelwerk: {draft.rule_set.version}",
        f"Export: {stamp.export_id}",
        f"Erstellt: {created:%d.%m.%Y %H:%M} Uhr (Europe/Berlin)",
        f"SHA-256 des Entwurfs: {draft_sha256(draft)}",
        f"Ergebnis Zeile 85: {_money(draft.totals.result)} "
        f"(Eintrag {_euros(draft.totals.result_rounded)} €)",
        "§ 23 EStG: steuerfrei ab "
        + (_date(draft.tax_free_from) if draft.tax_free_from else "– (kein Kaufvertragsdatum)"),
        "",
        "Inhalt",
        *_file_list(files),
        f"  dokumente/  {len(documents)} Originale, unverändert",
        *(f"    {d.path}  SHA-256 {hashlib.sha256(d.data).hexdigest()}" for d in documents),
        "",
        "Für das Finanzamt eine Kopie nötig",
    ]
    if copies:
        lines += [f"  {d.path}: {COPY_REASON[d.finanzamt_copy or 'xfa']}" for d in copies]
        lines.append(
            "  So geht es: das Dokument in Adobe Reader öffnen und als PDF drucken; die Kopie "
            "dem Finanzamt senden, das Original hier bleibt unverändert."
        )
    else:
        lines.append("  keine")
    if income_tax:  # phase 4b D104: the owners' Anlage N sheets and the Gesamt page
        lines += [
            "",
            "Einkommensteuer (Anlage N je Eigentümer, Gesamtschätzung des Haushalts)",
            "  Die Gesamtschätzung ist eine Schätzung, kein Bescheid.",
            *(f"  {text}" for text in notes),
        ]
    lines += [
        "",
        "zahlungen.csv nennt den Beleg einer Zahlung mit seiner Nummer in dokumente/ und die "
        "Gegenseite nur mit maskierter IBAN.",
        "CSV-Format: Semikolon, Dezimalkomma, UTF-8.",
        "",
        DISCLAIMER,
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")


def _zip_time(created_at: datetime) -> tuple[int, int, int, int, int, int]:
    local = _local(created_at)
    if local.year < 1980:  # the ZIP format starts in 1980
        return (1980, 1, 1, 0, 0, 0)
    return (
        local.year,
        local.month,
        local.day,
        local.hour,
        local.minute,
        local.second - local.second % 2,
    )


def _add(archive: zipfile.ZipFile, path: str, data: bytes, when, *, stored: bool) -> None:
    info = zipfile.ZipInfo(path, date_time=when)
    info.create_system = 3  # the default depends on the platform
    info.external_attr = 0o644 << 16
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    archive.writestr(info, data, compresslevel=None if stored else 6)


def archive_filename(tax_year: int, property_label: str) -> str:
    return safe_filename(f"anlage-v-{tax_year}-{property_label}.zip")


def render_archive(
    draft: AnlageVDraft,
    rule_set: RuleSet,
    facts: DraftFacts,
    stamp: ExportStamp,
    *,
    property_label: str,
    documents: Sequence[ArchiveDocument],
    extra_files: Sequence[tuple[str, bytes]] = (),
    banner: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> bytes:
    """The yearly archive (ZIP): ausfuellhilfe.pdf, anlage-v-<year>.csv, zahlungen.csv,
    README.txt, the `extra_files` (phase 4b D104: each owner's Anlage N sheet and the Gesamt
    page, already rendered) and dokumente/ with the untouched originals (stored, not
    recompressed). `banner`: lines printed at the top of README.txt (the open Jahresfragen);
    `notes`: its Einkommensteuer section (what was left out and why)."""
    copies = sum(1 for d in documents if d.finanzamt_copy is not None)
    sheet = render_field_sheet(draft, rule_set, facts, stamp, finanzamt_copies=copies)
    numbers = {d.document_id: d.number for d in documents}
    files = [
        ("ausfuellhilfe.pdf", sheet),
        (f"anlage-v-{draft.tax_year}.csv", lines_csv(draft, rule_set)),
        ("zahlungen.csv", payments_csv(draft, facts, numbers)),
    ]
    readme = readme_text(
        draft,
        rule_set,
        stamp,
        property_label=property_label,
        files=[*files, *extra_files],
        documents=documents,
        banner=banner,
        notes=notes,
        income_tax=bool(extra_files or notes),
    )
    when = _zip_time(stamp.created_at)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files:
            _add(archive, path, data, when, stored=False)
        _add(archive, "README.txt", readme, when, stored=False)
        for path, data in extra_files:
            _add(archive, path, data, when, stored=False)
        for document in documents:
            _add(archive, document.path, document.data, when, stored=True)
    return buffer.getvalue()
