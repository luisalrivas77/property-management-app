"""Phase 9 step 4a: the explanation's pure rules (docs/phases/phase-9.md §8, §9, D252–D258).

Replays explain_validator.yaml (D258 layer 1), instrument_names.yaml (layer 2, generated in
code), explain_facts.yaml, explain_render.yaml and lights.yaml's every_sentence_passes_validator.
"""

from collections import Counter
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fixture_models_phase9 import EvFacts, load

from hausbuch.rules.advice import facts as facts_rules
from hausbuch.rules.advice import render as render_rules
from hausbuch.rules.advice import template as template_rules
from hausbuch.rules.advice.validator import validate, versions
from hausbuch.rules.health.bands import proposals
from hausbuch.schemas import health as h
from hausbuch.schemas import plan as pl
from hausbuch.schemas.advice import (
    AdviceMode,
    ExplainFacts,
    ExplainLabels,
    ExplainOutput,
    Fact,
    FigureType,
    Paragraph,
    StateValue,
    Topic,
    ViolationCode,
)
from hausbuch.schemas.health import BandLimits
from hausbuch.schemas.household import NetWorthClass

V = ViolationCode
VALIDATOR = load("explain_validator")

_DUMMY = {
    FigureType.MONEY: "1.00",
    FigureType.PERCENT: "0.1",
    FigureType.MONTHS: "1.0",
    FigureType.MONTH: "2027-01",
    FigureType.COUNT: "3",
    FigureType.MULTIPLE: "1.0",
}


def ev_facts(
    base: EvFacts, mode: AdviceMode | None = None, states: dict[str, str] | None = None
) -> ExplainFacts:
    """explain_validator.yaml's facts as an ExplainFacts: each key with its §8.1 type."""
    mode = mode or base.mode
    generic = mode is AdviceMode.GENERIC
    merged = {**base.states, **(states or {})}
    return ExplainFacts(
        mode=mode,
        as_of=base.as_of,
        figures=[
            Fact(key=k, type=facts_rules.figure_type(k), value=_DUMMY[facts_rules.figure_type(k)])
            for k in base.figures
        ],
        states={k: StateValue(v) for k, v in merged.items()},
        accounts={} if generic else base.accounts,
        plans={} if generic else base.plans,
        assets={} if generic else base.assets,
    )


def check(text: str, topic: Topic, facts: ExplainFacts, **kwargs):
    return validate([Paragraph(topic=topic, text=text)], facts, facts.mode, **kwargs)


def _codes(result) -> list[str]:
    return sorted({v.code.value for v in result.violations})


# --- Layer 1: explain_validator.yaml ----------------------------------------------------

ACCEPT = [c for c in VALIDATOR.accept if not c.from_bands]
REJECT = VALIDATOR.reject


@pytest.mark.parametrize("case", ACCEPT, ids=[c.id for c in ACCEPT])
def test_must_accept(case):
    facts = ev_facts(VALIDATOR.facts, case.mode, case.states)
    result = check(case.text, case.topic, facts, extra_lexicon=VALIDATOR.extra_lexicon)
    assert result.ok, f"{case.id}: {_codes(result)} {result.violations}"


@pytest.mark.parametrize("case", REJECT, ids=[c.id for c in REJECT])
def test_must_reject(case):
    facts = ev_facts(VALIDATOR.facts, case.mode, case.states)
    if case.paragraphs is not None:
        paragraphs = [Paragraph(topic=p.topic, text=p.text) for p in case.paragraphs]
    else:
        paragraphs = [Paragraph(topic=case.topic, text=case.text * (case.repeat or 1))]
    result = validate(paragraphs, facts, facts.mode, extra_lexicon=VALIDATOR.extra_lexicon)
    wanted = case.rejects if isinstance(case.rejects, list) else [case.rejects]
    found = {v.code for v in result.violations}
    assert set(wanted) <= found, f"{case.id}: wants {wanted}, found {_codes(result)}"


def test_layer_one_totals():
    """D258 layer 1: 100 % of the must-reject texts rejected, 100 % of the must-accept passed."""
    accepted = sum(
        check(
            c.text,
            c.topic,
            ev_facts(VALIDATOR.facts, c.mode, c.states),
            extra_lexicon=VALIDATOR.extra_lexicon,
        ).ok
        for c in ACCEPT
    )
    print(f"\nlayer 1: {accepted}/{len(ACCEPT)} must-accept accepted")
    assert accepted == len(ACCEPT)


# --- The marks' sentences (explain_validator::every_mark_sentence, lights.yaml) ---------

_MARK_TOPIC = {
    "reserve": Topic.RESERVE,
    "savings_rate": Topic.SAVINGS_RATE,
    "ltv": Topic.DEBT,
    "debt_service_ratio": Topic.DEBT,
    "debt_ratio": Topic.DEBT,
    "debt_to_income": Topic.DEBT,
}
_LIGHTS = [f"light_{f}" for f in _MARK_TOPIC]


def mark_cases():
    """Every sentence of bands.yaml with the states of a light of its colour, in both modes.
    An intro sentence is said under every colour; the other lights are left unset, so a
    sentence is judged by its own figure's light only."""
    out = []
    for code in sorted(proposals()[2]):
        figure, _, kind = code.partition(".")
        colours = ["green", "amber", "red"] if kind == "intro" else [kind.split("_")[0]]
        for colour in colours:
            states = {name: None for name in _LIGHTS}
            states[f"light_{figure}"] = colour
            if figure == "reserve":
                states["reserve_state"] = "reached" if colour == "green" else "below"
            for mode in AdviceMode:
                out.append((code, colour, mode, states))
    return out


MARKS = mark_cases()


@pytest.mark.parametrize(
    ("code", "colour", "mode", "states"), MARKS, ids=[f"{c}-{k}-{m}" for c, k, m, _ in MARKS]
)
def test_every_mark_sentence_passes(code, colour, mode, states):
    base = VALIDATOR.facts
    kept = {k: v for k, v in {**base.states, **states}.items() if v is not None}
    facts = ev_facts(base.model_copy(update={"states": kept}), mode)
    text = proposals()[2][code]
    result = check(text, _MARK_TOPIC[code.partition(".")[0]], facts)
    assert result.ok, f"{code} under {colour} ({mode}): {_codes(result)}"


def test_versions_named():
    got = versions()
    assert got["validator"].startswith("advice-rules-")
    assert set(got) == {"validator", "lexicon", "vocabulary"}
    assert all(got.values())


def test_every_violation_returned_not_only_the_first():
    facts = ev_facts(VALIDATOR.facts)
    result = check("Der $ZPH 3 tausend.", Topic.NET_WORTH, facts)
    assert {V.CHARACTER, V.DIGIT, V.NUMBER_WORD, V.ABBREVIATION} <= {
        v.code for v in result.violations
    }


def test_schema_output_accepted_too():
    facts = ev_facts(VALIDATOR.facts)
    out = ExplainOutput(
        paragraphs=[
            Paragraph(topic=Topic.RESERVE, text="Eure Reserve reicht für {f:reserve_months}.")
        ]
    )
    assert validate(out, facts, AdviceMode.OWN_USE).ok


def test_generic_rejects_every_k_p_a_slot():
    """`generic` facts carry no k/p/a keys, and any such slot is a violation."""
    own = ev_facts(VALIDATOR.facts)
    facts = ev_facts(VALIDATOR.facts, AdviceMode.GENERIC)
    assert not (facts.accounts or facts.plans or facts.assets)
    for text in ("Auf {k:k1} liegt Geld.", "Der Sparplan {p:p1} läuft.", "{a:a1} gehört euch."):
        assert check(text, Topic.NET_WORTH, own).ok, text
        result = check(text, Topic.NET_WORTH, facts)
        assert V.SLOT in {v.code for v in result.violations}, text
        # an own_use facts object validated in generic mode is judged as generic too
        result = validate([Paragraph(topic=Topic.NET_WORTH, text=text)], own, AdviceMode.GENERIC)
        assert V.SLOT in {v.code for v in result.violations}, text


# --- Layer 2: instrument_names.yaml, generated (D258) -----------------------------------

NAMES = load("instrument_names")
_LOOKALIKE = {
    "a": "а",
    "e": "е",
    "o": "о",
    "p": "р",
    "c": "с",
    "x": "х",
}


def transform(name: str, how: str) -> str:
    """The carriers' transforms as instrument_names.yaml describes them."""
    match how:
        case "plain":
            return name
        case "lower":
            return name.lower()
        case "upper":
            return name.upper()
        case "spaced":
            return " ".join(ch for ch in name if not ch.isspace())
        case "hyphenated":
            half = len(name) // 2
            return f"{name[:half]}-{name[half:]}"
        case "compound":
            return f"{name}fonds"
        case "zero_width":
            return f"{name[0]}​{name[1:]}"
        case "lookalike":
            for i, ch in enumerate(name):
                if ch in _LOOKALIKE:
                    return f"{name[:i]}{_LOOKALIKE[ch]}{name[i + 1 :]}"
            return f"а{name}"
        case "dollar":
            return f"${name}"
        case "braces":
            return f"{{{name}}}"
    raise AssertionError(how)


def generated():
    for mode in AdviceMode:
        for carrier in NAMES.carriers:
            for block, names in NAMES.names.items():
                for name in names:
                    text = carrier.sentence.replace("<NAME>", transform(name, carrier.transform))
                    yield mode, carrier.id, block, name, text


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.1f} %)"


def test_layer_two_every_name_in_every_carrier_rejected():
    """Every name × every carrier × both modes is rejected, on the shipped lexicon only. Prints
    the count, the checks that rejected the texts, the lexicon's hit rate per name block and
    per carrier (the invented names are in no lexicon by design), and the texts only the
    vocabulary check caught."""
    by_check: Counter[str] = Counter()
    lexicon_hit: Counter[tuple[str, str]] = Counter()
    seen: Counter[tuple[str, str]] = Counter()
    only_vocabulary: Counter[str] = Counter()
    lexicon_missed_plain: set[str] = set()
    escaped = []
    texts = list(generated())
    for mode, carrier, block, name, text in texts:
        result = check(text, Topic.NET_WORTH, ev_facts(VALIDATOR.facts, mode))
        codes = {v.code for v in result.violations}
        if not codes:
            escaped.append((mode.value, carrier, name))
        by_check.update(c.value for c in codes)
        seen[("block", block)] += 1
        seen[("carrier", carrier)] += block != "invented"
        hit = V.LEXICON in codes
        lexicon_hit[("block", block)] += hit
        lexicon_hit[("carrier", carrier)] += hit and block != "invented"
        if codes == {V.VOCABULARY}:
            only_vocabulary[block] += 1
        if carrier == "plain" and block != "invented" and not hit:
            lexicon_missed_plain.add(name)
    total = len(texts)
    print(f"\nlayer 2: {total} texts generated, {total - len(escaped)} rejected")
    for code, n in sorted(by_check.items(), key=lambda kv: -kv[1]):
        print(f"layer 2: {code:15} found in {_pct(n, total)}")
    for kind in ("block", "carrier"):
        for (k, key), n in seen.items():
            if k == kind and n:
                print(f"layer 2: lexicon hits, {kind} {key:20} {_pct(lexicon_hit[(k, key)], n)}")
    print(
        f"layer 2: caught by the vocabulary check only: {sum(only_vocabulary.values())} "
        f"{dict(only_vocabulary)}"
    )
    print(
        f"layer 2: real names the lexicon misses in the plain carrier: "
        f"{sorted(lexicon_missed_plain)}"
    )
    assert not escaped, escaped


@pytest.mark.parametrize("carrier", NAMES.carriers, ids=[c.id for c in NAMES.carriers])
@pytest.mark.parametrize("mode", list(AdviceMode))
def test_layer_two_control_carrier_alone_accepted(carrier, mode):
    """The control: each carrier with an ordinary word in place of the name passes, so a
    rejection above comes from the name, not from the carrier, its topic or its states."""
    text = carrier.sentence.replace("<NAME>", "Depot")
    result = check(text, Topic.NET_WORTH, ev_facts(VALIDATOR.facts, mode))
    assert result.ok, f"{carrier.id}: {_codes(result)}"


# --- render (explain_render.yaml) -------------------------------------------------------

RENDER = load("explain_render")
RENDER_CASES = [c for c in RENDER.cases if c.renders]


@pytest.mark.parametrize("case", RENDER_CASES, ids=[c.id for c in RENDER_CASES])
def test_render(case):
    facts = case.facts or RENDER.facts
    labels = case.labels or RENDER.labels
    for one in case.renders:
        para = [Paragraph(topic=Topic.NET_WORTH, text=one.text)]
        assert render_rules.render(para, facts, labels) == [one.expect], f"{case.id}: {one.text}"


def test_render_raises_on_an_unknown_slot():
    cases = [c for c in RENDER.cases if c.raises]
    assert cases
    for case in cases:
        for text in case.raises:
            with pytest.raises(render_rules.UnknownSlot):
                render_rules.render(
                    [Paragraph(topic=Topic.NET_WORTH, text=f"Vorher {text} nachher.")],
                    case.facts or RENDER.facts,
                    case.labels or RENDER.labels,
                )
    no_labels = RENDER.labels.model_copy(update={"accounts": {}})
    with pytest.raises(render_rules.UnknownSlot):  # a held key without its label
        render_rules.render_text("{k:k1}", RENDER.facts, no_labels)


# --- template (explain_render.yaml's template_* cases) ----------------------------------

TEMPLATE_CASES = [c for c in RENDER.cases if c.id.startswith("template_")]


def _with_states(facts: ExplainFacts, states: dict[str, str]) -> ExplainFacts:
    merged = {**facts.states, **{k: StateValue(v) for k, v in states.items()}}
    return facts.model_copy(update={"states": merged})


@pytest.mark.parametrize("case", TEMPLATE_CASES, ids=[c.id for c in TEMPLATE_CASES])
def test_template(case):
    facts = _with_states(case.facts or RENDER.facts, case.states)
    labels = case.labels or RENDER.labels
    paragraphs = template_rules.render(facts, facts.mode)
    chosen = [s.id for s in template_rules.sentences(facts)]
    assert set(case.expect_sentences) <= set(chosen), f"{case.id}: {chosen}"
    for p in paragraphs:
        for letter in case.expect_no_slots:
            assert f"{{{letter}:" not in p.text, case.id
    result = validate(paragraphs, facts, facts.mode)
    assert result.ok, f"{case.id}: {_codes(result)}"
    rendered = " ".join(render_rules.render(paragraphs, facts, labels))
    for wanted in case.expect_rendered_contains:
        assert wanted in rendered, f"{case.id}: {rendered}"


def test_render_mark_sentence():
    """A mark's sentence (bands.yaml) renders through the same formats."""
    case = next(c for c in RENDER.cases if c.sentence)
    assert case.sentence in proposals()[2]


def test_template_drafts_kept_by_id():
    """Every template_* must-accept text of the validator names a template sentence."""
    drafts = {c.id for c in VALIDATOR.accept if c.id.startswith("template_")}
    assert drafts <= {s.id for s in template_rules.ALL}


_CHOSEN_BY = {
    "template_surplus_zero": {"investable_state": "zero"},
    "template_surplus_left_negative": {"left_state": "negative"},
    "template_reserve_reached": {"reserve_state": "reached", "light_reserve": "green"},
    "template_net_worth_up": {"trend_state": "up"},
    "template_net_worth_flat": {"trend_state": "flat"},
    "template_basis_full": {"basis_state": "full"},
    "template_projection": {"projection_state": "shown"},
    "template_projection_part": {"projection_state": "shown"},
    "template_projection_crypto": {"projection_state": "shown"},
    # 19 Nov 2026 (the step 4a review): chosen by a figure, under these states
    "template_surplus_buffer": {"investable_state": "zero"},
    "template_surplus_buffer_one": {"investable_state": "zero"},
    "template_surplus_zero_one": {"investable_state": "zero"},
}


@pytest.mark.parametrize("mode", list(AdviceMode))
@pytest.mark.parametrize("sentence", template_rules.ALL, ids=[s.id for s in template_rules.ALL])
def test_every_template_sentence_passes_the_validator(sentence, mode):
    """Every template sentence passes in both modes under the states it is chosen by."""
    facts = ev_facts(VALIDATOR.facts, mode, _CHOSEN_BY.get(sentence.id, {}))
    result = check(sentence.text, sentence.topic, facts)
    assert result.ok, f"{sentence.id}: {_codes(result)}"
    # chosen by a figure (its sign, or one absent), not by a state
    by_figure = {
        "template_savings_rate_negative",
        "template_reserve_amount",
        "template_projection_crypto",
        # 19 Nov 2026: one closed month, free cash the buffer takes or none, a negative reserve
        "template_surplus_positive_one",
        "template_surplus_buffer_one",
        "template_surplus_zero",
        "template_surplus_zero_one",
        "template_reserve_negative",
        "template_basis_short_one",
        "template_fallback",
    }
    if sentence.id not in by_figure:
        assert sentence in template_rules.sentences(facts), sentence.id


def test_template_negative_savings_rate():
    facts = ev_facts(VALIDATOR.facts)
    figures = [
        f if f.key != "savings_rate" else f.model_copy(update={"value": "-0.4"})
        for f in facts.figures
    ]
    picked = template_rules.sentences(facts.model_copy(update={"figures": figures}))
    assert template_rules.SAVINGS_RATE_NEGATIVE in picked
    assert template_rules.SAVINGS_RATE not in picked


_STATE_GRID = [
    {
        "reserve_state": reserve,
        "investable_state": investable,
        "left_state": left,
        "trend_state": trend,
        "basis_state": basis,
        "projection_state": projection,
        **({"light_reserve": "green"} if reserve == "reached" else {}),
    }
    for reserve in ("below", "reached")
    for investable, left in (
        ("positive", "positive"),
        ("positive", "negative"),
        ("zero", "positive"),
    )
    for trend in ("up", "down", "flat")
    for basis in ("short", "full")
    for projection in ("shown", "absent")
]


def _light_settings():
    """No light at all (no band confirmed), then each light alone in each of its values."""
    yield {}
    for name in _LIGHTS:
        for value in ("none", "green", "amber", "red"):
            yield {name: value}


def _reachable(states: dict[str, str]) -> bool:
    """The reserve's green is its target reached (D238): green only when reached, and a
    reached reserve is green or unmarked."""
    light = states.get("light_reserve")
    if states["reserve_state"] == "reached":
        return light in (None, "green")
    return light != "green"


def _without_lights(base: EvFacts) -> EvFacts:
    kept = {k: v for k, v in base.states.items() if not k.startswith("light_")}
    return base.model_copy(update={"states": kept})


def _template_ok(facts: ExplainFacts, mode: AdviceMode, where: object) -> None:
    paragraphs = template_rules.render(facts, mode)
    assert 1 <= len(paragraphs) <= 4, where
    result = validate(paragraphs, facts, mode)
    assert result.ok, (where, _codes(result), [p.text for p in paragraphs])
    render_rules.render(paragraphs, facts, ExplainLabels())  # no k/p/a, so no label needed


@pytest.mark.parametrize("mode", list(AdviceMode))
def test_template_in_every_state_and_light_at_most_four_and_valid(mode):
    """The template is the fallback that must always pass: every state of the grid under no
    light, and under each light alone in each value that can occur."""
    base = _without_lights(VALIDATOR.facts)
    n = 0
    for lights in _light_settings():
        for grid in _STATE_GRID:
            states = {k: v for k, v in grid.items() if k != "light_reserve"} | lights
            if not _reachable(states):
                continue
            _template_ok(ev_facts(base, mode, states), mode, states)
            n += 1
    assert n > 1000


def _with_values(facts: ExplainFacts, values: dict[str, str]) -> ExplainFacts:
    figures = [f.model_copy(update={"value": values.get(f.key, f.value)}) for f in facts.figures]
    return facts.model_copy(update={"figures": figures})


# 19 Nov 2026 (the step 4a review): the figures that choose a sentence of their own
_FIGURE_CASES = {
    "no_month": {"basis_months": "0"},
    "one_month": {"basis_months": "1"},
    "free_cash_buffered": {"free_cash": "250.00", "investable": "0.00"},
    "no_free_cash": {"free_cash": "0.00", "investable": "0.00"},
    "negative_reserve": {"reserve": "-120.00", "reserve_months": "-0.3"},
    "no_plans": {"already_invested": "0.00"},
    "no_principal": {"principal_total": "0.00"},
}


@pytest.mark.parametrize("mode", list(AdviceMode))
@pytest.mark.parametrize("case", sorted(_FIGURE_CASES))
def test_template_figure_choices_render_under_every_light(case, mode):
    """Each sentence a figure chooses is rendered and validated as a whole paragraph, over
    the state grid and under each light alone."""
    base = _without_lights(VALIDATOR.facts)
    for lights in _light_settings():
        for grid in _STATE_GRID[::3]:
            states = {k: v for k, v in grid.items() if k != "light_reserve"} | lights
            if not _reachable(states):
                continue
            facts = _with_values(ev_facts(base, mode, states), _FIGURE_CASES[case])
            _template_ok(facts, mode, (case, states))


def test_template_figure_choices_pick_their_sentences():
    facts = ev_facts(VALIDATOR.facts, None, {"investable_state": "zero"})
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, {"free_cash": "250.00"}))}
    assert "template_surplus_buffer" in ids
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, {"basis_months": "1"}))}
    assert {"template_surplus_buffer_one", "template_basis_short_one"} <= ids
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, {"basis_months": "0"}))}
    assert not {"template_basis_short", "template_basis_short_one"} & ids
    nothing = {"free_cash": "0.00", "investable": "0.00"}
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, nothing))}
    assert "template_surplus_zero" in ids and "template_surplus_buffer" not in ids
    ids = {
        s.id
        for s, _ in template_rules.written(_with_values(facts, nothing | {"basis_months": "1"}))
    }
    assert "template_surplus_zero_one" in ids and "template_surplus_zero" not in ids
    facts = ev_facts(VALIDATOR.facts, None, {"investable_state": "positive"})
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, {"basis_months": "1"}))}
    assert "template_surplus_positive_one" in ids and "template_surplus_positive" not in ids
    # no closed month: the part naming the months is dropped, „Nach dem Puffer …“ (the
    # antecedent of „Davon …“) stays
    for left in ("positive", "negative"):
        zero = ev_facts(VALIDATOR.facts, None, {"investable_state": "positive", "left_state": left})
        parts = [p for _, p in template_rules.written(_with_values(zero, {"basis_months": "0"}))]
        assert not any("{f:basis_months}" in p for p in parts)
        assert any(p.startswith("Nach dem Puffer von {f:buffer}") for p in parts), left
    facts = ev_facts(VALIDATOR.facts)
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, {"reserve": "-1.00"}))}
    assert "template_reserve_negative" in ids and "template_reserve_below" not in ids
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, {"principal_total": "0"}))}
    assert "template_savings_rate_principal" not in ids
    facts = ev_facts(VALIDATOR.facts, None, {"left_state": "positive"})
    ids = {s.id for s, _ in template_rules.written(_with_values(facts, {"already_invested": "0"}))}
    assert "template_surplus_plans" not in ids


@pytest.mark.parametrize("mode", list(AdviceMode))
def test_template_with_no_figures_is_the_fallback(mode):
    """With nothing to say, render returns the fixed fallback sentence, never nothing."""
    for lights in _light_settings():
        facts = ev_facts(_without_lights(VALIDATOR.facts), mode, lights)
        facts = facts.model_copy(update={"figures": []})
        paragraphs = template_rules.render(facts, mode)
        assert paragraphs == [
            Paragraph(topic=template_rules.FALLBACK.topic, text=template_rules.FALLBACK.text)
        ]
        assert validate(paragraphs, facts, mode).ok, lights


@pytest.mark.parametrize("mode", list(AdviceMode))
@pytest.mark.parametrize("missing", VALIDATOR.facts.figures)
def test_template_with_one_figure_absent(missing, mode):
    """A figure the facts lack drops the parts that name it, and those that refer back to it;
    what is left still passes and renders."""
    for grid in _STATE_GRID[::5]:
        facts = ev_facts(VALIDATOR.facts, mode, grid)
        facts = facts.model_copy(update={"figures": [f for f in facts.figures if f.key != missing]})
        _template_ok(facts, mode, (missing, grid))
        for _, part in template_rules.written(facts):
            assert f"{{f:{missing}}}" not in part


def _drop(facts: ExplainFacts, *keys: str) -> ExplainFacts:
    return facts.model_copy(update={"figures": [f for f in facts.figures if f.key not in keys]})


def test_template_dependent_parts_follow_their_antecedent():
    facts = ev_facts(VALIDATOR.facts, None, {"left_state": "positive"})
    ids = [s.id for s, _ in template_rules.written(_drop(facts, "investable"))]
    assert "template_surplus_plans" not in ids  # „Davon fließen …“ without the investable
    ids = [s.id for s, _ in template_rules.written(_drop(facts, "savings_rate"))]
    assert "template_savings_rate" not in ids
    assert "template_savings_rate_principal" not in ids
    texts = [p for _, p in template_rules.written(_drop(facts, "path_as_is_reached"))]
    assert any(t.startswith("Mit allen Sparplänen pausiert wäre die Reserve") for t in texts)


def test_template_paused_path_only_while_plans_run():
    facts = ev_facts(VALIDATOR.facts)
    zero = [
        f if f.key != "already_invested" else f.model_copy(update={"value": "0.00"})
        for f in facts.figures
    ]
    picked = template_rules.sentences(facts.model_copy(update={"figures": zero}))
    assert template_rules.PATHS_PAUSED not in picked
    assert template_rules.PATHS_PAUSED in template_rules.sentences(facts)


@pytest.mark.parametrize("mode", list(AdviceMode))
def test_template_projection_with_a_crypto_rate_only(mode):
    facts = _drop(
        ev_facts(VALIDATOR.facts, mode, {"projection_state": "shown"}), "assumed_change_depot"
    )
    paragraphs = template_rules.render(facts, mode)
    projection = [p for p in paragraphs if p.topic is Topic.PROJECTION]
    assert projection, [p.topic for p in paragraphs]
    assert "{f:assumed_change_crypto}" in projection[0].text
    assert validate(paragraphs, facts, mode).ok


# --- facts (explain_facts.yaml) ---------------------------------------------------------

FACTS = load("explain_facts")


def _assembled(inputs) -> ExplainFacts:
    for key, f in inputs.figures.items():
        if f is not None:
            assert f.type is facts_rules.figure_type(key), key  # the fixture's type is §8.1's
    projection = None
    if inputs.projection is not None:
        p = inputs.projection
        projection = facts_rules.ProjectionIn(
            until=p.until,
            total=p.total,
            change=p.change,
            assumption_part=p.assumption_part,
            depot=p.value_change.depot,
            crypto=p.value_change.crypto,
        )
    return facts_rules.assemble(
        inputs.mode,
        inputs.as_of,
        figures={k: (None if f is None else f.value) for k, f in inputs.figures.items()},
        accounts=[facts_rules.AccountIn(label=a.label, kind=a.kind) for a in inputs.accounts],
        plans=[
            facts_rules.PlanIn(label=p.destination_label, asset_class=p.asset_class, plan_key=p.key)
            for p in inputs.plans
        ],
        assets=[
            facts_rules.AssetIn(
                name=a.name, asset_class=a.class_, units=a.units_at_as_of, spam=a.spam
            )
            for a in inputs.assets
        ],
        reserve=(inputs.reserve.amount, inputs.reserve.target) if inputs.reserve else None,
        window_months=inputs.window_months,
        bands=inputs.bands,
        lights=inputs.lights,
        projection=projection,
    )


@pytest.mark.parametrize("case", FACTS.cases, ids=[c.id for c in FACTS.cases])
def test_facts(case):
    got = _assembled(case.inputs)
    want = case.expect
    dumped = got.model_dump_json()
    for name in ("accounts", "plans", "assets"):
        if getattr(want, name) is not None:
            assert getattr(got, name) == getattr(want, name), f"{case.id}: {name}"
    if want.labels is not None:
        assert got.labels == want.labels, case.id
    for text in want.never_in_request:
        assert text not in dumped, f"{case.id}: {text!r} reached the request"
    keys = [f.key for f in got.figures]
    if want.figure_keys is not None:
        assert keys == want.figure_keys, case.id
    for one in want.figures_include:
        fact = got.figure(one.key)
        assert fact is not None, f"{case.id}: {one.key}"
        assert (fact.type, fact.value) == (one.type, one.value), f"{case.id}: {one.key}"
    for key in want.absent:
        assert key not in keys, f"{case.id}: {key}"
    for name, value in want.states.items():
        assert got.states.get(name) == value, f"{case.id}: {name}"
    for name in want.states_absent:
        assert name not in got.states, f"{case.id}: {name}"


def test_labels_never_in_the_serialised_facts():
    """The serialised facts contain no label string: distinctive labels in, then absent."""
    marker = "Qwzlabel"
    got = facts_rules.assemble(
        AdviceMode.OWN_USE,
        VALIDATOR.facts.as_of,
        figures={"reserve": Decimal("100.00")},
        accounts=[facts_rules.AccountIn(label=f"{marker} Konto", kind="bank_savings")],
        plans=[facts_rules.PlanIn(label=f"{marker} Plan", asset_class="depot", plan_key="xq")],
        assets=[
            facts_rules.AssetIn(name=f"{marker} Fonds", asset_class="depot", units=Decimal("1"))
        ],
        paths={"pause:xq": (Decimal("10.00"), "2028-01")},
    )
    dumped = got.model_dump_json()
    assert marker not in dumped
    assert got.labels.accounts == {"k1": f"{marker} Konto"}
    assert got.figure("path_pause_p1_reached") is not None  # the opaque key, never the plan's
    assert "xq" not in dumped
    assert len(facts_rules.facts_sha256(got)) == 64
    stripped = got.model_copy(update={"labels": ExplainLabels()})
    assert facts_rules.facts_sha256(got) == facts_rules.facts_sha256(stripped)


def test_generic_facts_carry_no_keys():
    got = facts_rules.assemble(
        AdviceMode.GENERIC,
        VALIDATOR.facts.as_of,
        figures={"reserve": Decimal("100.00")},
        accounts=[facts_rules.AccountIn(label="Konto", kind="bank_savings")],
        plans=[facts_rules.PlanIn(label="Plan", asset_class="depot", plan_key="xq")],
        assets=[facts_rules.AssetIn(name="Fonds", asset_class="depot", units=Decimal("1"))],
        paths={"pause:xq": (Decimal("10.00"), "2028-01"), "as_is": (Decimal("5.00"), "2029-01")},
    )
    assert not (got.accounts or got.plans or got.assets)
    assert not (got.labels.accounts or got.labels.plans or got.labels.assets)
    keys = {f.key for f in got.figures}
    assert "path_as_is_reached" in keys
    assert not any("pause" in k for k in keys)


def test_build_maps_health_and_plan():
    """`build` reads Health and HouseholdPlan into `assemble` (constructed without their own
    validators, which their tests cover; this checks the mapping only)."""
    c = Decimal
    health = h.Health.model_construct(
        as_of=date(2027, 6, 3),
        window=h.Window.model_construct(months=["2027-03", "2027-04", "2027-05"], enough=True),
        savings_rate=h.SavingsRate.model_construct(
            saved=c("900.00"), principal=c("300.00"), rate=c("0.12"), rate_regular=c("0.15")
        ),
        reserve=None,
        debt=h.Debt.model_construct(
            properties=[h.PropertyLtv.model_construct(ltv=c("0.7388"))],
            debt_ratio=c("0.6"),
            debt_service_ratio=c("0.1537"),
            debt_to_income=c("4.2"),
        ),
        split={NetWorthClass.BANK: c("0.2"), NetWorthClass.PROPERTY: c("0.8")},
        series=[
            h.NetWorthPoint.model_construct(total=c("128380.00"), change=c("-11770.00")),
            h.NetWorthPoint.model_construct(total=None, change=None),
        ],
        marks={
            h.MarkedFigure.DEBT_SERVICE_RATIO: h.Light.model_construct(colour=h.LightColour.GREEN)
        },
    )
    reserve = h.Reserve.model_construct(
        amount=c("16830.00"),
        months=c("3.8"),
        need=c("4430.00"),
        target=c("26820.00"),
        target_months=c("6.0"),
        gap=c("9990.00"),
    )
    plan = pl.HouseholdPlan.model_construct(
        as_of=date(2027, 6, 3),
        levers=pl.HouseholdLevers.model_construct(bands=[]),
        surplus=pl.Surplus.model_construct(
            rows=[pl.SurplusRow.model_construct(key=k, amount=c("10.00")) for k in pl.SurplusKey]
        ),
        plans=[
            pl.SavingsPlan.model_construct(key="depot", destination_payee=None, asset_class="depot")
        ],
        already_invested=c("400.00"),
        investable_left=c("602.00"),
        reserve=reserve,
        paths=[
            pl.Path.model_construct(
                key="as_is", to_reserve_first_month=c("50.00"), reached="2029-04"
            ),
            pl.Path.model_construct(
                key="pause:depot", to_reserve_first_month=c("450.00"), reached="2028-08"
            ),
        ],
        projection=None,
    )
    got = facts_rules.build(
        health,
        plan,
        AdviceMode.OWN_USE,
        accounts=[facts_rules.AccountIn(label="Tagesgeld", kind="bank_savings")],
        plan_labels={"depot": "Depot Weltreise"},
    )
    value = {f.key: f.value for f in got.figures}
    assert value["net_worth"] == "128380.00"  # the last point with a total
    assert value["reserve_months"] == "3.8"  # the plan's reserve, health's being None
    assert value["ltv"] == "0.7388"
    assert value["debt_to_income"] == "4.20"
    assert value["basis_months"] == "3"
    assert value["path_pause_p1_reached"] == "2028-08"
    assert got.plans == {"p1": "depot"}
    assert got.labels.plans == {"p1": "Depot Weltreise"}
    assert got.states["trend_state"] == "down"
    assert got.states["basis_state"] == "short"
    assert got.states["reserve_state"] == "below"
    assert got.states["projection_state"] == "absent"
    assert "light_debt_service_ratio" not in got.states  # no confirmed band, no light state
    assert "Depot Weltreise" not in got.model_dump_json()

    # the projection branch: the last point and the typed rates
    point = pl.ProjectionPoint.model_construct(
        on=date(2032, 12, 31),
        total=c("257747.90"),
        change=pl.ProjectionChange.model_construct(
            total=c("30000.00"), from_assumptions=c("8000.00")
        ),
    )
    sid = UUID(int=1)
    projection = pl.NetWorthProjection.model_construct(
        points=[pl.ProjectionPoint.model_construct(on=date(2027, 6, 30), change=None), point],
        assumptions=[
            pl.ProjectionAssumption.model_construct(
                kind=pl.AssumptionKind.DEPOT_RATE, value=c("0.037"), scenario_id=sid
            ),
            pl.ProjectionAssumption.model_construct(
                kind=pl.AssumptionKind.FLAT_GROWTH, value=c("0.02"), scenario_id=sid
            ),
        ],
    )
    bands = [BandLimits(figure="debt_service_ratio", green=c("0.30"), red=c("0.40"))]
    shown = plan.model_copy(
        update={
            "projection": projection,
            "levers": pl.HouseholdLevers.model_construct(bands=bands),
        }
    )
    got = facts_rules.build(health, shown, AdviceMode.GENERIC)
    value = {f.key: f.value for f in got.figures}
    assert got.states["projection_state"] == "shown"
    assert value["projection_until"] == "2032-12"
    assert value["projection_total"] == "257747.90"
    assert value["projection_change"] == "30000.00"
    assert value["projection_assumption_part"] == "8000.00"
    assert value["assumed_change_depot"] == "0.037"
    assert value["flat_value_growth"] == "0.02"
    assert "assumed_change_crypto" not in value
    assert got.states["light_debt_service_ratio"] == "green"  # its band confirmed
    assert not (got.plans or got.labels.plans)  # generic


def test_figures_round_half_up_and_the_reserve_red_limit_in_whole_euros():
    """19 Nov 2026 (the step 4a review): a half cent goes up, and the reserve's red limit is
    whole euros, half up, from the unrounded product of its share and the target."""
    assert facts_rules.fact("free_cash", Decimal("0.125")).value == "0.13"
    assert facts_rules.fact("reserve_months", Decimal("2.25")).value == "2.3"
    assert facts_rules.fact("debt_to_income", Decimal("1.005")).value == "1.01"
    bands = [BandLimits(figure="reserve", red=Decimal("0.5"))]
    for target, limit in (("1001.00", "501.00"), ("1000.98", "500.00"), ("1234.57", "617.00")):
        got = facts_rules.assemble(
            AdviceMode.OWN_USE,
            VALIDATOR.facts.as_of,
            figures={},
            reserve=(Decimal("100.00"), Decimal(target)),
            bands=bands,
        )
        assert got.figure("band_reserve_red").value == limit, target
