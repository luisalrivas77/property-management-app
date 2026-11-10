"""Phase 4b step 4: the Anlage N field sheet and the Gesamt page rendered from the phase 4b
fixtures (docs/phases/phase-4b.md D104, D110). The sheet prints every form line with its Zeile
and Kz (a Kz on two Zeilen stays on both), counts without "€", the provisional banner, the
open Jahresfragen and a last page of reminders; both files are byte-for-byte reproducible. No
database: services/tax_export_anlage_n.py is pure."""

import io
import re
from datetime import UTC, datetime
from uuid import UUID

from fixture_models_phase4b import current, load
from pypdf import PdfReader

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.draft import draft_n
from hausbuch.rules.tax.return_estimate import return_estimate
from hausbuch.schemas.employment import PropertyIncome
from hausbuch.services.tax_export_anlage_n import (
    anlage_n_sheet_filename,
    estimate_page_filename,
    questions_open_text,
    render_anlage_n_sheet,
    render_estimate_page,
)
from hausbuch.services.tax_export_files import ExportStamp

STAMP = ExportStamp(UUID(int=7), datetime(2027, 1, 15, 9, 30, tzinfo=UTC))
RULES = rules_for(2026)
REMINDER = "Berufliche Umzugskosten von Hand in der Anlage N eintragen."


def _draft(case_id: str):
    case = next(c for c in load("anlage_n").cases if c.id == case_id)
    return draft_n(case.facts, 2026, RULES, registry.tariff(2026))


def _sheet(case_id: str, **kwargs) -> bytes:
    return render_anlage_n_sheet(_draft(case_id), RULES, STAMP, owner_name="Person B", **kwargs)


def _pages(data: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]


def test_every_form_line_prints_with_its_zeile_and_kz():
    draft = _draft("owner_b_commute_day_rate")
    text = "\n".join(_pages(_sheet("owner_b_commute_day_rate")))
    assert "Eigentümer: Person B · Steuerjahr 2026" in text
    for line in draft.lines:
        if line.zeile is None:
            continue
        assert f"\n{line.zeile}\n" in text, line.key
        if line.kennzahl is not None:
            assert f"\n{line.kennzahl}\n" in text, line.key
    # Kz 110 stands on Z5 (Bruttoarbeitslohn) and on Z29 (days on the commute): never merged.
    assert re.search(r"\n5\n110\nBruttoarbeitslohn", text)
    assert re.search(r"\n29\n110\nArbeitsweg 1: Arbeitstage", text)
    # Pay to the cent, counts as plain numbers.
    assert "32.000,00 €" in text
    days = text.split("Arbeitsweg 1: Arbeitstage", 1)[1].split("\n")
    assert days[2].strip() == "100" and "€" not in days[2]


def test_the_sheet_is_marked_provisional_and_ends_with_the_reminders():
    pages = _pages(_sheet("owner_a_remote_work_room", reminders=[REMINDER]))
    assert "VORLÄUFIG" in pages[0]
    assert "Von Hand in ELSTER" in pages[-1] and REMINDER in pages[-1]
    assert "keine Steuerberatung" in "\n".join(pages)
    no_reminders = _pages(_sheet("owner_a_remote_work_room"))
    assert "Von Hand in ELSTER" in no_reminders[-1] and REMINDER not in no_reminders[-1]


def test_the_open_jahresfragen_are_a_banner():
    text = "\n".join(_pages(_sheet("owner_a_remote_work_room", questions_open=3)))
    assert "3 Jahresfragen offen" in text
    assert "Jahresfragen offen" not in "\n".join(_pages(_sheet("owner_a_remote_work_room")))
    assert questions_open_text(1).startswith("1 Jahresfrage offen")


def test_the_sheet_is_reproducible():
    one = _sheet("owner_a_remote_work_room", reminders=[REMINDER], questions_open=2)
    two = _sheet("owner_a_remote_work_room", reminders=[REMINDER], questions_open=2)
    assert one == two


def _estimate():
    case = next(c for c in load("return_estimate").cases if c.id == "household_2026")
    return case, return_estimate(
        case.tax_year,
        case.owners,
        case.resolved_owners(),
        current(case.household),
        [PropertyIncome(property_id=p.property_id, result=p.result) for p in case.properties],
        case.questionnaire,
        current(case.answers),
        registry.tariff(case.tax_year),
        RULES,
    )


def test_the_gesamt_page_prints_the_estimate():
    case, estimate = _estimate()
    names = {o.owner_id: f"Person {i}" for i, o in enumerate(case.owners)}
    labels = {p.property_id: "Wohnung" for p in case.properties}
    data = render_estimate_page(estimate, STAMP, owner_names=names, property_labels=labels)
    text = "\n".join(_pages(data))
    assert "Einkommensteuer 2026 · Gesamt (Schätzung)" in text
    assert "62.033" in text  # the zvE of the worked example
    assert "12.400,00 €" in text  # withheld
    assert "Jahresfragen 2026:" in text
    assert data == render_estimate_page(estimate, STAMP, owner_names=names, property_labels=labels)


def test_the_file_names():
    assert anlage_n_sheet_filename(2026, "Person A") == "Anlage-N_2026_Person A.pdf"
    assert estimate_page_filename(2026) == "Gesamt_2026_Schaetzung.pdf"
