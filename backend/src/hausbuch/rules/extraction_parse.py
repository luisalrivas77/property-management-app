"""Parsing an extract response into ExtractedValues (fixture phase2/extraction_parse.yaml).

Pure: no I/O, no model calls. Spec: docs/phases/phase-2.md D15, §5.2, §5.3; the value kinds and
their stored forms are in hausbuch/schemas/extraction.py.

The extract call uses citations, so it cannot use JSON structured outputs (the API returns 400
for the combination). The prompt asks for one `field_key: value` line per field instead; this
module keys the lines, attaches the page_location citation of the text that produced each
line, normalizes the document's own form (German amounts, dates, percentages), and derives the
confidence from the checks that passed. The confidence is never a number the model reported.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import partial

from hausbuch.schemas.ai import CitedTextBlock, ExtractedValue, SourceRef
from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import (
    COST_LINE_CODES,
    EXTRACTION_MODELS,
    TENANCY_GROUP,
    ValueKind,
    asked_fields,
    value_kind,
)

from .errors import RuleError
from .iban import normalize_iban

UNKNOWN = "unbekannt"

_KEY_LINE = re.compile(r"([a-z0-9_]+):[ \t]*(.*)")
_NUMBER = re.compile(r"[+-]?[0-9][0-9.,]*[0-9]|[+-]?[0-9]")
_THOUSANDS_ONLY = re.compile(r"[+-]?[0-9]{1,3}(\.[0-9]{3})+")
# A minus written apart from the number (D129 review): "./. 5.000", "- 5.000,00", "−5.000"
# (U+2212) at the start of the value, or "5.000,00-" at its end.
_SEPARATE_SIGN = re.compile(r"(?:\./\.|[-\u2212]) ?(?=[0-9])")
_TRAILING_MINUS = re.compile(r"[0-9]-$")
_NUMERIC_DATE = re.compile(r"([0-9]{1,2})\.\s?([0-9]{1,2})\.\s?([0-9]{4})")
_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
# A two-digit year, as handwritten by a signature ("Ort, den 30.3.26", Luis's Darlehensvertrag,
# 8 Nov 2026, D47): 00-69 -> 20yy, 70-99 -> 19yy (the POSIX strptime pivot). Tried only after
# the four-digit form, and never inside a longer number.
_SHORT_DATE = re.compile(r"(?<![0-9])([0-9]{1,2})\.\s?([0-9]{1,2})\.\s?([0-9]{2})(?![0-9])")
_CENTURY_PIVOT = 70
_WORD_DATE = re.compile(r"([0-9]{1,2})\.?\s+([A-Za-zÄÖÜäöü]+)\s+([0-9]{4})")
_YEAR = re.compile(r"(?<![0-9])[0-9]{4}(?![0-9])")
_FRACTION = re.compile(r"([0-9][0-9.,]*)\s*/\s*([0-9][0-9.,]*)")
_IBAN_IN_TEXT = re.compile(r"[A-Za-z]{2}[0-9]{2}(?:\s?[A-Za-z0-9]){10,32}")
_DIGITS = re.compile(r"[0-9]")
# A trailing "(Seite 1)", "(S. 2)", "(Seite 1, § 1)": the model's page reference, not the value.
_PAGE_REFERENCE = re.compile(r"\s*\((?:seite|s\.)\s*[0-9]+[^()]*\)\s*$", re.IGNORECASE)
# A loan number's label the model wrote into contract_number ("Kontonummer 1234567890", Luis's
# Sparkasse contract, 8 Nov 2026): Kontonummer, Konto-Nr., Darlehenskontonummer,
# Darlehensnummer, Darlehens-Nr., Vertragsnummer, Vertrags-Nr. or Nr.
_NUMBER_LABEL = r"(?:(?:darlehens)?konto|darlehens|vertrags)\s*-?\s*(?:nummer|nr\b\.?)|nr\b\.?"
# The label at the start, with an optional colon; it must be followed by a space, a colon, the
# end, or end in a dot, so "NR-2026-1" keeps its "NR".
_LEADING_LABEL = re.compile(rf"^(?:{_NUMBER_LABEL})(?:\s*:\s*|\s+|(?<=\.)|$)", re.IGNORECASE)
_LABEL_INSIDE = re.compile(rf"\b(?:{_NUMBER_LABEL})", re.IGNORECASE)
_TRAILING_PARENTHETICAL = re.compile(r"\s*\(([^()]*)\)\s*$")
_LABELLED_FIELDS = frozenset({"contract_number"})

_MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11,
    "dezember": 12,
}  # fmt: skip

_NUMERIC_KINDS: frozenset[ValueKind] = frozenset(
    {"money", "decimal", "rate", "date", "year", "fraction", "iban"}
)


class _Invalid(ValueError):
    """The raw value does not normalize to its kind, or fails the field's type."""


# --- Lines and citations ----------------------------------------------------------------


def parse_lines(
    blocks: list[CitedTextBlock],
    kind: ExtractionKind,
    *,
    page_offset: int = 0,
    reference_year: int | None = None,
    subtype: DocumentSubtype | None = None,
    properties: tuple["PropertyAddress", ...] = (),
) -> dict[str, ExtractedValue]:
    """The fields an extract response stated, keyed by field name.

    Only keys of EXTRACTION_MODELS[kind] count; a repeated key: the last line wins. A field
    the response did not mention is not in the result (the caller fills it with
    ExtractedValue.missing()). page_offset is the first page of the request's chunk minus 1,
    for a PDF split into several requests. For the owners' tax documents (SCRUBBED_KINDS: the
    two pay documents and the Einkommensteuerbescheid) the lines and every quote are scrubbed
    of identifiers first (scrub_identifiers, D103, D129), with
    reference_year (the job clock's year; default: this year) for the birth-date rule. The
    general reading and a Verwaltervertrag (GENERAL_SCRUBBED_KINDS) get scrub_general on
    every line and every quote (D131, decided 10 Nov 2026). Only `properties`, the
    workspace's own addresses, are kept: an address the request asked for (asked_fields with
    `subtype`) keeps its value only when it is a property's, else its value goes and its
    cited source stays (the pre-fill then links nothing); a contract number shaped like an
    IBAN or a Steuernummer is not read; a line the model volunteered keeps nothing (PR #135
    review).
    """
    model = EXTRACTION_MODELS[kind]
    scrub = kind in SCRUBBED_KINDS
    general = kind in GENERAL_SCRUBBED_KINDS
    lines = _lines(blocks)
    if scrub:
        # Scrubbed together, so a value under a line of labels is seen; the scrub keeps lines.
        texts = scrubber(kind, reference_year=reference_year)(
            "\n".join(text for text, _ in lines)
        ).split("\n")
        lines = [(text, contributors) for text, (_, contributors) in zip(texts, lines, strict=True)]
    # A line for a field the request did not ask (a hand-only field, or a link field the
    # subtype does not need) is never a value.
    link, asked = NO_LINK, frozenset(asked_fields(kind, subtype))
    if general:
        link = link_values([text for text, _ in lines], asked, properties)
        texts = scrub_general_answer("\n".join(text for text, _ in lines), asked, properties).split(
            "\n"
        )
        lines = [(text, contributors) for text, (_, contributors) in zip(texts, lines, strict=True)]
    result: dict[str, ExtractedValue] = {}
    for text, contributors in lines:
        match = _KEY_LINE.fullmatch(text)
        if match is None or match.group(1) not in model.model_fields:
            continue
        key, raw = match.group(1), _without_page_reference(match.group(2).strip())
        if key in _LABELLED_FIELDS:
            raw = _without_number_label(raw)
        if not raw or raw.casefold() == UNKNOWN or key not in asked:
            result[key] = ExtractedValue.missing()
            continue
        source = _source(blocks, contributors, page_offset)
        if scrub and source is not None:
            source = source.model_copy(
                update={"quote": scrubber(kind, reference_year=reference_year)(source.quote)}
            )
        if general and source is not None:
            source = source.model_copy(
                update={"quote": scrub_general(source.quote, properties, link.contract_number)}
            )
        if general and key == "contract_number" and link.contract_number is None:
            result[key] = ExtractedValue.missing()  # an identifier read as the number
            continue
        if general and key in _ADDRESS_FIELDS and not link.address_matches:
            # A stated address that is no property's; a line the model wrote "unbekannt" (now
            # "…" like every unkept address line) is simply missing.
            read = key in link.address_read
            result[key] = _unmatched_address(source) if read else ExtractedValue.missing()
            continue
        result[key] = _extracted(raw, value_kind(model, key), source)
    return result


def _unmatched_address(source: SourceRef | None) -> ExtractedValue:
    """An address line of a general reading that is no property's (an owner's home read as
    the property): its value goes, and the cited source stays, so the pre-fill knows an
    address was read and links nothing (prefill.address_unmatched)."""
    if source is None:
        return ExtractedValue.missing()
    return ExtractedValue(value=None, source=source, checks=["cited"], confidence="low")


def _without_page_reference(raw: str) -> str:
    """The value without a page reference the model appended to it ("Lena Schmidt (Seite
    1)", "500,00 € (S. 2)"): only a parenthetical at the very end that starts with Seite or
    S. and a number. The page is the citation's; left in, it would fail value_in_quote. A
    value that is only a page reference becomes empty, so the field is missing."""
    return _PAGE_REFERENCE.sub("", raw)


def _without_number_label(raw: str) -> str:
    """A contract number without the label the model wrote into it: a trailing parenthetical
    with no digit or naming a label ("1234567890 (Kontonummer)"), then a leading label with an
    optional colon ("Kontonummer 1234567890", "Darlehens-Nr.: 4711"). The identifier stays as
    written; a parenthetical with a digit that names no label ("(Teil 2)") stays too. Only for
    contract_number: elsewhere brackets and words like these are part of the value."""
    trailing = _TRAILING_PARENTHETICAL.search(raw)
    if trailing is not None:
        inside = trailing.group(1)
        if not _DIGITS.search(inside) or _LABEL_INSIDE.search(inside):
            raw = raw[: trailing.start()]
    return _LEADING_LABEL.sub("", raw.strip(), count=1).strip()


def _lines(blocks: list[CitedTextBlock]) -> list[tuple[str, list[int]]]:
    """The concatenated text split on newlines; per line, the indexes of the blocks that
    contributed a character to it, in order."""
    lines: list[tuple[str, list[int]]] = []
    chars: list[str] = []
    contributors: list[int] = []
    for index, block in enumerate(blocks):
        for ch in block.text:
            if ch == "\n":
                lines.append(("".join(chars).rstrip("\r"), contributors))
                chars, contributors = [], []
                continue
            chars.append(ch)
            if not contributors or contributors[-1] != index:
                contributors.append(index)
    lines.append(("".join(chars).rstrip("\r"), contributors))
    return lines


def _source(
    blocks: list[CitedTextBlock], contributors: list[int], page_offset: int
) -> SourceRef | None:
    for index in contributors:
        citations = blocks[index].citations
        if citations:
            first = citations[0]
            return SourceRef(page=first.start_page_number + page_offset, quote=first.cited_text)
    return None


def _extracted(raw: str, kind: ValueKind, source: SourceRef | None) -> ExtractedValue:
    try:
        value = normalize_value(raw, kind)
        valid = True
    except _Invalid:
        value, valid = _collapse(raw), False
    checks: list = []
    if source is not None:
        checks.append("cited")
        if _in_quote(raw, value if valid else None, kind, source.quote):
            checks.append("value_in_quote")
    if valid:
        checks.append("validates")
    if checks == ["cited", "value_in_quote", "validates"]:
        confidence = "high"
    elif checks == ["cited", "validates"]:
        confidence = "medium"
    else:
        confidence = "low"
    return ExtractedValue(value=value, source=source, checks=checks, confidence=confidence)


def _in_quote(raw: str, value, kind: ValueKind, quote: str) -> bool:
    if kind in _NUMERIC_KINDS:
        digits = _digits(raw)
        return bool(digits) and digits in _digits(quote)
    haystack = _collapse(quote).casefold()
    if kind == "text_list" and isinstance(value, list):
        items = value
    else:
        items = [value if isinstance(value, str) else _collapse(raw)]
    return bool(items) and all(_collapse(item).casefold() in haystack for item in items)


def _digits(text: str) -> str:
    return "".join(_DIGITS.findall(text))


def _collapse(text: str) -> str:
    return " ".join(text.split())


# --- Values -------------------------------------------------------------------------------


def normalize_value(raw: str, kind: ValueKind):
    """The stored form of a value written in the document's own form (schemas/extraction.py).
    Raises ValueError (_Invalid) when it does not normalize or fails the field's type."""
    match kind:
        case "money":
            amount = _decimal(raw)
            if amount.as_tuple().exponent < -2:  # type: ignore[operator]
                raise _Invalid("an amount has at most two decimal places")
            return str(amount.quantize(Decimal("0.01")))
        case "decimal":
            return _plain(_decimal(raw))
        case "rate":
            rate = _decimal(raw) / 100
            if not Decimal(0) <= rate < Decimal(1):
                raise _Invalid("a rate is in [0, 1)")
            return _plain(rate.normalize())
        case "date":
            return _date(raw).isoformat()
        case "year":
            match_ = _YEAR.search(raw)
            if match_ is None or not 1800 <= int(match_.group()) <= 2100:
                raise _Invalid("a year is between 1800 and 2100")
            return int(match_.group())
        case "fraction":
            return _fraction(raw)
        case "iban":
            return _iban(raw)
        case "text":
            text = _collapse(raw)
            if not text:
                raise _Invalid("empty text")
            return text
        case "text_list":
            items = [_collapse(item) for item in raw.split(";")]
            items = [item for item in items if item]
            if not items:
                raise _Invalid("empty list")
            return items
    raise _Invalid(f"unknown value kind {kind}")


def _decimal(raw: str) -> Decimal:
    """The first number in the text, German form first: a comma is the decimal separator and
    dots group thousands; without a comma, dots in groups of three are thousands.

    A negative may also be written with the sign apart from the digits, as an
    Einkommensteuerbescheid prints a negative zvE ("./. 5.000"), or as "- 5.000,00", "−5.000"
    or "5.000,00-". Only a sign at the very start (at most one space before the digits) or at
    the very end of the value counts, so a dash in running text never turns into a sign."""
    text = raw.replace("\u00a0", " ").strip()
    negative = False
    lead = _SEPARATE_SIGN.match(text)
    if lead is not None:
        negative, text = True, text[lead.end() :]
    elif _TRAILING_MINUS.search(text):
        negative, text = True, text[:-1]
    match = _NUMBER.search(text)
    if match is None:
        raise _Invalid("no number")
    token = match.group()
    if "," in token:
        if token.count(",") > 1:
            raise _Invalid("more than one decimal comma")
        whole, frac = token.split(",")
        if "." in frac or ("." in whole and not _THOUSANDS_ONLY.fullmatch(whole)):
            raise _Invalid("malformed thousands grouping")
        token = whole.replace(".", "") + "." + frac
    elif _THOUSANDS_ONLY.fullmatch(token):
        token = token.replace(".", "")
    elif token.count(".") > 1:
        raise _Invalid("malformed number")
    try:
        number = Decimal(token)
    except InvalidOperation:
        raise _Invalid("not a number") from None
    if not number.is_finite():
        raise _Invalid("not a finite number")
    return -number if negative and number > 0 else number


def _plain(number: Decimal) -> str:
    return format(number, "f")


def _date(raw: str) -> date:
    for pattern, order in ((_NUMERIC_DATE, "dmy"), (_ISO_DATE, "ymd")):
        match = pattern.search(raw)
        if match is not None:
            a, b, c = (int(g) for g in match.groups())
            day, month, year = (a, b, c) if order == "dmy" else (c, b, a)
            return _real_date(year, month, day)
    match = _SHORT_DATE.search(raw)
    if match is not None:
        day, month, short = (int(g) for g in match.groups())
        return _real_date(short + (2000 if short < _CENTURY_PIVOT else 1900), month, day)
    match = _WORD_DATE.search(raw)
    if match is not None and match.group(2).casefold() in _MONTHS:
        return _real_date(
            int(match.group(3)), _MONTHS[match.group(2).casefold()], int(match.group(1))
        )
    raise _Invalid("no date")


def _real_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        raise _Invalid("not a calendar date") from None


def _fraction(raw: str) -> dict[str, int]:
    """ "85/1000", "85/1.000stel", and a decimal share kept exact and unreduced:
    "47,25/1.000" -> 4725/100000 (both sides scaled by the same power of ten)."""
    match = _FRACTION.search(raw)
    if match is None:
        raise _Invalid("no fraction")
    numerator, denominator = (_share_number(g) for g in match.groups())
    places = max(-n.as_tuple().exponent for n in (numerator, denominator))
    scale = Decimal(10) ** max(places, 0)
    numerator, denominator = int(numerator * scale), int(denominator * scale)
    if numerator == 0 or denominator == 0 or numerator > denominator:
        raise _Invalid("a fraction is at most 1")
    return {"numerator": numerator, "denominator": denominator}


def _share_number(text: str) -> Decimal:
    """One side of a share: a comma is the decimal mark and dots group thousands (German);
    without a comma, dots grouping exactly three digits are thousands ("1.000"), else a
    single dot is the decimal mark ("47.25", as a model may write it)."""
    text = text.rstrip(".,")
    if "," in text:
        text = text.replace(".", "").replace(",", ".", 1)
    elif _THOUSANDS_ONLY.fullmatch(text):
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise _Invalid("not a number") from None


def _iban(raw: str) -> str:
    candidates = [raw] + [m.group() for m in _IBAN_IN_TEXT.finditer(raw)]
    for candidate in candidates:
        try:
            return normalize_iban(candidate)
        except RuleError:
            continue
    raise _Invalid("not a valid IBAN")


# --- The identifier scrub of the owners' own tax documents (phase-4b.md D96, D103, D129) ---

# The kinds whose lines and quotes are scrubbed before anything is stored: an owner's
# Lohnsteuerbescheinigung and payslip (D103) and the household's Einkommensteuerbescheid
# (D129). The model sees the whole page; a quote it cites can span a neighbouring field, and a
# value it writes can carry more than was asked.
SCRUBBED_KINDS = frozenset(
    {
        ExtractionKind.LOHNSTEUERBESCHEINIGUNG,
        ExtractionKind.GEHALTSABRECHNUNG,
        ExtractionKind.EINKOMMENSTEUERBESCHEID,
        # Phase 3 D143: a payment proof from a private account (scrub_payment_proof).
        ExtractionKind.PAYMENT_PROOF,
    }
)
SCRUBBED = "…"

# Each pattern stays within one line ([ \t], never \s), so a scrubbed text keeps its lines.
_H = r"[ \t]"
# A label, an optional colon, and what follows it on the line up to the next label-like break.
_LETTER = "A-Za-zÄÖÜäöüß"
# One token of an identifier's value: it holds a digit ("12", "14.03.1988", "MSTRMXMS88C14K",
# "DE89"), or is a single capital (the letter inside a Sozialversicherungsnummer) or a month.
_ID_TOKEN = (
    r"(?:(?:januar|februar|märz|april|mai|juni|juli|august|september|oktober|november"
    rf"|dezember)(?![{_LETTER}])|[A-Za-z0-9./-]*[0-9][A-Za-z0-9./-]*|[A-Z](?![{_LETTER}]))"
)
# An identifier's label (D103): Identifikationsnummer, eTIN, Sozialversicherungsnummer,
# Personalnummer and its short forms (MA-Nr., AN-Nr., Mitarb.-Nr.), Steuernummer, IBAN and the
# birth date (Geburtsdatum, Geb.-Dat., Gebdat, geboren am).
_ID_LABEL = (
    r"(?:steuerliche[ \t]+)?identifikations-?nummer|identifikations-?nr\.?"
    r"|id\.?-?nr\.?|steuer-?id(?:entifikationsnummer)?|e-?tin|transfer-?ticket"
    r"|sozialversicherungs-?(?:nummer|nr\.?)|sv-?(?:nummer|nr\.?)"
    r"|versicherungs-?(?:nummer|nr\.?)|rv-?(?:nummer|nr\.?)|personal-?(?:nummer|nr\.?)"
    r"|pers\.?-?nr\.?|p-?nr\.?|arbeitnehmer-?(?:nummer|nr\.?)|mitarbeiter-?(?:nummer|nr\.?)"
    r"|mitarb\.?-?nr\.?|ma-?nr\.?|an-?nr\.?"
    r"|steuer-?(?:nummer|nr\.?)|st\.?-?nr\.?|iban|geburtsdatum|geb\.?-?datum"
    r"|geburtsdat\.?|geb\.?-?dat\.?|gebdat"
    r"|geboren(?:[ \t]+am)?|geb\.(?:[ \t]*am)?"
)
# A label starts and ends at a word's edge; "Geb." may end in its dot.
_LABEL_START = rf"(?<![{_LETTER}])"
_LABEL_END = rf"(?<![{_LETTER}](?=[{_LETTER}]))(?![{_LETTER}])"
_LABELLED = re.compile(
    rf"{_LABEL_START}(?P<label>{_ID_LABEL}){_LABEL_END}"
    rf"(?P<sep>{_H}*:?{_H}*)"
    rf"(?P<value>{_ID_TOKEN}(?:{_H}{{1,3}}{_ID_TOKEN})*)",
    re.IGNORECASE,
)
# A line of labels only ("Geburtsdatum   Personalnummer"): the values stand on the next line,
# under their labels, as on a payslip's header.
_LABELS_ONLY = re.compile(
    rf"[ \t:/\r]*(?:{_LABEL_START}(?:{_ID_LABEL}){_LABEL_END}[ \t:/\r]*)+", re.IGNORECASE
)
_DIGIT_TOKEN = re.compile(r"\S*[0-9]\S*")
# A full date: 14.03.1988, 14. März 1988, 1988-03-14. One whose year lies at least
# _BIRTH_DATE_YEARS before the reference year is a birth date on these documents (the pay
# period, the tax year and the document's own date never lie that far back).
_BIRTH_DATE_YEARS = 14
_FULL_DATE = re.compile(
    rf"(?<![0-9])(?:[0-9]{{1,2}}\.{_H}?[0-9]{{1,2}}\.{_H}?(?P<dmy>[0-9]{{4}})"
    rf"|[0-9]{{1,2}}\.?{_H}+(?i:{'|'.join(_MONTHS)}){_H}+(?P<word>[0-9]{{4}})"
    r"|(?P<iso>[0-9]{4})-[0-9]{2}-[0-9]{2})(?![0-9])"
)
_PATTERNS = (
    # Identifikationsnummer, as printed on the form ("12 345 678 901") or run together.
    re.compile(rf"(?<![0-9])[0-9]{{2}}{_H}?[0-9]{{3}}{_H}?[0-9]{{3}}{_H}?[0-9]{{3}}(?![0-9])"),
    # Sozialversicherungsnummer: area, birth date, the surname's letter, serial and check digit.
    re.compile(
        rf"(?<![0-9A-Za-z])[0-9]{{2}}{_H}?[0-9]{{6}}{_H}?[A-Z]{_H}?[0-9]{{2}}{_H}?[0-9](?![0-9])"
    ),
    # eTIN: eight letters of the name, the birth year, a month letter, the day and a check letter.
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{8}[0-9]{2}[A-Z][0-9]{2}[A-Z](?![A-Za-z0-9])"),
    # An IBAN, grouped or not.
    re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[0-9]{2}(?:[ \t]?[A-Z0-9]){11,30}(?![A-Za-z0-9])"),
    # A Steuernummer as the Länder print it ("12/345/67890", "143/123/45678", D129), its
    # 13-digit ELSTER form ("2181508150123"), and any run of nine or more digits (an amount on
    # these documents always has separators). The 13-digit form is a run of nine or more as
    # well; it is named so that D129's two formats stay covered if the run rule changes.
    re.compile(r"(?<![0-9/])[0-9]{2,3}/[0-9]{3,4}/[0-9]{4,5}(?![0-9/])"),
    re.compile(r"(?<![0-9])[0-9]{13}(?![0-9])"),
    re.compile(r"(?<![0-9])[0-9]{9,}(?![0-9])"),
    # A street with its number ("Musterweg 9", "Industriestr. 5a") and a postal code with its
    # town ("12345 Musterstadt"): the employee's or the employer's address.
    re.compile(
        rf"(?<![{_LETTER}])(?:[A-ZÄÖÜ][{_LETTER}.-]*?)?(?i:straße|strasse|str\.|weg|gasse|platz"
        rf"|allee|ring|damm|ufer|chaussee|steig|pfad){_H}+[0-9]{{1,4}}(?:{_H}?[a-z])?"
        r"(?![0-9A-Za-z])",
    ),
    re.compile(
        rf"(?<![0-9.,])[0-9]{{5}}{_H}+(?!(?:EUR|Euro)\b)[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+"
        rf"(?:{_H}+(?:am|an[ \t]der|im|in[ \t]der|ob[ \t]der)?{_H}*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß-]+)?"
    ),
)


def scrub_identifiers(text: str, *, reference_year: int | None = None) -> str:
    """The text with every identifier of a pay document or an Einkommensteuerbescheid
    replaced by "…" (D103, D129): a value after an identifier's label
    (Identifikationsnummer, eTIN, Sozialversicherungsnummer, Personalnummer, Steuernummer,
    IBAN, Geburtsdatum, ...), every number on the line under a line of such labels only, a
    full date at least 14 years before reference_year (a birth date; default: this year), an
    Identifikationsnummer, a Sozialversicherungsnummer, an eTIN, an IBAN, a Steuernummer
    (with slashes or in its 13-digit form), any run of nine or more digits, a street with its
    house number and a postal code with its town. Amounts, the period's dates, the tax class
    and names stay. Pure and idempotent; lines are kept."""
    year = date.today().year if reference_year is None else reference_year
    text = _under_labels(text)
    text = _LABELLED.sub(lambda m: f"{m.group('label')}{m.group('sep')}{SCRUBBED}", text)
    text = _FULL_DATE.sub(lambda m: _birth_date(m, year), text)
    for pattern in _PATTERNS:
        text = pattern.sub(SCRUBBED, text)
    return text


# Phase 3 D143: on a payment proof, the payer's side of the transfer. The value after one of
# these labels (the account holder's or payer's name, an account number, a bank code, a
# mandate or creditor id, a customer number) goes, up to a tab, two spaces, the payee's label
# or the line's end, so a row "Auftraggeber: A  Empfänger: B" keeps the payee. A label alone on
# its line (an optional colon) takes the whole next non-blank line, the layout of a bank's
# Umsatzdetail. "Von:", "An:" and "für" are not labels: they are too common in a proof's text
# (a period "von 01.01. an", a purpose "für Rechnung ...") and would scrub what is kept.
_PAYER_LABEL = (
    r"auftraggeber(?:in)?|konto-?inhaber(?:in)?|inhaber(?:in)?|zahlungspflichtige[rn]?"
    r"|einzahler(?:in)?|absender(?:in)?|name[ \t]+des[ \t]+konto-?inhabers"
    r"|konto-?(?:nummer|nr\.?)|kto\.?-?nr\.?|konto|bic|swift|blz|bankleitzahl"
    r"|mandats-?referenz|gläubiger-?id(?:entifikationsnummer)?|kunden-?(?:nummer|nr\.?)"
    r"|kd\.?-?nr\.?"
)
_PAYER_LABEL_ONLY = re.compile(
    rf"{_H}*{_LABEL_START}(?:{_PAYER_LABEL}){_LABEL_END}{_H}*:?{_H}*", re.IGNORECASE
)
_PAYER_LABELLED = re.compile(
    rf"{_LABEL_START}(?P<label>{_PAYER_LABEL}){_LABEL_END}(?P<sep>{_H}*:?{_H}*)"
    # The value starts with neither a space, a colon nor a full stop, so a label alone on its
    # line ("Auftraggeber:", "Kd.-Nr.") is left to _under_payer_labels, not cut to "Kd.-Nr…".
    r"(?P<value>(?=[^\s:.])(?:(?![ \t]{2}|\t|empfänger|zahlungsempfänger|begünstigter)[^\n])+)",
    re.IGNORECASE,
)


def scrub_payment_proof(text: str, *, reference_year: int | None = None) -> str:
    """A payment proof's text scrubbed (phase 3 D143): every identifier scrub_identifiers
    removes (IBANs, long account numbers, addresses, a birth date), and the value after a
    payer-side label (_PAYER_LABEL). The payee, the amount, the dates and the purpose stay.
    Pure and idempotent; lines are kept."""
    text = _under_payer_labels(scrub_identifiers(text, reference_year=reference_year))
    return _PAYER_LABELLED.sub(lambda m: f"{m.group('label')}{m.group('sep')}{SCRUBBED}", text)


def _under_payer_labels(text: str) -> str:
    """The first non-blank line under a line holding only a payer-side label (and a colon),
    scrubbed whole; its indent is kept."""
    lines = text.split("\n")
    out = list(lines)
    for index, line in enumerate(lines):
        if not _PAYER_LABEL_ONLY.fullmatch(line):
            continue
        for below in range(index + 1, len(lines)):
            if lines[below].strip():
                value = lines[below]
                out[below] = value[: len(value) - len(value.lstrip())] + SCRUBBED
                break
    return "\n".join(out)


def scrubber(kind: ExtractionKind, *, reference_year: int | None) -> Callable[[str], str]:
    """The identifier scrub of a SCRUBBED_KINDS kind: a payment proof's own (D143), else
    scrub_identifiers (D103, D129)."""
    scrub = scrub_payment_proof if kind is ExtractionKind.PAYMENT_PROOF else scrub_identifiers
    return partial(scrub, reference_year=reference_year)


def _under_labels(text: str) -> str:
    """Every number on the first non-blank line under a line of identifier labels only."""
    lines = text.split("\n")
    out = list(lines)
    for index, line in enumerate(lines):
        if not _LABELS_ONLY.fullmatch(line):
            continue
        below = next((i for i in range(index + 1, len(lines)) if lines[i].strip()), None)
        if below is not None:
            out[below] = _DIGIT_TOKEN.sub(SCRUBBED, out[below])
    return "\n".join(out)


def _birth_date(match: re.Match[str], reference_year: int) -> str:
    year = int(match.group("dmy") or match.group("word") or match.group("iso"))
    return SCRUBBED if year <= reference_year - _BIRTH_DATE_YEARS else match.group(0)


# --- the general reading's scrub (D131, decided 10 Nov 2026) -----------------------------

# The general reading and a Verwaltervertrag are scrubbed too: a Vorauszahlungsbescheid
# prints the Steuernummer and the owners' home address, a tenant's Selbstauskunft the
# applicant's. The model cites whole passages, so every quote can carry them.
GENERAL_SCRUBBED_KINDS = frozenset({ExtractionKind.GENERAL, ExtractionKind.MANAGEMENT_CONTRACT})
# The fields a general reading links a property by, and the loan's number. Their lines are
# kept only when they hold a property's own address or a contract number that is no
# identifier; every other value goes (PR #135 review, F1 and F2). The lender is scrubbed like
# any other line: the bank's name stays, a customer number or an address beside it goes.
_ADDRESS_FIELDS = frozenset({"street", "house_number", "postal_code"})
_LINK_VALUE_FIELDS = _ADDRESS_FIELDS | {"contract_number"}
_ID_PATTERNS = _PATTERNS[:-2]  # everything but the two address patterns, in their order
_STREET, _POSTAL_TOWN = _PATTERNS[-2:]
# A contract number shaped like an IBAN or a Steuernummer (with slashes) is an identifier,
# not a loan's number: it counts as not read.
_IDENTIFIER_SHAPES = (_PATTERNS[3], _PATTERNS[4])
_KEEP_MARK = ""  # private-use characters: no pattern matches them


@dataclass(frozen=True)
class PropertyAddress:
    """A property's address as the workspace stores it: the only street, house number and
    postal code a general reading keeps. The job loads them before parsing; they are never
    taken from the model's lines."""

    street: str
    house_number: str
    postal_code: str


@dataclass(frozen=True)
class LinkValues:
    """What a general reading's link lines decide (link_values): whether it read an address,
    whether that address is one of the workspace's properties, and the contract number it
    read when that number is no identifier (it stays in every quote, so the loan can still be
    linked by it)."""

    address_read: frozenset[str] = frozenset()  # the address fields with a stated value
    address_matches: bool = False
    contract_number: str | None = None


NO_LINK = LinkValues()


def street_key(value: object) -> str:
    """Casefolded (ß as ss), spaces, hyphens and dots removed, and a street ending in the
    abbreviation "str" spelled out: "Lindenstr.", "Linden-Straße" and "Lindenstrasse" are
    one key."""
    key = re.sub(r"[\s.\-]", "", str(value).casefold())
    return f"{key}asse" if key.endswith("str") else key


def house_number_key(value: object) -> str:
    """Casefolded, spaces and trailing dots removed: "12 a" is "12a". A hyphen or slash
    stays significant, so "1-2" (or "1 - 2") is never "12" (review, 7 Nov 2026)."""
    return re.sub(r"\s", "", str(value).casefold()).rstrip(".")


def _postal_key(value: object) -> str:
    return "".join(str(value).split())


_ADDRESS_KEYS = {
    "street": street_key,
    "house_number": house_number_key,
    "postal_code": _postal_key,
}


def link_values(
    texts: list[str], asked: frozenset[str], properties: tuple[PropertyAddress, ...] = ()
) -> LinkValues:
    """The link lines of an answer (unscrubbed), of the fields the request asked for only.
    The address read matches when one property has every address field read (street and
    house number by key, the postal code as written); a contract number counts only when it
    is not shaped like an IBAN or a Steuernummer."""
    found: dict[str, str] = {}
    for text in texts:
        match = _KEY_LINE.fullmatch(text)
        if match is None or match.group(1) not in _LINK_VALUE_FIELDS & asked:
            continue
        raw = _without_page_reference(match.group(2).strip())
        if match.group(1) == "contract_number":
            raw = _without_number_label(raw)
        if raw and raw.casefold() != UNKNOWN:
            found[match.group(1)] = raw
    number = found.get("contract_number")
    if number is not None and _is_identifier(number):
        number = None
    read = {key: value for key, value in found.items() if key in _ADDRESS_FIELDS}
    return LinkValues(
        address_read=frozenset(read),
        address_matches=bool(read) and any(_fits(read, p) for p in properties),
        contract_number=number,
    )


def _is_identifier(number: str) -> bool:
    return any(shape.search(number.upper()) for shape in _IDENTIFIER_SHAPES)


def _fits(read: dict[str, str], prop: PropertyAddress) -> bool:
    stored = {"street": prop.street, "house_number": prop.house_number}
    stored["postal_code"] = prop.postal_code
    return all(
        _ADDRESS_KEYS[name](value) == _ADDRESS_KEYS[name](stored[name])
        for name, value in read.items()
    )


def scrub_general(
    text: str,
    properties: tuple[PropertyAddress, ...] = (),
    contract_number: str | None = None,
) -> str:
    """The text without identifiers or a person's address, for the general reading and a
    Verwaltervertrag (D131, decided 10 Nov 2026): the labelled identifiers and those of
    D103/D129 (Steuernummer in both forms, Identifikationsnummer, Sozialversicherungsnummer,
    eTIN, IBAN, a run of nine or more digits), a labelled birth date, and every street with
    its number and postal code with its town but a property's own (`properties`: the
    workspace's addresses, never what the model read). An unlabelled old date stays: a
    Teilungserklärung or a building's records may be decades old, and their date is the
    document's own. `contract_number`, a loan number link_values accepted, stays wherever it
    stands on its own, so a loan can still be linked by it. Pure and idempotent; lines are
    kept."""
    protected = _protected_contract_number(contract_number)
    if protected is not None:
        text = protected.sub(_KEEP_MARK, text)
    text = _under_labels(text)
    text = _LABELLED.sub(lambda m: f"{m.group('label')}{m.group('sep')}{SCRUBBED}", text)
    for pattern in _ID_PATTERNS:
        text = pattern.sub(SCRUBBED, text)
    text = _STREET.sub(
        lambda m: m.group(0) if _is_property_street(m, properties) else SCRUBBED, text
    )
    text = _POSTAL_TOWN.sub(
        lambda m: m.group(0) if _is_property_postal(m.group(0), properties) else SCRUBBED, text
    )
    if protected is not None and contract_number is not None:
        text = text.replace(_KEEP_MARK, contract_number.strip())
    return text


# A contract number shorter than this is not protected: a short one ("12") would hide the
# digits of an identifier from the patterns.
_PROTECTED_MIN_LENGTH = 4


def _protected_contract_number(number: str | None) -> re.Pattern[str] | None:
    """The read contract number as it stands in the text on its own, not inside a longer
    number or word."""
    if number is None or len(number.strip()) < _PROTECTED_MIN_LENGTH:
        return None
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(number.strip())}(?![A-Za-z0-9])")


def _is_property_street(match: re.Match[str], properties: tuple[PropertyAddress, ...]) -> bool:
    """The street match is a property's street and house number, compared by key, with the
    words before the match where the property's street has more of them ("Berliner Straße"
    stored, "Straße 7" matched)."""
    name, _, number = " ".join(match.group(0).split()).rpartition(" ")
    words = match.string[: match.start()].split()
    for prop in properties:
        if house_number_key(number) != house_number_key(prop.house_number):
            continue
        wanted = street_key(prop.street)
        if street_key(name) == wanted:
            return True
        extra = len(prop.street.split()) - 1
        for count in range(1, min(extra, len(words)) + 1):
            if street_key(" ".join([*words[-count:], name])) == wanted:
                return True
    return False


def _is_property_postal(found: str, properties: tuple[PropertyAddress, ...]) -> bool:
    return any(found.split()[0] == _postal_key(p.postal_code) for p in properties)


def scrub_general_answer(
    text: str, asked: frozenset[str], properties: tuple[PropertyAddress, ...] = ()
) -> str:
    """A general reading's raw answer as ai_call keeps it: every line scrubbed (scrub_general)
    but an asked address line when the address read is a property's, and an asked contract
    number line when the number is no identifier, which are kept as written. Any other
    address or contract number line, asked or volunteered, keeps its key only."""
    lines = text.split("\n")
    link = link_values(lines, asked, properties)
    scrubbed = scrub_general("\n".join(lines), properties, link.contract_number).split("\n")
    return "\n".join(
        _answer_line(line, clean, asked, link) for line, clean in zip(lines, scrubbed, strict=True)
    )


def _answer_line(line: str, clean: str, asked: frozenset[str], link: LinkValues) -> str:
    """An asked address line of a property's address, or an asked contract number that is no
    identifier, as written; any other address or contract number line with its value gone
    (an owner's home read as the property, a line the model volunteered, an IBAN read as the
    number); any other line scrubbed."""
    match = _KEY_LINE.fullmatch(line)
    if match is None or match.group(1) not in _LINK_VALUE_FIELDS:
        return clean
    key = match.group(1)
    kept = key in asked and (
        link.address_matches if key in _ADDRESS_FIELDS else link.contract_number is not None
    )
    return line if kept else f"{key}: {SCRUBBED}"


# --- Which field set a classified document gets -----------------------------------------

# The catalogue's invoices keep the invoice reader (amount, due date; D131).
_CATALOGUE_INVOICES = (
    DocumentSubtype.UTILITY_BILL,
    DocumentSubtype.HOUSEHOLD_SERVICES_INVOICE,
    DocumentSubtype.WORK_EXPENSE_INVOICE,
)
# The subtypes read by their type's reader: three that existed before the catalogue, and a
# financing bill (D138), an invoice or Bescheid whose cost lines are sorted like any other.
_READ_BY_TYPE = (
    DocumentSubtype.PRICE_SPLIT_EVIDENCE,
    DocumentSubtype.APPRAISAL,
    DocumentSubtype.COST_BASIS_EVIDENCE,
    DocumentSubtype.FINANCING_INVOICE,
)
_BY_SUBTYPE: dict[DocumentSubtype, ExtractionKind] = {
    DocumentSubtype.PURCHASE_CONTRACT: ExtractionKind.PURCHASE_CONTRACT,
    DocumentSubtype.LOAN_CONTRACT: ExtractionKind.LOAN_CONTRACT,
    DocumentSubtype.LEASE: ExtractionKind.LEASE,
    DocumentSubtype.HANDOVER_PROTOCOL: ExtractionKind.HANDOVER_PROTOCOL,
    DocumentSubtype.DEPOSIT_RECORD: ExtractionKind.DEPOSIT_RECORD,
    DocumentSubtype.GRUNDSTEUERBESCHEID: ExtractionKind.GRUNDSTEUERBESCHEID,
    DocumentSubtype.EINKOMMENSTEUERBESCHEID: ExtractionKind.EINKOMMENSTEUERBESCHEID,
    DocumentSubtype.MANAGEMENT_CONTRACT: ExtractionKind.MANAGEMENT_CONTRACT,  # D130
    DocumentSubtype.PAYMENT_PROOF: ExtractionKind.PAYMENT_PROOF,  # phase 3 D143
    **dict.fromkeys(_CATALOGUE_INVOICES, ExtractionKind.INVOICE),
}
# Every other subtype of the catalogue (§6.2.1) is read by the general reading, whatever its
# type: a depot_statement is never read as the rental account's statement, a
# prepayment_notice never as a Nebenkosten Bescheid (D131).
_BY_SUBTYPE |= {
    subtype: ExtractionKind.GENERAL
    for subtype in DocumentSubtype
    if subtype not in _BY_SUBTYPE and subtype not in _READ_BY_TYPE
}
_BY_TYPE: dict[DocumentType, ExtractionKind] = {
    DocumentType.INVOICE: ExtractionKind.INVOICE,
    DocumentType.BESCHEID: ExtractionKind.BESCHEID,
    DocumentType.WIRTSCHAFTSPLAN: ExtractionKind.WIRTSCHAFTSPLAN,
    DocumentType.HAUSGELDABRECHNUNG: ExtractionKind.HAUSGELDABRECHNUNG,
    DocumentType.ZINSBESCHEINIGUNG: ExtractionKind.ZINSBESCHEINIGUNG,
    DocumentType.STATEMENT: ExtractionKind.STATEMENT,
    DocumentType.LOHNSTEUERBESCHEINIGUNG: ExtractionKind.LOHNSTEUERBESCHEINIGUNG,
    DocumentType.GEHALTSABRECHNUNG: ExtractionKind.GEHALTSABRECHNUNG,
}

# The classifier's types that hold tenant data whatever the upload said (§5.5 step 3): the
# lease and its papers, and every subtype of the catalogue's tenancy group (D131).
TENANT_SUBTYPES = frozenset(
    {
        DocumentSubtype.LEASE,
        DocumentSubtype.HANDOVER_PROTOCOL,
        DocumentSubtype.DEPOSIT_RECORD,
        *TENANCY_GROUP,
    }
)


# The subtypes that existed before the catalogue (0020's list); the rest are the catalogue's.
_BEFORE_CATALOGUE = frozenset(
    {
        DocumentSubtype.PURCHASE_CONTRACT,
        DocumentSubtype.PRICE_SPLIT_EVIDENCE,
        DocumentSubtype.APPRAISAL,
        DocumentSubtype.LOAN_CONTRACT,
        DocumentSubtype.LEASE,
        DocumentSubtype.HANDOVER_PROTOCOL,
        DocumentSubtype.DEPOSIT_RECORD,
        DocumentSubtype.COST_BASIS_EVIDENCE,
        DocumentSubtype.GRUNDSTEUERBESCHEID,
        DocumentSubtype.EINKOMMENSTEUERBESCHEID,
    }
)
CATALOGUE_SUBTYPES = frozenset(set(DocumentSubtype) - _BEFORE_CATALOGUE)
# Subtypes only code sets, never the classifier (D138): left out of the classify schema
# (ai.requests.classify_schema), and dropped should a model answer one anyway.
# A payment proof (phase 3 D143) is typed by its upload from a bill's to-do, never classified.
CODE_ONLY_SUBTYPES = frozenset({DocumentSubtype.FINANCING_INVOICE, DocumentSubtype.PAYMENT_PROOF})
# The types with a reader of their own that no catalogue subtype overrides (D131, decided 10
# Nov 2026 after the first eval recording read an owner's Lohnsteuerbescheinigung as an
# Arbeitgeberbescheinigung, which subtype-first routing would have read for title and date
# only). A statement, Bescheid, invoice, letter, contract or `other` still yields to one.
DEDICATED_TYPES = frozenset(
    {
        DocumentType.LOHNSTEUERBESCHEINIGUNG,
        DocumentType.GEHALTSABRECHNUNG,
        DocumentType.ZINSBESCHEINIGUNG,
        DocumentType.WIRTSCHAFTSPLAN,
        DocumentType.HAUSGELDABRECHNUNG,
    }
)


def accepted_subtype(
    type_: DocumentType, subtype: DocumentSubtype | None
) -> DocumentSubtype | None:
    """The subtype a classification keeps: a catalogue subtype on a dedicated type is dropped,
    so the type's reader, the retention and the proposal's typing follow the type. A
    subtype of the tenancy group is kept: it is the tenant gate's signal, and a tenant's
    payslip read as the owners' would propose an owner's pay (D131, PR #128 review). The
    subtypes from before the catalogue are kept as they always were. A subtype only code sets
    (D138) is never taken from the model."""
    if subtype in CODE_ONLY_SUBTYPES:
        return None
    if subtype in CATALOGUE_SUBTYPES and type_ in DEDICATED_TYPES and subtype not in TENANCY_GROUP:
        return None
    return subtype


def extraction_kind(type_: DocumentType, subtype: DocumentSubtype | None) -> ExtractionKind:
    """The field set to extract for a classified document. The subtype wins over the type (a
    Grundsteuerbescheid is a bescheid with its own field set, a Depotauszug a statement read
    for title and date only). A type without a reader of its own (a contract the classifier
    could not place, a letter, `other`) gets the general reading: title and date (D130)."""
    if subtype is not None and subtype in _BY_SUBTYPE:
        return _BY_SUBTYPE[subtype]
    return _BY_TYPE.get(type_, ExtractionKind.GENERAL)


# --- A Verwaltervertrag's fee with MwSt. (Luis, 10 Nov 2026) -----------------------------

# "zzgl. MwSt.", "zuzüglich der gesetzlichen Umsatzsteuer", "zzgl. 19 % USt." and the like:
# the contract adds VAT to the stated fee.
_VAT_ADDED = re.compile(
    r"(?:zzgl\.?|zuzügl\.?|zuzüglich|plus|\+)\s*(?:der\s+)?(?:gesetzl(?:\.|ichen?)\s*)?"
    r"(?:[0-9]{1,2}(?:,[0-9]+)?\s*%\s*)?(?:MwSt|Mehrwertsteuer|USt|Umsatzsteuer)",
    re.IGNORECASE,
)
_VAT_RATE = re.compile(r"([0-9]{1,2}(?:,[0-9]+)?)\s*%")
# §12 Abs. 1 UStG: the standard rate, which a property manager's fee carries.
STANDARD_VAT_RATE = Decimal("19")
_CENT = Decimal("0.01")


def management_fee_gross(fee_net: ExtractedValue, monthly_fee: ExtractedValue) -> ExtractedValue:
    """Betrag for a Verwaltervertrag: the net fee the model read, plus MwSt. when the fee's
    wording (its text or either quote) adds it, at the rate the wording names, else 19 %.
    Without such wording the fee is taken as it stands. Computed by code (principle 1), so it
    carries `derived` and no source; missing when the net fee is."""
    try:  # a parsed money value is stored in plain form ("30.00")
        net = Decimal(str(fee_net.value)) if fee_net.value is not None else None
    except InvalidOperation:
        net = None
    if net is None or not net.is_finite():
        return ExtractedValue.missing()
    wording = " ".join(
        str(part)
        for part in (
            monthly_fee.value,
            monthly_fee.source.quote if monthly_fee.source else None,
            fee_net.source.quote if fee_net.source else None,
        )
        if part
    )
    gross = net
    if _VAT_ADDED.search(wording):
        rate = STANDARD_VAT_RATE
        named = _VAT_RATE.search(wording)
        if named is not None:
            candidate = Decimal(named.group(1).replace(",", "."))
            if 0 < candidate <= 25:
                rate = candidate
        gross = (net * (100 + rate) / 100).quantize(_CENT, rounding=ROUND_HALF_UP)
    levels = [v.confidence if v.value is not None else "low" for v in (fee_net, monthly_fee)]
    order = {"low": 0, "medium": 1, "high": 2}
    return ExtractedValue(
        value=str(gross.quantize(_CENT)),
        derived_from=["fee_net", "monthly_fee"],
        checks=["derived", "validates"],
        confidence=min(levels, key=order.__getitem__),
    )


# --- A bill's cost lines (phase 2 D135) ----------------------------------------------------

# An Umsatzsteuer line, besides the code vat: its text names the tax (casefolded, by word).
_VAT_LINE = re.compile(r"\b(?:umsatzsteuer|mehrwertsteuer|ust|mwst)\b")
# The sum of the lines may miss the printed total by a rounding cent.
COST_LINES_TOLERANCE = Decimal("0.01")


@dataclass(frozen=True)
class CostLine:
    """One cost line of a bill as read: its text, its amount (signed as printed, a credit
    negative), the model's category (None when its code is unknown, or for the VAT line), and
    whether it is the Umsatzsteuer line."""

    text: str
    amount: Decimal
    category: AllocationCategory | None
    vat: bool


def cost_lines(items: object) -> list[CostLine] | None:
    """The stored cost_lines value (a list of `text | amount | code` texts) as CostLines; None
    when it is not a list or any item does not have that shape or a readable amount. Pure."""
    if not isinstance(items, list) or not items:
        return None
    out = []
    for item in items:
        if not isinstance(item, str):
            return None
        parts = [_collapse(part) for part in item.split("|")]
        if len(parts) != 3 or not parts[0]:
            return None
        text, raw_amount, code = parts
        try:
            amount = Decimal(normalize_value(raw_amount, "money"))
        except (_Invalid, InvalidOperation):
            return None
        code = code.casefold()
        vat = code == "vat" or _VAT_LINE.search(text.casefold()) is not None
        category = None
        if not vat and code in COST_LINE_CODES:
            category = AllocationCategory(code)
        out.append(CostLine(text=text, amount=amount, category=category, vat=vat))
    return out


def cost_lines_balance(lines: list[CostLine], total: object) -> bool:
    """True when the lines sum to the bill's total (a stored money string) within a cent."""
    try:
        amount = Decimal(str(total)) if total is not None else None
    except InvalidOperation:
        return False
    if amount is None or not amount.is_finite():
        return False
    return abs(sum((line.amount for line in lines), Decimal(0)) - amount) <= COST_LINES_TOLERANCE


def checked_cost_lines(values: dict[str, ExtractedValue]) -> ExtractedValue | None:
    """cost_lines with `validates` only when every line parses and they sum to amount_total;
    otherwise the value stays for the review but loses `validates` (confidence low), so the
    pre-fill falls back to the whole bill. None when the reading has no cost_lines."""
    read = values.get("cost_lines")
    if read is None or read.value is None:
        return None
    lines = cost_lines(read.value)
    total = values.get("amount_total", ExtractedValue.missing()).value
    if lines is not None and cost_lines_balance(lines, total):
        return read
    checks = [c for c in read.checks if c != "validates"]
    return read.model_copy(update={"checks": checks, "confidence": "low"})


# --- A Wohnflächenberechnung's dwellings (phase 2 D139) -------------------------------------


# D139 guard: a Grundriss or a single flat's Wohnflächenberechnung lists rooms, and the
# household's units are rooms ("Zimmer 1"), so a room row would match a unit by its label.
# A row whose label starts with a room word is never a Wohnung. A total row is dropped only
# beside other rows: alone, it is the one flat's Wohnfläche.
_ROOM_WORDS = frozenset(
    {
        "zimmer",
        "schlafzimmer",
        "wohnzimmer",
        "kinderzimmer",
        "arbeitszimmer",
        "küche",
        "bad",
        "wc",
        "flur",
        "diele",
        "abstellraum",
        "balkon",
        "loggia",
        "terrasse",
        "keller",
    }
)
_TOTAL_WORDS = frozenset({"summe", "gesamt", "insgesamt", "total", "wohnfläche gesamt"})
_FIRST_WORD = re.compile(r"\w+")


def _first_word(label: str) -> str:
    match = _FIRST_WORD.match(label.casefold())
    return match.group(0) if match else ""


def _is_total(label: str) -> bool:
    return label.casefold().rstrip(" :") in _TOTAL_WORDS


@dataclass(frozen=True)
class Dwelling:
    """One dwelling of a Wohnflächenberechnung as read: its label as printed, its Wohnfläche
    (a decimal string with two places, as property.living_area_m2 stores it), its MEA
    ({numerator, denominator}) and its location when the row states them."""

    label: str
    area: str
    mea: dict[str, int] | None
    location: str | None


def dwellings(items: object) -> list[Dwelling]:
    """The stored dwellings value (a list of `label | Wohnfläche | MEA | Lage` texts) as
    Dwellings, in the document's order. A row without a label or a readable positive area
    (at most two decimal places) is left out; an unreadable MEA or an unstated part is None.
    A room row (its label's first word Zimmer, Küche, Bad, ...) is left out, and a total row
    (Summe, Gesamt, ...) too when other rows remain (D139). Not a list: []. Pure."""
    if not isinstance(items, list):
        return []
    out = [row for item in items if not _is_room(item) and (row := _dwelling(item)) is not None]
    flats = [row for row in out if not _is_total(row.label)]
    return flats if flats else out


def lone_dwelling(items: object) -> bool:
    """True when the list names exactly one Wohnung (D139 rule (a)): dwellings() keeps one row
    and every other item is a room or a total row the guard leaves out. An item left out as
    unreadable (no label, no readable area) counts against it, since it may be another flat.
    Pure."""
    if not isinstance(items, list) or len(dwellings(items)) != 1:
        return False
    rest = [item for item in items if not _is_room(item)]
    return all(_dwelling(item) is not None for item in rest)


def _is_room(item: object) -> bool:
    return isinstance(item, str) and _first_word(_collapse(item.split("|")[0])) in _ROOM_WORDS


def _dwelling(item: object) -> Dwelling | None:
    """One row of the list, or None when it has no label or no readable area."""
    if not isinstance(item, str):
        return None
    parts = [_collapse(part) for part in item.split("|")]
    parts += [""] * (4 - len(parts))
    label, raw_area, raw_mea, location = parts[:4]
    if not label or label.casefold() == UNKNOWN:
        return None
    try:
        area = Decimal(normalize_value(raw_area, "decimal"))
    except (_Invalid, InvalidOperation):
        return None
    if area <= 0 or area.as_tuple().exponent < -2:  # type: ignore[operator]
        return None
    mea = None
    if raw_mea and raw_mea.casefold() != UNKNOWN:
        try:
            mea = normalize_value(raw_mea, "fraction")
        except _Invalid:
            mea = None
    known = location and location.casefold() != UNKNOWN
    return Dwelling(
        label=label,
        area=str(area.quantize(Decimal("0.01"))),
        mea=mea,
        location=location if known else None,
    )


def derive_fields(
    kind: ExtractionKind, values: dict[str, ExtractedValue]
) -> dict[str, ExtractedValue]:
    """The fields code computes or checks in a merged reading: a Verwaltervertrag's Betrag,
    and an invoice's or Bescheid's cost lines against its total (D135). The job and the eval
    reading both call it after the chunk merge."""
    if kind is ExtractionKind.MANAGEMENT_CONTRACT:
        fee_net = values.get("fee_net", ExtractedValue.missing())
        monthly_fee = values.get("monthly_fee", ExtractedValue.missing())
        return {**values, "amount_total": management_fee_gross(fee_net, monthly_fee)}
    if kind in (ExtractionKind.INVOICE, ExtractionKind.BESCHEID):
        checked = checked_cost_lines(values)
        if checked is not None:
            return {**values, "cost_lines": checked}
    return values
