"""The app's own explanation from the same facts (docs/phases/phase-9.md §8.5, D257).
Fixture: the `template_*` cases of tests/fixtures/phase9/explain_render.yaml; each sentence
is also a must-accept text of the validator (a test feeds every one through it).

`sentences(facts)` chooses one or two fixed sentences per topic by the facts' states; `render`
keeps a sentence only when the facts hold every figure it names, and at most four topics
(check 11), by the priority below, in the order of §8.3. A sentence names no account, plan or
asset, so the template reads the same in both modes and its text is the same after rendering
in either.

Readings (docs/phases/phase-9.md §15.3, step 4a): the drafts of explain_validator.yaml are kept
word for word except where their rendering reads wrong. `template_reserve_below` would render
„von 6,0 Monate Monaten“ (the months format carries its word), so the target is its own
clause; `template_reserve_reached` names no target amount, which the reserve's figures may
lack; `template_net_worth_down` would render „-11.770 € weniger“, so it states the change; a
negative savings rate says so instead of „-46,5 % gespart“ (chosen by the figure's sign, as no
state carries it). `template_net_worth_flat`, `template_basis_full`,
`template_savings_rate_negative`, `template_reserve_amount` (the reserve below its target
when the months it covers are absent) and `template_projection_crypto` (only a crypto rate
typed) are added. `template_paths` is split: its second sentence, `template_paths_paused`,
names the reserve itself (the draft's „sie“ had no antecedent once the first was dropped) and
is said only while plans run. A part that refers back („Davon …“) is dropped with the figure
it refers to (`needs`).

Amended 19 Nov 2026 (the step 4a review, D255 amended): `template_surplus_left_negative` reads
„Für eure Sparpläne reicht der Betrag nicht, …“ (its „höher als“ is a contradiction under a
falling trend now). „Davon fließen …“ is said only while plans run, the Tilgung only when
there is one. A negative reserve has its own sentence (`template_reserve_negative`); free cash
that the buffer takes in full says so (`template_surplus_buffer`); one closed month is said in
the singular (`template_surplus_positive_one`, `template_surplus_zero_one`,
`template_basis_short_one`), and no month at all drops every sentence that names the basis.
When nothing else can be said, `render` returns `template_fallback`, never nothing.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from hausbuch.rules.advice import rules
from hausbuch.schemas.advice import AdviceMode, ExplainFacts, Paragraph, StateValue, Topic

S = StateValue


@dataclass(frozen=True)
class Sentence:
    id: str
    topic: Topic
    text: str
    # figures a part refers back to without naming them („Davon …“): without them it is dropped
    needs: tuple[str, ...] = ()


def _s(id_: str, topic: Topic, text: str, needs: tuple[str, ...] = ()) -> Sentence:
    return Sentence(id_, topic, text, needs)


SURPLUS_POSITIVE = _s(
    "template_surplus_positive",
    Topic.SURPLUS,
    "Im Schnitt der letzten {f:basis_months} Monate bleiben euch {f:free_cash} im Monat. "
    "Nach dem Puffer von {f:buffer} könnt ihr {f:investable} anlegen.",
)
SURPLUS_POSITIVE_ONE = _s(
    "template_surplus_positive_one",
    Topic.SURPLUS,
    "Im letzten abgeschlossenen Monat bleiben euch {f:free_cash}. "
    "Nach dem Puffer von {f:buffer} könnt ihr {f:investable} anlegen.",
)
SURPLUS_BUFFER = _s(
    "template_surplus_buffer",
    Topic.SURPLUS,
    "Im Schnitt der letzten {f:basis_months} Monate bleiben euch {f:free_cash} im Monat. "
    "Der Betrag geht ganz in den Puffer von {f:buffer}, zum Anlegen ist nichts übrig.",
)
SURPLUS_BUFFER_ONE = _s(
    "template_surplus_buffer_one",
    Topic.SURPLUS,
    "Im letzten abgeschlossenen Monat bleiben euch {f:free_cash}. "
    "Der Betrag geht ganz in den Puffer von {f:buffer}, zum Anlegen ist nichts übrig.",
)
SURPLUS_ZERO_ONE = _s(
    "template_surplus_zero_one",
    Topic.SURPLUS,
    "Im letzten abgeschlossenen Monat ist nach den laufenden Ausgaben nichts zum Anlegen übrig.",
)
SURPLUS_ZERO = _s(
    "template_surplus_zero",
    Topic.SURPLUS,
    "Im Schnitt der letzten {f:basis_months} Monate ist nach den laufenden Ausgaben nichts "
    "zum Anlegen übrig.",
)
SURPLUS_PLANS = _s(
    "template_surplus_plans",
    Topic.SURPLUS,
    "Davon fließen schon {f:already_invested} im Monat in eure Sparpläne, danach sind es noch "
    "{f:investable_left}.",
    needs=("investable",),
)
SURPLUS_LEFT_NEGATIVE = _s(
    "template_surplus_left_negative",
    Topic.SURPLUS,
    "Für eure Sparpläne reicht der Betrag nicht, den ihr nach dem Puffer anlegen könnt.",
)
RESERVE_BELOW = _s(
    "template_reserve_below",
    Topic.RESERVE,
    "Eure Reserve reicht für {f:reserve_months}. Euer Ziel sind {f:reserve_target_months}, es "
    "fehlen noch {f:reserve_gap}.",
)
RESERVE_NEGATIVE = _s(
    "template_reserve_negative",
    Topic.RESERVE,
    "Eure Reserve liegt bei {f:reserve} und reicht damit für keinen Monat. Euer Ziel sind "
    "{f:reserve_target_months}, es fehlen noch {f:reserve_gap}.",
)
RESERVE_AMOUNT = _s(
    "template_reserve_amount",
    Topic.RESERVE,
    "Eure Reserve liegt bei {f:reserve}.",
)
RESERVE_REACHED = _s(
    "template_reserve_reached",
    Topic.RESERVE,
    "Eure Reserve von {f:reserve} hat euer Ziel erreicht.",
)
PATHS = _s(
    "template_paths",
    Topic.PATHS,
    "Wenn alles so weiterläuft wie bisher, ist die Reserve im {f:path_as_is_reached} voll.",
)
PATHS_PAUSED = _s(
    "template_paths_paused",
    Topic.PATHS,
    "Mit allen Sparplänen pausiert wäre die Reserve im {f:path_reserve_first_reached} voll.",
)
SAVINGS_RATE = _s(
    "template_savings_rate",
    Topic.SAVINGS_RATE,
    "Von eurem Einkommen habt ihr {f:savings_rate} gespart. Ohne die Einmalausgaben wären es "
    "{f:savings_rate_regular}.",
    needs=("savings_rate",),
)
SAVINGS_RATE_PRINCIPAL = _s(
    "template_savings_rate_principal",
    Topic.SAVINGS_RATE,
    "Davon sind {f:principal_total} Tilgung des Kredits der Wohnung.",
    needs=("savings_rate",),
)
SAVINGS_RATE_NEGATIVE = _s(
    "template_savings_rate_negative",
    Topic.SAVINGS_RATE,
    "Ihr habt mehr ausgegeben als eingenommen, eure Sparquote liegt bei {f:savings_rate}.",
)
DEBT = _s(
    "template_debt",
    Topic.DEBT,
    "Die Kredite der Wohnung machen {f:ltv} ihres geschätzten Werts aus. Für Zins und Tilgung "
    "gehen {f:debt_service_ratio} eures Einkommens an die Bank.",
)
NET_WORTH_UP = _s(
    "template_net_worth_up",
    Topic.NET_WORTH,
    "Euer Vermögen liegt bei {f:net_worth}, das sind {f:net_worth_change} mehr als im Vormonat.",
)
NET_WORTH_DOWN = _s(
    "template_net_worth_down",
    Topic.NET_WORTH,
    "Euer Vermögen liegt bei {f:net_worth}. Die Veränderung zum Vormonat beträgt "
    "{f:net_worth_change}.",
)
NET_WORTH_FLAT = _s(
    "template_net_worth_flat",
    Topic.NET_WORTH,
    "Euer Vermögen liegt bei {f:net_worth}.",
)
BASIS_SHORT = _s(
    "template_basis_short",
    Topic.BASIS,
    "Die Zahlen stützen sich erst auf {f:basis_months} abgeschlossene Monate und sind auf das "
    "Jahr hochgerechnet.",
)
BASIS_SHORT_ONE = _s(
    "template_basis_short_one",
    Topic.BASIS,
    "Die Zahlen stützen sich erst auf einen abgeschlossenen Monat und sind auf das Jahr "
    "hochgerechnet.",
)
BASIS_FULL = _s(
    "template_basis_full",
    Topic.BASIS,
    "Die Zahlen stützen sich auf {f:basis_months} abgeschlossene Monate.",
)
PROJECTION = _s(
    "template_projection",
    Topic.PROJECTION,
    "Mit eurer Annahme von {f:assumed_change_depot} pro Jahr für {c:depot} läge euer Vermögen "
    "Ende {f:projection_until} bei {f:projection_total}.",
)
PROJECTION_CRYPTO = _s(
    "template_projection_crypto",
    Topic.PROJECTION,
    "Mit eurer Annahme von {f:assumed_change_crypto} pro Jahr für {c:crypto} läge euer Vermögen "
    "Ende {f:projection_until} bei {f:projection_total}.",
)
PROJECTION_PART = _s(
    "template_projection_part",
    Topic.PROJECTION,
    "Mit euren Annahmen von {f:assumed_change_depot} für {c:depot} und "
    "{f:assumed_change_crypto} für {c:crypto} pro Jahr stammen {f:projection_assumption_part} "
    "der Veränderung aus diesen Annahmen.",
)

FALLBACK = _s(
    "template_fallback",
    Topic.BASIS,
    "Für diesen Zeitraum gibt es noch keine Zahlen.",
)

ALL = (
    SURPLUS_POSITIVE,
    SURPLUS_POSITIVE_ONE,
    SURPLUS_BUFFER,
    SURPLUS_BUFFER_ONE,
    SURPLUS_ZERO,
    SURPLUS_ZERO_ONE,
    SURPLUS_PLANS,
    SURPLUS_LEFT_NEGATIVE,
    RESERVE_BELOW,
    RESERVE_NEGATIVE,
    RESERVE_AMOUNT,
    RESERVE_REACHED,
    PATHS,
    PATHS_PAUSED,
    SAVINGS_RATE,
    SAVINGS_RATE_PRINCIPAL,
    SAVINGS_RATE_NEGATIVE,
    DEBT,
    NET_WORTH_UP,
    NET_WORTH_DOWN,
    NET_WORTH_FLAT,
    BASIS_SHORT,
    BASIS_SHORT_ONE,
    BASIS_FULL,
    PROJECTION,
    PROJECTION_CRYPTO,
    PROJECTION_PART,
    FALLBACK,
)

# The four topics kept when more have something to say: what can be invested and the reserve
# first, then the caveat of a short basis and the household's own projection; a full basis is
# no caveat and comes last.
_PRIORITY = (
    Topic.SURPLUS,
    Topic.RESERVE,
    Topic.BASIS,
    Topic.PROJECTION,
    Topic.PATHS,
    Topic.SAVINGS_RATE,
    Topic.DEBT,
    Topic.NET_WORTH,
)
_ORDER = (
    Topic.SURPLUS,
    Topic.RESERVE,
    Topic.PATHS,
    Topic.SAVINGS_RATE,
    Topic.DEBT,
    Topic.NET_WORTH,
    Topic.BASIS,
    Topic.PROJECTION,
)
_SENTENCE = re.compile(r"[^.!?]+[.!?]")


def _negative(facts: ExplainFacts, key: str) -> bool:
    fact = facts.figure(key)
    return fact is not None and Decimal(fact.value) < 0


def _positive(facts: ExplainFacts, key: str) -> bool:
    fact = facts.figure(key)
    return fact is not None and Decimal(fact.value) > 0


def _number(facts: ExplainFacts, key: str) -> Decimal | None:
    fact = facts.figure(key)
    return None if fact is None else Decimal(fact.value)


def sentences(facts: ExplainFacts) -> list[Sentence]:
    """The template's sentences for the facts' states, before any figure is checked."""
    st = facts.states.get
    one = _number(facts, "basis_months") == 1  # one closed month: said in the singular
    out: list[Sentence] = []
    if st("investable_state") == S.ZERO:
        if _positive(facts, "free_cash"):  # the buffer takes it all
            out.append(SURPLUS_BUFFER_ONE if one else SURPLUS_BUFFER)
        else:
            out.append(SURPLUS_ZERO_ONE if one else SURPLUS_ZERO)
    elif st("investable_state") == S.POSITIVE:
        out.append(SURPLUS_POSITIVE_ONE if one else SURPLUS_POSITIVE)
        if st("left_state") == S.NEGATIVE:
            out.append(SURPLUS_LEFT_NEGATIVE)
        elif st("left_state") == S.POSITIVE and _positive(facts, "already_invested"):
            out.append(SURPLUS_PLANS)  # „Davon fließen …“ only while plans run
    if st("reserve_state") == S.BELOW:
        # a negative reserve covers no month; without the months, its amount alone
        months = facts.figure("reserve_months") is not None
        if _negative(facts, "reserve"):
            out.append(RESERVE_NEGATIVE)
        else:
            out.append(RESERVE_BELOW if months else RESERVE_AMOUNT)
        out.append(PATHS)
        if _positive(facts, "already_invested"):  # a pause path only when plans run
            out.append(PATHS_PAUSED)
    elif st("reserve_state") == S.REACHED:
        out.append(RESERVE_REACHED)
    if _negative(facts, "savings_rate"):
        out.append(SAVINGS_RATE_NEGATIVE)
    else:
        out.append(SAVINGS_RATE)
        if _positive(facts, "principal_total"):  # the Tilgung only when there is one
            out.append(SAVINGS_RATE_PRINCIPAL)
    out.append(DEBT)
    out.append({S.UP: NET_WORTH_UP, S.DOWN: NET_WORTH_DOWN}.get(st("trend_state"), NET_WORTH_FLAT))
    if st("basis_state") == S.SHORT:
        out.append(BASIS_SHORT_ONE if one else BASIS_SHORT)
    elif st("basis_state") == S.FULL:
        out.append(BASIS_FULL)
    if st("projection_state") == S.SHOWN:
        # the rate the household typed is named; both typed: the part from them as well
        if facts.figure("assumed_change_depot") is not None:
            out.append(PROJECTION)
        else:
            out.append(PROJECTION_CRYPTO)
        out.append(PROJECTION_PART)
    return out


def _fillable(text: str, facts: ExplainFacts) -> bool:
    figures = {f.key for f in facts.figures}
    if _number(facts, "basis_months") == 0:  # no closed month: no sentence names the basis
        figures.discard("basis_months")
    classes = {c.value for c in facts.classes}
    for letter, key in re.findall(rules()["slot_pattern"], text):
        if (letter == "f" and key not in figures) or (letter == "c" and key not in classes):
            return False
        if letter in "kpa":  # the template names none; never in generic
            return False
    return True


def written(facts: ExplainFacts) -> list[tuple[Sentence, str]]:
    """Each chosen sentence's parts whose figures the facts hold, in the order chosen."""
    out = []
    figures = {f.key for f in facts.figures}
    for sentence in sentences(facts):
        if not figures.issuperset(sentence.needs):
            continue
        for part in _SENTENCE.findall(sentence.text):
            part = part.strip()
            if _fillable(part, facts):
                out.append((sentence, part))
    return out


def render(facts: ExplainFacts, mode: AdviceMode) -> list[Paragraph]:
    """The template text: at most four paragraphs, one per topic, shown as „Standardtext“."""
    del mode  # the template names no account, plan or asset, so both modes read alike
    by_topic: dict[Topic, list[str]] = {}
    for sentence, part in written(facts):
        by_topic.setdefault(sentence.topic, []).append(part)
    priority = list(_PRIORITY)
    if facts.states.get("basis_state") != S.SHORT:  # a full basis is no caveat: said last
        priority.remove(Topic.BASIS)
        priority.append(Topic.BASIS)
    kept = [t for t in priority if t in by_topic][: rules()["shape"]["paragraphs_max"]]
    out = [Paragraph(topic=t, text=" ".join(by_topic[t])) for t in _ORDER if t in kept]
    return out or [Paragraph(topic=FALLBACK.topic, text=FALLBACK.text)]  # never nothing
