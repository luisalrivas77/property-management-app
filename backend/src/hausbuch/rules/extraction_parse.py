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
from datetime import date
from decimal import Decimal, InvalidOperation

from hausbuch.schemas.ai import CitedTextBlock, ExtractedValue, SourceRef
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import EXTRACTION_MODELS, ValueKind, value_kind

from .errors import RuleError
from .iban import normalize_iban

UNKNOWN = "unbekannt"

_KEY_LINE = re.compile(r"([a-z0-9_]+):[ \t]*(.*)")
_NUMBER = re.compile(r"[+-]?[0-9][0-9.,]*[0-9]|[+-]?[0-9]")
_THOUSANDS_ONLY = re.compile(r"[+-]?[0-9]{1,3}(\.[0-9]{3})+")
_NUMERIC_DATE = re.compile(r"([0-9]{1,2})\.\s?([0-9]{1,2})\.\s?([0-9]{4})")
_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_WORD_DATE = re.compile(r"([0-9]{1,2})\.?\s+([A-Za-zÄÖÜäöü]+)\s+([0-9]{4})")
_YEAR = re.compile(r"(?<![0-9])[0-9]{4}(?![0-9])")
_FRACTION = re.compile(r"([0-9][0-9.,]*)\s*/\s*([0-9][0-9.,]*)")
_IBAN_IN_TEXT = re.compile(r"[A-Za-z]{2}[0-9]{2}(?:\s?[A-Za-z0-9]){10,32}")
_DIGITS = re.compile(r"[0-9]")

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
    blocks: list[CitedTextBlock], kind: ExtractionKind, *, page_offset: int = 0
) -> dict[str, ExtractedValue]:
    """The fields an extract response stated, keyed by field name.

    Only keys of EXTRACTION_MODELS[kind] count; a repeated key: the last line wins. A field
    the response did not mention is not in the result (the caller fills it with
    ExtractedValue.missing()). page_offset is the first page of the request's chunk minus 1,
    for a PDF split into several requests.
    """
    model = EXTRACTION_MODELS[kind]
    result: dict[str, ExtractedValue] = {}
    for text, contributors in _lines(blocks):
        match = _KEY_LINE.fullmatch(text)
        if match is None or match.group(1) not in model.model_fields:
            continue
        key, raw = match.group(1), match.group(2).strip()
        if not raw or raw.casefold() == UNKNOWN:
            result[key] = ExtractedValue.missing()
            continue
        source = _source(blocks, contributors, page_offset)
        result[key] = _extracted(raw, value_kind(model, key), source)
    return result


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
    dots group thousands; without a comma, dots in groups of three are thousands."""
    match = _NUMBER.search(raw.replace(" ", " "))
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
    return number


def _plain(number: Decimal) -> str:
    return format(number, "f")


def _date(raw: str) -> date:
    for pattern, order in ((_NUMERIC_DATE, "dmy"), (_ISO_DATE, "ymd")):
        match = pattern.search(raw)
        if match is not None:
            a, b, c = (int(g) for g in match.groups())
            day, month, year = (a, b, c) if order == "dmy" else (c, b, a)
            return _real_date(year, month, day)
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
        return Decimal(text).normalize()
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


# --- Which field set a classified document gets -----------------------------------------

_BY_SUBTYPE: dict[DocumentSubtype, ExtractionKind] = {
    DocumentSubtype.PURCHASE_CONTRACT: ExtractionKind.PURCHASE_CONTRACT,
    DocumentSubtype.LOAN_CONTRACT: ExtractionKind.LOAN_CONTRACT,
    DocumentSubtype.LEASE: ExtractionKind.LEASE,
    DocumentSubtype.HANDOVER_PROTOCOL: ExtractionKind.HANDOVER_PROTOCOL,
    DocumentSubtype.DEPOSIT_RECORD: ExtractionKind.DEPOSIT_RECORD,
    DocumentSubtype.GRUNDSTEUERBESCHEID: ExtractionKind.GRUNDSTEUERBESCHEID,
}
_BY_TYPE: dict[DocumentType, ExtractionKind] = {
    DocumentType.INVOICE: ExtractionKind.INVOICE,
    DocumentType.BESCHEID: ExtractionKind.BESCHEID,
    DocumentType.WIRTSCHAFTSPLAN: ExtractionKind.WIRTSCHAFTSPLAN,
    DocumentType.HAUSGELDABRECHNUNG: ExtractionKind.HAUSGELDABRECHNUNG,
    DocumentType.ZINSBESCHEINIGUNG: ExtractionKind.ZINSBESCHEINIGUNG,
    DocumentType.STATEMENT: ExtractionKind.STATEMENT,
}

# The classifier's types that hold tenant data whatever the upload said (§5.5 step 3).
TENANT_SUBTYPES = frozenset(
    {DocumentSubtype.LEASE, DocumentSubtype.HANDOVER_PROTOCOL, DocumentSubtype.DEPOSIT_RECORD}
)


def extraction_kind(type_: DocumentType, subtype: DocumentSubtype | None) -> ExtractionKind | None:
    """The field set to extract for a classified document; None means typing only (a letter,
    an appraisal, `other`). The subtype wins over the type (a Grundsteuerbescheid is a
    bescheid with its own field set)."""
    if subtype is not None and subtype in _BY_SUBTYPE:
        return _BY_SUBTYPE[subtype]
    if type_ == DocumentType.CONTRACT:
        return None  # a contract the classifier could not place: typing only
    return _BY_TYPE.get(type_)
