"""The explanation's validator (docs/phases/phase-9.md §9, D255, D256). Fixtures:
tests/fixtures/phase9/explain_validator.yaml and instrument_names.yaml.

`validate(output, facts, mode)` runs the thirteen checks of §9.1 in their order over the text
as the model wrote it, before any rendering. A failing check does not stop the later ones:
every violation is returned, with its code and its position in the paragraph's NFKC form. The
validator is deterministic and fails closed: a word it does not know, a slot it cannot place,
a character outside its allow-list all reject. Its data are `advice_rules.yaml`,
`advice_lexicon.yaml` and `advice_vocabulary.txt`; `versions()` names the three, so a stored
explanation records what judged it (D257, §9.3).

Readings (recorded in docs/phases/phase-9.md §15.3, step 4a):

- A word is a maximal run of letters (any script, so a look-alike letter stays inside its
  word); a slot is masked before words are read, so it is never a word and it breaks a
  phrase. A sentence ends at `.`, `!` or `?`.
- Check 8b: a word that holds a product noun and is neither a product noun, a permitted
  compound nor a word of the vocabulary (an inflected form such as „Sparplänen“) is a
  compound outside the permitted list. The word directly before any word holding a product
  noun (whitespace only between them), when capitalised, and every word joined to one by a
  hyphen, must be a function word or a permitted compound.
- Check 12 requires the typed-rate slot and an assumption word in a `projection` paragraph
  (D255); the table of §9.1 lists their absence as the violation. A `projection` paragraph
  while `projection_state` is not `shown` is check 12's too (§8.3).
- The `light_*` rows of the contradiction table are check 13's and report `mark`.
- Amended 19 Nov 2026 (D255, D256 amended, the step 4a review): a text that is not NFKC-normal,
  or holds a NUL, fails check 1 as it stands. A slot stands apart (check 2): whitespace or the
  start of the text before it; whitespace, the end, or one of `.,;:!?` followed by whitespace or
  the end after it; no `mal`, `plus` or `minus` between two figure slots of a sentence. Check
  8b's join: a gap of hyphens, apostrophes, colons, quotation marks or brackets (spaces
  allowed) joins two words, a comma never. Check 9's descriptive list is read per paragraph and
  reports `recommendation`. Checks 9, 12 and 13 read the recommendation and the transaction
  words as one list. A contradiction row without a topic holds in every paragraph; the lights'
  rows judge a paragraph by the lights of the marked figures it slots, else by every light.
  Check 12 reports the future and forecast words outside a `projection` paragraph as
  `projection`; check 13 reports a colour word it cannot tie as `mark`. Check 11 needs a word
  outside the slots in every paragraph.
- When the facts' mode and the mode passed differ, the stricter one (`generic`) applies.
- `output` may be an `ExplainOutput` or a plain list of paragraphs, so that check 11 can name a
  fifth paragraph or a long one that the schema itself would refuse.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache

from hausbuch.rules.advice import lexicon, rules, vocabulary
from hausbuch.schemas.advice import (
    PROJECTION_KEYS,
    TYPED_RATE_KEYS,
    AdviceMode,
    ExplainFacts,
    ExplainOutput,
    Paragraph,
    StateValue,
    Topic,
    ValidationResult,
    Violation,
    ViolationCode,
)

V = ViolationCode
_MASK = "\x00"  # a slot's characters once masked: not a letter, not a space, not a dot
_WORD = re.compile(r"[^\W\d_]+")
_DIGIT = re.compile(r"\d")
_DOT_IN_TOKEN = re.compile(r"[^\W_]\.[^\W_]")
_SENTENCE_END = re.compile(r"[.!?]")
_SINGLE_LETTER_GAP = re.compile(r"^[\s\-–.]+$")
_HYPHEN = re.compile(r"^[-–]$")
_SQUASHED = re.compile(r"[\s\-–.]")
_COLOURED = frozenset({StateValue.GREEN, StateValue.AMBER, StateValue.RED})
_JOIN = re.compile(r"^\s*[-–'\"„“”():]+(?:\s*[-–'\"„“”():]+)*\s*$")
_AFTER_SLOT = re.compile(r"[.,;:!?](?:\s|$)")
_SLOT_OPERATORS = frozenset({"mal", "plus", "minus"})


def versions() -> dict[str, str]:
    """The versions of the validator's three data files, stored with every explanation."""
    return {
        "validator": rules()["version"],
        "lexicon": lexicon()[0],
        "vocabulary": vocabulary()[0],
    }


# --- The data, prepared once ------------------------------------------------------------


@dataclass(frozen=True)
class _Data:
    allowed: frozenset[str]
    slot: re.Pattern[str]
    slots_by_mode: dict[str, frozenset[str]]
    short_upper: frozenset[str]
    single_letter_run: int
    link_markers: frozenset[str]
    squash_min: int
    number_words: frozenset[str]
    product_nouns: tuple[str, ...]
    permitted: frozenset[str]
    function_words: frozenset[str]
    recommendation: frozenset[str]
    transaction: frozenset[str]
    advice: frozenset[str]  # the two lists as one (D256 amended 19 Nov 2026)
    descriptive: frozenset[str]
    colours: tuple[tuple[frozenset[StateValue], frozenset[str]], ...]
    assumption: tuple[str, ...]
    negated_assumption: tuple[str, ...]
    future: tuple[str, ...]
    forecast: tuple[str, ...]
    future_phrases: tuple[str, ...]
    judging: tuple[str, ...]
    contradictions: tuple[dict, ...]
    marked_slots: dict[str, frozenset[str]]
    paragraphs_max: int
    paragraph_chars: int
    words: frozenset[str]
    names: frozenset[str]


@cache
def _data() -> _Data:
    r = rules()
    chars = r["allowed_characters"]
    shape = r["shape"]
    return _Data(
        allowed=frozenset(chars["letters"] + chars["other"]),
        slot=re.compile(r["slot_pattern"]),
        slots_by_mode={m: frozenset(s) for m, s in r["slots_by_mode"].items()},
        short_upper=frozenset(r["short_upper_case"]),
        single_letter_run=int(r["single_letter_run"]),
        link_markers=frozenset(r["link_markers"]),
        squash_min=int(r["squash_min_length"]),
        number_words=frozenset(r["number_words"]) | frozenset(r["fraction_words"]),
        product_nouns=tuple(r["product_nouns"]),
        permitted=frozenset(r["permitted_compounds"]),
        function_words=frozenset(r["function_words"]),
        recommendation=frozenset(r["recommendation_words"]),
        transaction=frozenset(r["transaction_words"]),
        advice=frozenset(r["recommendation_words"]) | frozenset(r["transaction_words"]),
        descriptive=frozenset(r["descriptive_words"]),
        colours=tuple(
            (frozenset(StateValue(x) for x in row["lights"]), frozenset(row["words"]))
            for row in r["colour_words"]
        ),
        assumption=tuple(r["assumption_words"]),
        negated_assumption=tuple(r["negated_assumptions"]),
        future=tuple(r["future_auxiliaries"]),
        forecast=tuple(r["forecast_words"]),
        future_phrases=tuple(r["future_phrases"]),
        judging=tuple(r["judging_words"]),
        contradictions=tuple(r["contradictions"]),
        marked_slots={k: frozenset(v) for k, v in r["marked_figure_slots"].items()},
        paragraphs_max=int(shape["paragraphs_max"]),
        paragraph_chars=int(shape["paragraph_max_characters"]),
        words=vocabulary()[1],
        names=lexicon()[1],
    )


# --- One paragraph, read once -----------------------------------------------------------


@dataclass(frozen=True)
class _Slot:
    start: int
    end: int
    letter: str
    key: str


@dataclass(frozen=True)
class _Word:
    start: int
    end: int
    text: str

    @property
    def lower(self) -> str:
        return self.text.lower()


@dataclass(frozen=True)
class _Read:
    index: int
    topic: Topic
    raw: str
    text: str  # NFKC
    masked: str  # the slots' characters replaced by _MASK
    slots: tuple[_Slot, ...]
    words: tuple[_Word, ...]
    sentences: tuple[tuple[int, int], ...]


def _read(index: int, paragraph: Paragraph, data: _Data) -> _Read:
    text = unicodedata.normalize("NFKC", paragraph.text)
    slots = tuple(
        _Slot(m.start(), m.end(), m.group(1), m.group(2)) for m in data.slot.finditer(text)
    )
    chars = list(text)
    for slot in slots:
        chars[slot.start : slot.end] = _MASK * (slot.end - slot.start)
    masked = "".join(chars)
    words = tuple(_Word(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(masked))
    sentences, start = [], 0
    for m in _SENTENCE_END.finditer(masked):
        sentences.append((start, m.end()))
        start = m.end()
    if start < len(masked):
        sentences.append((start, len(masked)))
    return _Read(
        index, paragraph.topic, paragraph.text, text, masked, slots, words, tuple(sentences)
    )


def _v(code: ViolationCode, read: _Read, start: int | None = None, end: int | None = None):
    return Violation(code=code, paragraph=read.index, start=start, end=end)


def _phrase(phrase: str) -> re.Pattern[str]:
    """A phrase matched as whole words on lower-cased text; a slot breaks it."""
    body = r"\s+".join(re.escape(part) for part in phrase.split())
    return re.compile(rf"(?<![^\W_]){body}(?![^\W_])")


@cache
def _phrases(entries: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(_phrase(e) for e in entries)


def _found(patterns: Iterable[re.Pattern[str]], lower: str) -> list[re.Match[str]]:
    return [m for p in patterns for m in p.finditer(lower)]


def _in_span(start: int, span: tuple[int, int]) -> bool:
    return span[0] <= start < span[1]


# --- The checks, in the order of §9.1 ---------------------------------------------------


def _characters(read: _Read, data: _Data) -> list[Violation]:  # 1
    out = []
    if read.raw != read.text:  # not NFKC-normal: rejected as written, never read as its form
        out.append(_v(V.CHARACTER, read))
    for i, ch in enumerate(read.text):  # a NUL is found before the slots are masked
        if ch == _MASK:
            out.append(_v(V.CHARACTER, read, i, i + 1))
    for i, ch in enumerate(read.masked):
        if ch != _MASK and ch not in data.allowed:
            out.append(_v(V.CHARACTER, read, i, i + 1))
    return out


def _slots(read: _Read, facts: ExplainFacts, mode: AdviceMode, data: _Data) -> list[Violation]:
    out = []  # 2
    for i, ch in enumerate(read.masked):
        if ch in "{}":
            out.append(_v(V.SLOT, read, i, i + 1))
    held = {
        "f": {f.key for f in facts.figures},
        "k": set(facts.accounts),
        "p": set(facts.plans),
        "a": set(facts.assets),
        "c": {c.value for c in facts.classes},
    }
    allowed = data.slots_by_mode[mode.value]
    for slot in read.slots:
        if slot.letter not in allowed or slot.key not in held[slot.letter]:
            out.append(_v(V.SLOT, read, slot.start, slot.end))
    return out + _slot_bounds(read)


def _slot_bounds(read: _Read) -> list[Violation]:
    """A slot stands apart (D255 amended 19 Nov 2026): whitespace or the text's start before
    it; whitespace, the end, or sentence punctuation followed by either after it."""
    out = []
    text = read.text
    for slot in read.slots:
        if slot.start > 0 and not text[slot.start - 1].isspace():
            out.append(_v(V.SLOT, read, slot.start - 1, slot.end))
        after = text[slot.end :]
        if after and not after[0].isspace() and not _AFTER_SLOT.match(after):
            out.append(_v(V.SLOT, read, slot.start, slot.end + 1))
    figures = [s for s in read.slots if s.letter == "f"]
    for left, right in zip(figures, figures[1:], strict=False):
        gap = read.masked[left.end : right.start]
        if _SENTENCE_END.search(gap):
            continue
        words = {m.group(0).lower() for m in _WORD.finditer(gap)}
        if words & _SLOT_OPERATORS:  # a figure is never computed in the text
            out.append(_v(V.SLOT, read, left.start, right.end))
    return out


def _digits(read: _Read) -> list[Violation]:  # 3
    return [_v(V.DIGIT, read, m.start(), m.end()) for m in _DIGIT.finditer(read.masked)]


def _number_words(read: _Read, data: _Data) -> list[Violation]:  # 4
    return [
        _v(V.NUMBER_WORD, read, w.start, w.end) for w in read.words if w.lower in data.number_words
    ]


def _abbreviations(read: _Read, data: _Data) -> list[Violation]:  # 5
    out = []
    for w in read.words:
        if 2 <= len(w.text) <= 6 and w.text.isupper() and w.text not in data.short_upper:
            out.append(_v(V.ABBREVIATION, read, w.start, w.end))
    run: list[_Word] = []

    def close() -> None:
        if len(run) >= data.single_letter_run:
            out.append(_v(V.ABBREVIATION, read, run[0].start, run[-1].end))

    for w in read.words:
        if len(w.text) == 1:
            if run and not _SINGLE_LETTER_GAP.match(read.masked[run[-1].end : w.start]):
                close()
                run = []
            run.append(w)
        else:
            close()
            run = []
    close()
    return out


def _links(read: _Read, data: _Data) -> list[Violation]:  # 6
    out = [_v(V.LINK, read, w.start, w.end) for w in read.words if w.lower in data.link_markers]
    out += [_v(V.LINK, read, m.start(), m.end()) for m in _DOT_IN_TOKEN.finditer(read.masked)]
    return out


def _squash(text: str) -> tuple[str, list[int]]:
    """Lower-cased, with spaces, hyphens, dots and format characters removed; the index of
    each kept character in the text. A masked slot is kept, so it joins nothing."""
    kept, where = [], []
    for i, ch in enumerate(text):
        if _SQUASHED.match(ch) or unicodedata.category(ch) == "Cf":
            continue
        kept.append(ch.lower())
        where.append(i)
    return "".join(kept), where


def _lexicon(read: _Read, data: _Data, names: frozenset[str]) -> list[Violation]:  # 7
    out = []
    squashed, where = _squash(read.masked)
    lower = read.masked.lower()
    unhyphenated = re.sub(r"[-–]", "", read.masked).lower()
    for name in sorted(names):
        key = _squash(name)[0]
        if not key:
            continue
        if len(key) >= data.squash_min:
            start = squashed.find(key)
            while start != -1:
                out.append(_v(V.LEXICON, read, where[start], where[start + len(key) - 1] + 1))
                start = squashed.find(key, start + 1)
        else:
            pattern = _phrase(name)
            hits = list(pattern.finditer(lower))
            if not hits:
                hits = list(pattern.finditer(unhyphenated))
                if hits:  # positions in the text without hyphens: the paragraph only
                    out.append(_v(V.LEXICON, read))
                    continue
            out += [_v(V.LEXICON, read, m.start(), m.end()) for m in hits]
    return out


def _compound(word: str, words: frozenset[str], parts_left: int = 4) -> bool:
    """A full German compound of known words: parts of at least three letters, the linking
    letters s, es, n, en, e between parts, at most four parts (check 8)."""
    return _split(word, words, parts_left)


@cache
def _links_between() -> tuple[str, ...]:
    return ("", "s", "es", "n", "en", "e")


def _split(word: str, words: frozenset[str], parts_left: int) -> bool:
    if parts_left < 1:
        return False
    if len(word) >= 3 and word in words:
        return True
    if parts_left < 2:
        return False
    for cut in range(3, len(word) - 2):
        head = word[:cut]
        if head not in words:
            continue
        rest = word[cut:]
        for link in _links_between():
            if (
                rest.startswith(link)
                and len(rest) - len(link) >= 3
                and _split(rest[len(link) :], words, parts_left - 1)
            ):
                return True
    return False


def _vocabulary(read: _Read, data: _Data) -> list[Violation]:  # 8
    out = []
    for w in read.words:
        lower = w.lower
        if lower in data.words or _compound(lower, data.words):
            continue
        out.append(_v(V.VOCABULARY, read, w.start, w.end))
    return out


def _holds_product(lower: str, data: _Data) -> bool:
    return any(noun in lower for noun in data.product_nouns)


def _product_nouns(read: _Read, data: _Data) -> list[Violation]:  # 8b
    out = []
    words = read.words
    for i, w in enumerate(words):
        lower = w.lower
        if not _holds_product(lower, data):
            continue
        if not (lower in data.product_nouns or lower in data.permitted or lower in data.words):
            out.append(_v(V.PRODUCT_NOUN, read, w.start, w.end))
        if i > 0:
            before = words[i - 1]
            gap = read.masked[before.end : w.start]
            fine = before.lower in data.function_words or before.lower in data.permitted
            if gap.isspace() and before.text[:1].isupper() and not fine:
                out.append(_v(V.PRODUCT_NOUN, read, before.start, w.end))
            if _JOIN.match(gap) and not fine:
                out.append(_v(V.PRODUCT_NOUN, read, before.start, w.end))
        if i + 1 < len(words):
            after = words[i + 1]
            gap = read.masked[w.end : after.start]
            fine = after.lower in data.function_words or after.lower in data.permitted
            if _JOIN.match(gap) and not fine:
                out.append(_v(V.PRODUCT_NOUN, read, w.start, after.end))
    return out


def _sentence_words(read: _Read, span: tuple[int, int]) -> list[_Word]:
    return [w for w in read.words if _in_span(w.start, span)]


def _sentence_slots(read: _Read, span: tuple[int, int]) -> list[_Slot]:
    return [s for s in read.slots if _in_span(s.start, span)]


def _lit_slots(facts: ExplainFacts, data: _Data) -> set[str]:
    """The figure keys whose light is green, amber or red."""
    lit: set[str] = set()
    for light, keys in data.marked_slots.items():
        if facts.states.get(light) in _COLOURED:
            lit |= keys
    return lit


def _descriptive_only(read: _Read, facts: ExplainFacts, data: _Data) -> bool:
    """A paragraph about what the household holds, a lit figure or the projection uses only
    the descriptive words (D256 amended 19 Nov 2026)."""
    if read.topic is Topic.PROJECTION:
        return True
    lit = _lit_slots(facts, data)
    return any(s.letter in "ac" or (s.letter == "f" and s.key in lit) for s in read.slots)


def _recommendations(read: _Read, facts: ExplainFacts, data: _Data) -> list[Violation]:  # 9
    out = []
    for span in read.sentences:
        if not any(s.letter == "a" for s in _sentence_slots(read, span)):
            continue
        for w in _sentence_words(read, span):
            if w.lower in data.advice:
                out.append(_v(V.RECOMMENDATION, read, w.start, w.end))
    if _descriptive_only(read, facts, data):
        for w in read.words:
            if w.lower not in data.descriptive:
                out.append(_v(V.RECOMMENDATION, read, w.start, w.end))
    return out


def _contradictions(read: _Read, facts: ExplainFacts, data: _Data) -> list[Violation]:
    """Checks 10 and 13's rows: per topic and state, phrases that may not appear."""
    out = []
    lower = read.masked.lower()
    slotted = _marked_lights(read, data)
    for row in data.contradictions:
        if row.get("topic", read.topic.value) != read.topic.value:
            continue
        if facts.states.get(row["state"]) != row["value"]:
            continue
        is_light = row["state"].startswith("light_")
        if is_light and slotted and row["state"] not in slotted:
            continue  # a paragraph slotting a marked figure is judged by its own light
        code = V.MARK if is_light else V.CONTRADICTION
        for m in _found(_phrases(tuple(row["phrases"])), lower):
            out.append(_v(code, read, m.start(), m.end()))
    return out


def _marked_lights(read: _Read, data: _Data) -> set[str]:
    """The lights of the marked figures a paragraph slots."""
    return {
        light
        for light, keys in data.marked_slots.items()
        for s in read.slots
        if s.letter == "f" and s.key in keys
    }


def _shape(
    paragraphs: Sequence[Paragraph], reads: Sequence[_Read], data: _Data
) -> list[Violation]:  # 11
    out = []
    if not paragraphs:
        return [Violation(code=V.SHAPE, paragraph=0)]
    for read in reads:
        if not read.words:  # a word outside the slots (D255 amended 19 Nov 2026)
            out.append(_v(V.SHAPE, read))
    if len(paragraphs) > data.paragraphs_max:
        out.append(Violation(code=V.SHAPE, paragraph=data.paragraphs_max))
    seen: set[Topic] = set()
    for i, p in enumerate(paragraphs):
        if len(p.text) > data.paragraph_chars:
            out.append(Violation(code=V.SHAPE, paragraph=i, start=data.paragraph_chars))
        if p.topic in seen:
            out.append(Violation(code=V.SHAPE, paragraph=i))
        seen.add(p.topic)
    return out


def _projection(read: _Read, facts: ExplainFacts, data: _Data) -> list[Violation]:  # 12
    out = []
    lower = read.masked.lower()
    if read.topic is not Topic.PROJECTION:
        for s in read.slots:
            if s.letter == "f" and s.key in PROJECTION_KEYS:
                out.append(_v(V.PROJECTION, read, s.start, s.end))
        # the future is the projection's alone (D255 amended 19 Nov 2026)
        banned = data.future + data.forecast + data.future_phrases
        for m in _found(_phrases(banned), lower):
            out.append(_v(V.PROJECTION, read, m.start(), m.end()))
        return out
    if facts.states.get("projection_state") != StateValue.SHOWN:
        out.append(_v(V.PROJECTION, read))
    if not any(s.letter == "f" and s.key in TYPED_RATE_KEYS for s in read.slots):
        out.append(_v(V.PROJECTION, read))
    if not _found(_phrases(data.assumption), lower):
        out.append(_v(V.PROJECTION, read))
    banned = data.future + data.forecast + data.future_phrases + data.judging
    banned += data.negated_assumption
    for m in _found(_phrases(banned), lower):
        out.append(_v(V.PROJECTION, read, m.start(), m.end()))
    for w in read.words:
        if w.lower in data.advice:
            out.append(_v(V.PROJECTION, read, w.start, w.end))
    for s in read.slots:
        if s.letter in "apk":
            out.append(_v(V.PROJECTION, read, s.start, s.end))
    return out


def _marks(read: _Read, facts: ExplainFacts, data: _Data) -> list[Violation]:  # 13
    marked = _lit_slots(facts, data)
    out = []
    for span in read.sentences:
        slots = _sentence_slots(read, span)
        if not any(s.letter == "c" or (s.letter == "f" and s.key in marked) for s in slots):
            continue
        for w in _sentence_words(read, span):
            if w.lower in data.advice:
                out.append(_v(V.MARK, read, w.start, w.end))
    # a colour word is tied to the lit figures its paragraph slots, each of its colour
    lights = {facts.states.get(light) for light in _marked_lights(read, data)}
    for w in read.words:
        for colours, words in data.colours:
            if w.lower in words and (not lights or not lights <= colours):
                out.append(_v(V.MARK, read, w.start, w.end))
    return out


# --- The validator ----------------------------------------------------------------------


def validate(
    output: ExplainOutput | Sequence[Paragraph],
    facts: ExplainFacts,
    mode: AdviceMode,
    *,
    extra_lexicon: Iterable[str] = (),
) -> ValidationResult:
    """The thirteen checks over the model's output and its facts; fails closed.

    `extra_lexicon` adds names for a test only (explain_validator.yaml's invented issuer and
    coin); the shipped lexicon is never changed by it."""
    data = _data()
    paragraphs = list(output.paragraphs if isinstance(output, ExplainOutput) else output)
    if AdviceMode.GENERIC in (mode, facts.mode):
        mode = AdviceMode.GENERIC
    names = data.names | frozenset(n.lower() for n in extra_lexicon)
    reads = [_read(i, p, data) for i, p in enumerate(paragraphs)]
    found: dict[ViolationCode, list[Violation]] = {code: [] for code in ViolationCode}
    for read in reads:
        found[V.CHARACTER] += _characters(read, data)
        found[V.SLOT] += _slots(read, facts, mode, data)
        found[V.DIGIT] += _digits(read)
        found[V.NUMBER_WORD] += _number_words(read, data)
        found[V.ABBREVIATION] += _abbreviations(read, data)
        found[V.LINK] += _links(read, data)
        found[V.LEXICON] += _lexicon(read, data, names)
        found[V.VOCABULARY] += _vocabulary(read, data)
        found[V.PRODUCT_NOUN] += _product_nouns(read, data)
        found[V.RECOMMENDATION] += _recommendations(read, facts, data)
        for v in _contradictions(read, facts, data):
            found[v.code].append(v)
        found[V.PROJECTION] += _projection(read, facts, data)
        found[V.MARK] += _marks(read, facts, data)
    found[V.SHAPE] += _shape(paragraphs, reads, data)
    return ValidationResult(violations=[v for code in ViolationCode for v in found[code]])
