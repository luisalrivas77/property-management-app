"""The figures' own sentences in English (phase 10 D284, step 4c, services/read_texts.py): the
key figures' marks, the debt gaps, the plan's comparison, the net worth's notes, the Budget's
marks and reference points and the categories named in a to-do. Every German text a producer
writes matches its catalogue, English comes back under `en`, and German is unchanged without a
header."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from hausbuch import i18n
from hausbuch.rules.budget import budget
from hausbuch.rules.budget.references import references
from hausbuch.rules.categories import CATEGORY_LABEL
from hausbuch.rules.health import bands
from hausbuch.rules.plan import spending
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.budget import BudgetView
from hausbuch.schemas.health import DebtFigure, DebtGap, Light, MarkedFigure, Window
from hausbuch.schemas.household import HouseholdGroup
from hausbuch.schemas.net_worth import NetWorthClass, NetWorthLine, NetWorthLineKind
from hausbuch.schemas.plan import HouseholdLevers, SpendingChange, SpendingComparisonRow
from hausbuch.services import net_worth, plan, read_texts, todo_texts
from hausbuch.services.category_texts import CATEGORY_LABEL_EN, english_label

GERMAN_MARKS = ("ä", "ö", "ü", "ß", "„", " der ", " die ", " und ", " ist ", " nicht ", "eure")


def _german_free(text: str) -> bool:
    return not any(mark in f" {text} " for mark in GERMAN_MARKS)


# --- The key figures' marks ------------------------------------------------------------------

SLOTS = {
    "reserve": bands.format_money(Decimal("9990")),
    "reserve_target": bands.format_money(Decimal("12000")),
    "reserve_gap": bands.format_money(Decimal("2010")),
    "reserve_months": bands.format_months(Decimal("3.8")),
    "band_reserve_red": bands.format_money(Decimal("4000")),
}


def _slots(figure: MarkedFigure) -> dict[str, str]:
    fmt = bands.format_multiple if figure is MarkedFigure.DEBT_TO_INCOME else bands.format_percent
    out = dict(SLOTS)
    for name in (
        "savings_rate",
        "savings_rate_regular",
        "ltv",
        "debt_service_ratio",
        "debt_ratio",
        "debt_to_income",
    ):
        out[name] = fmt(Decimal("0.7388") if fmt is bands.format_percent else Decimal("3.2"))
    for band in (
        "band_savings_rate",
        "band_ltv",
        "band_debt_service",
        "band_debt_ratio",
        "band_debt_to_income",
    ):
        for side in ("green", "red"):
            out[f"{band}_{side}"] = fmt(
                Decimal("0.2") if fmt is bands.format_percent else Decimal("4")
            )
    return out


MARK_CASES = [(MarkedFigure(code.split(".")[0]), code) for code in sorted(bands.proposals()[2])]


@pytest.mark.parametrize(("figure", "code"), MARK_CASES, ids=[c for _, c in MARK_CASES])
def test_every_mark_sentence_has_english(figure, code):
    german = bands.render(bands.proposals()[2][code], _slots(figure))
    light = Light(figure=figure, texts=[german])
    with i18n.use(i18n.DE):
        assert todo_texts.english(light).texts == [german]
    with i18n.use(i18n.EN):
        english = todo_texts.english(light).texts[0]
    assert english != german
    assert _german_free(english), english
    assert "€9,990" in english or "%" not in german or "%" in english


def test_mark_formats_follow_english():
    figure = MarkedFigure.RESERVE
    german = bands.render(bands.proposals()[2]["reserve.amber"], _slots(figure))
    with i18n.use(i18n.EN):
        english = todo_texts.english(Light(figure=figure, texts=[german])).texts[0]
    assert english == (
        "Your reserve lasts for 3.8 months. €2,010 are still missing to reach your own target."
    )
    german = bands.render(
        bands.proposals()[2]["debt_to_income.green"], _slots(MarkedFigure.DEBT_TO_INCOME)
    )
    with i18n.use(i18n.EN):
        light = Light(figure=MarkedFigure.DEBT_TO_INCOME, texts=[german])
        assert todo_texts.english(light).texts == ["That is below your limit of 4.0."]


def test_rule_files_hold_no_english():
    """The English sits in services/, never under the folders rule_version() hashes."""
    root = Path(bands.__file__).parent.parent
    for folder in ("health", "plan"):
        for path in (root / folder).rglob("*"):
            if path.suffix in (".py", ".yaml"):
                assert "Your reserve" not in path.read_text("utf-8")


# --- The debt gaps -------------------------------------------------------------------------

DEBT_REASONS = [
    "Der geschätzte Wert der Immobilie ist 0 €; der Anteil der Kredite lässt sich nicht berechnen.",
    "Das Vermögen ohne Schulden ist 0 € oder weniger; der Verschuldungsgrad lässt sich nicht "
    "berechnen.",
    "Das Jahreseinkommen aus dem Zeitraum ist 0 € oder weniger; die Kennzahl lässt sich nicht "
    "berechnen.",
]


def test_debt_reasons_are_the_rules_own():
    source = Path(bands.__file__).with_name("figures.py").read_text("utf-8")
    flat = re.sub(r'"\s*\n\s*"', "", source)
    for reason in DEBT_REASONS:
        assert reason in flat


@pytest.mark.parametrize("reason", DEBT_REASONS)
def test_every_debt_gap_has_english(reason):
    gap = DebtGap(figure=DebtFigure.LTV, reason=reason)
    with i18n.use(i18n.DE):
        assert todo_texts.english(gap).reason == reason
    with i18n.use(i18n.EN):
        english = todo_texts.english(gap).reason
    assert english != reason and _german_free(english)


# --- The plan's comparison -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("difference", "expected"),
    [
        (Decimal("0.40"), "as planned"),
        (Decimal("1234.50"), "€1,235 more than planned"),
        (Decimal("-12.49"), "€12 less than planned"),
    ],
)
def test_comparison_words(difference, expected):
    words, text = spending.words(difference)
    row = SpendingComparisonRow(
        code=C.HH_STREAMING,
        group=HouseholdGroup.SUBSCRIPTIONS,
        planned=Decimal("100.00"),
        actual=Decimal("100.00") + difference,
        difference=difference,
        words=words,
        text=text,
    )
    with i18n.use(i18n.DE):
        assert todo_texts.english(row).text == text
    with i18n.use(i18n.EN):
        assert todo_texts.english(row).text == expected


# --- The net worth's notes and labels ------------------------------------------------------

NOTES = [
    "Kurs aus dem letzten Bericht",
    "laut Depotbericht vom 30.09.2026 1.234,5 Anteile, keiner gebucht",
    "Schätzwert: „Wert heute“ aus dem Szenario „Basis“ (Planung)",
    "Schätzwert: der Kaufpreis; einen aktuellen Wert unter Planung als „Wert heute“ eintragen",
    "Restschuld laut Tilgungsplan",
    net_worth.STORED_PROPERTY_NOTE,
    net_worth.STORED_LOAN_NOTE,
    net_worth.advance_note(date(2026, 7, 1)),
    net_worth.payout_note(date(2026, 7, 1)),
    f"{net_worth.advance_note(date(2026, 7, 1))}; zum Monatsende gespeichert",
    f"{net_worth.payout_note(date(2026, 7, 1))}; zum Monatsende gespeichert",
]


def _line(kind: NetWorthLineKind, label: str, note: str | None) -> NetWorthLine:
    return NetWorthLine(
        kind=kind,
        net_worth_class=NetWorthClass.PROPERTY,
        label=label,
        value_eur=Decimal("1.00"),
        note=note,
    )


@pytest.mark.parametrize("note", NOTES)
def test_every_net_worth_note_has_english(note):
    line = _line(NetWorthLineKind.PROPERTY, "Wohnung", note)
    with i18n.use(i18n.DE):
        assert todo_texts.english(line) is line or todo_texts.english(line) == line
    with i18n.use(i18n.EN):
        english = todo_texts.english(line).note
    assert english != note and _german_free(english), english


def test_net_worth_labels():
    advance = _line(NetWorthLineKind.PROPERTY, net_worth.advance_label("Wohnung"), None)
    account = _line(NetWorthLineKind.ACCOUNT, "Wertpapier", None)  # a typed account name
    with i18n.use(i18n.EN):
        assert todo_texts.english(advance).label == "Purchase price paid in advance – Wohnung"
        assert todo_texts.english(account).label == "Wertpapier"
        position = _line(NetWorthLineKind.POSITION, "Wertpapier", None)
        assert todo_texts.english(position).label == "Security"
    with i18n.use(i18n.DE):
        assert todo_texts.english(advance).label == "Anzahlung Kaufpreis – Wohnung"


# --- The Budget ----------------------------------------------------------------------------


def _marks():
    income = Decimal("4000.00")
    out = []
    for label in (*budget.LABELS.values(), budget.HOUSING_LABEL):
        for minimum in (False, True):
            for share, target in (
                (Decimal("0.5"), Decimal("50")),  # exactly on
                (Decimal("0.45"), Decimal("50")),  # under
                (Decimal("0.55"), Decimal("50")),  # over
            ):
                out.append(
                    budget.mark(label, share, share * income, income, target, minimum=minimum)
                )
    return out


@pytest.mark.parametrize("mark", _marks(), ids=lambda m: m.text)
def test_every_budget_mark_has_english(mark):
    with i18n.use(i18n.DE):
        assert todo_texts.english(mark).text == mark.text
    with i18n.use(i18n.EN):
        english = todo_texts.english(mark).text
    assert english != mark.text and _german_free(english), english


def test_budget_mark_reads_naturally():
    mark = budget.mark("Wünsche", Decimal("0.35"), Decimal("1400"), Decimal("4000"), Decimal("30"))
    assert mark.text == (
        "Wünsche: 35,0 % eures Einkommens, 5,0 Prozentpunkte über eurem Ziel von höchstens "
        "30,0 %; das sind 200 € im Monat zu viel."
    )
    with i18n.use(i18n.EN):
        assert todo_texts.english(mark).text == (
            "Wants: 35.0% of your income, 5.0 percentage points above your target of at most "
            "30.0%; that is €200 a month too much."
        )


def test_no_shares_and_references():
    view = BudgetView(
        as_of=date(2026, 11, 20),
        window=Window(months=[], enough=False),
        no_shares=budget.NO_SHARES,
        references=list(references()[1]),
    )
    with i18n.use(i18n.DE):
        assert todo_texts.english(view) == view
    with i18n.use(i18n.EN):
        english = todo_texts.english(view)
    assert english.no_shares is not None and _german_free(english.no_shares)
    for ref in english.references:
        for text in (ref.label, ref.measures, ref.does_not_measure):
            assert _german_free(text), text
        for band in ref.thresholds:
            assert _german_free(band.label), band.label
    assert view.no_shares == budget.NO_SHARES  # the German view is not changed in place


def test_every_reference_has_english():
    rows = references()[1]
    assert {r.key for r in rows} == set(read_texts.REFERENCES)
    labels = {t.label for r in rows for t in r.thresholds}
    assert labels == set(read_texts.THRESHOLDS)


# --- Categories inside a to-do -------------------------------------------------------------


def test_every_category_has_english():
    assert set(CATEGORY_LABEL_EN) == set(C)
    assert all(english_label(CATEGORY_LABEL[c]) == CATEGORY_LABEL_EN[c] for c in C)
    assert english_label("Streaming") == "Streaming"  # not a category name: unchanged


def test_plan_todo_names_the_category_in_english():
    levers = HouseholdLevers(
        spending_changes=[
            SpendingChange(code=C.HH_STREAMING, percent="-0.10", from_month="2026-12")
        ]
    )
    [todo] = plan.spending_todos(levers, {})
    assert "„Streaming (Video)“" in todo.text
    with i18n.use(i18n.EN):
        swapped = todo_texts.english(todo)
    assert "“Streaming (video)”" in swapped.text
    assert swapped.action == "Check scenario"


def test_shape_is_kept():
    gap = DebtGap(figure=DebtFigure.LTV, reason=DEBT_REASONS[0])
    with i18n.use(i18n.EN):
        swapped = todo_texts.english({"gaps": [gap], "id": uuid4()})
    assert set(swapped) == {"gaps", "id"} and swapped["gaps"][0].figure is DebtFigure.LTV
