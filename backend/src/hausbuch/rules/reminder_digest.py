"""The reminder digest's text (docs/phases/phase-5.md D123): one German line per reminder and
the mail around them. Pure: reminders in, subject, text and HTML out.

The lines are the Zu prüfen and Übersicht cards' own wording (`reminderLabel` in
frontend/src/components/reminders.tsx), with the date in front; a checklist line also names
its property, which the card shows beside it. A reminder's label arguments are dates, a
month, a year, or a property, unit, account or lender label as the user typed it (D5, D122),
so a line never carries an amount, a tenant's name or an address field; the mail adds only
fixed text and the link to the Übersicht, where the detail is.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from html import escape

from hausbuch.schemas.reminders import Reminder

SUBJECT = "Hausbuch: Erinnerungen"

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
_CHECKLIST_DOCUMENTS = {
    "grundsteuerbescheid": "Grundsteuerbescheid",
    "zinsbescheinigung": "Zinsbescheinigung",
    "wirtschaftsplan": "Wirtschaftsplan",
    "hausgeldabrechnung": "Hausgeldabrechnung",
}
_CHECKLIST_PREFIX = "checklist.due."
FALLBACK_LABEL = "Termin"


@dataclass(frozen=True)
class Digest:
    subject: str
    text: str
    html: str


def german_date(value: date | str) -> str:
    """2027-02-15 → 15.02.2027 (formatDate); text that is not a date stays as it is."""
    try:
        day = value if isinstance(value, date) else date.fromisoformat(value)
    except ValueError:
        return str(value) or "–"
    return f"{day.day:02d}.{day.month:02d}.{day.year:04d}"


def german_month(value: str) -> str:
    """2026-03 → März 2026 (formatMonth); anything else stays as it is."""
    year, _, month = value.partition("-")
    if year.isdigit() and month[:2].isdigit() and 1 <= int(month[:2]) <= 12:
        return f"{_MONTHS[int(month[:2]) - 1]} {year}"
    return value or "–"


def reminder_label(reminder: Reminder) -> str:
    """The reminder in one German line, from its label_code and label_args (§6.2)."""
    args = reminder.label_args

    def arg(name: str) -> str:
        value = args.get(name)
        return "" if value is None else str(value)

    code = reminder.label_code
    year = arg("year")
    if code.startswith(_CHECKLIST_PREFIX):
        name = _CHECKLIST_DOCUMENTS.get(code[len(_CHECKLIST_PREFIX) :], "Unterlage")
        whose = arg("lender") or arg("property")
        return f"{name} für {year} erwartet" + (f" ({whose})" if whose else "")
    match code:
        case "grundsteuer.instalment.remind":
            return f"Grundsteuer {arg('property')} überweisen"
        case "grundsteuer.instalment.sepa_mandate":
            return f"Grundsteuer {arg('property')}: Lastschrift prüfen"
        case "tax.return_deadline":
            return f"Steuererklärung {year} abgeben"
        case "tax.questionnaire_open":
            return f"Jahresfragen {year} beantworten"
        case "tax.wage_certificate_missing":
            return f"Lohnsteuerbescheinigung {year} fehlt"
        case "loan.fixed_rate_end":
            return (
                f"Zinsbindung {arg('lender')} endet am {german_date(arg('fixed_until'))}: "
                "Anschlussfinanzierung prüfen"
            )
        case "loan.special_termination":
            return (
                f"{arg('lender')}: Kündigung nach zehn Jahren möglich, prüfen, ob eine "
                "Umschuldung lohnt"
            )
        case "property.speculation_end":
            return f"{arg('property')}: Verkauf steuerfrei ab {german_date(arg('tax_free_from'))}"
        case "tenancy.settlement_deadline":
            return f"Nebenkostenabrechnung {year} für {arg('unit')} zustellen"
        case "tenancy.rent_missing":
            return f"Miete {arg('unit')} für {german_month(arg('month'))} fehlt"
        case "bank.sync_stale":
            return f"Bankabruf {arg('account')} lange nicht erfolgreich"
    return FALLBACK_LABEL


def digest_lines(reminders: Iterable[Reminder]) -> list[str]:
    """One line per reminder, by due day and id: "15.02.2027: Grundsteuer … überweisen"."""
    ordered = sorted(reminders, key=lambda r: (r.due_on, r.id))
    return [f"{german_date(r.due_on)}: {reminder_label(r)}" for r in ordered]


def render_digest(reminders: Iterable[Reminder], app_url: str) -> Digest:
    """The mail for one member: the lines, the link to the Übersicht, and how to stop it."""
    lines = digest_lines(reminders)
    if not lines:
        raise ValueError("a digest lists at least one reminder")
    intro = "Guten Tag,\n\nin Hausbuch stehen diese Termine an:"
    detail = "Was zu tun ist und alle Einzelheiten stehen auf der Übersicht:"
    footer = (
        "Diese E-Mail kommt höchstens einmal am Tag, wenn ein Termin ansteht. Abschalten "
        "lässt sie sich in Hausbuch unter Einstellungen, „Erinnerungen per E-Mail“."
    )
    text = "\n".join(
        [intro, "", *(f"- {line}" for line in lines), "", detail, app_url, "", footer, ""]
    )
    items = "".join(f"<li>{escape(line)}</li>" for line in lines)
    url = escape(app_url, quote=True)
    html = (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        f"<title>{escape(SUBJECT)}</title></head>"
        '<body style="font-family: sans-serif; line-height: 1.5;">'
        "<p>Guten Tag,</p><p>in Hausbuch stehen diese Termine an:</p>"
        f"<ul>{items}</ul>"
        f'<p>{escape(detail)}<br><a href="{url}">Übersicht öffnen</a></p>'
        f'<p style="color: #666; font-size: 0.9em;">{escape(footer)}</p>'
        "</body></html>"
    )
    return Digest(subject=SUBJECT, text=text, html=html)
