"""The language of the server's own texts (phase 10 D284, step 4c).

German is the source and the default. A request may ask for English with `Accept-Language`
(the frontend sends `de` or `en` on every call, `frontend/src/api/client.ts`);
`api.logging.RequestMiddleware` sets the request's language here for the whole request, so a
service reads it with `current()` instead of taking it as a parameter. Anything that runs
without a request (the job worker, the CLI, the daily tasks) reads German, the default.

Two ways a module gives its German texts an English twin, never a scattered `if english`:

- `Texts`: the module's German texts by key, each with its English beside it, in one dict
  next to where they are used. A module whose texts it writes itself uses this.
- `Patterns`: German texts written where no English may sit beside them (`rules/health/` and
  `rules/plan/` feed `services.health.rule_version()`, so a text added there would change the
  stored rule version): each German template is matched against the finished German text and
  the English template is filled with the parts it captured, converted to English formats
  (`{d:date}` „15.03.2026“ → „15 Mar 2026“). A text no pattern matches stays German.

What stays German whatever the request asks (D284): papers for third parties (tenant letters,
the ELSTER field sheets, the yearly archive and `zahlungsnachweise.pdf`), the reminder email,
event payloads, logs, stored texts (a model's reasoning, a document's text) and anything a
job writes. Texts are chosen when a response is built, never when a row is written.

This module imports nothing from hausbuch, so any module may use it.
"""

import re
import string
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Literal

Locale = Literal["de", "en"]
DE: Final = "de"
EN: Final = "en"
LOCALES: Final = (DE, EN)

_locale: ContextVar[Locale] = ContextVar("hausbuch_locale", default=DE)


def current() -> Locale:
    """The language of the request being served; German outside a request."""
    return _locale.get()


def english() -> bool:
    return _locale.get() == EN


@contextmanager
def use(locale: Locale) -> Iterator[None]:
    """Serve the block in `locale`, restoring the previous language afterwards."""
    token = _locale.set(locale)
    try:
        yield
    finally:
        _locale.reset(token)


_RANGE = re.compile(r"^([A-Za-z]{1,8}|\*)(?:-[A-Za-z0-9]{1,8})*$")


def from_accept_language(header: str | None) -> Locale:
    """The language an `Accept-Language` header prefers among German and English: English
    only when it ranks English above German (a higher q, or first at the same q); German for
    no header, an unreadable one, or one naming neither."""
    if not header:
        return DE
    best: dict[str, tuple[float, int]] = {}
    for position, part in enumerate(header.split(",")):
        pieces = [p.strip() for p in part.split(";")]
        tag = pieces[0]
        if not _RANGE.match(tag):
            continue
        quality = 1.0
        for param in pieces[1:]:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        primary = tag.split("-")[0].lower()
        if primary not in LOCALES or quality <= 0:
            continue
        rank = (quality, -position)
        if primary not in best or rank > best[primary]:
            best[primary] = rank
    if EN in best and (DE not in best or best[EN] > best[DE]):
        return EN
    return DE


# --- Formats --------------------------------------------------------------------------------

_MONTHS_DE = (
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
_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_CENT = Decimal("0.01")


def _group(digits: str, sep: str) -> str:
    out = []
    while len(digits) > 3:
        out.append(digits[-3:])
        digits = digits[:-3]
    out.append(digits)
    return sep.join(reversed(out))


def number(value: Decimal, places: int | None = None, locale: Locale | None = None) -> str:
    """A decimal with grouping: de „1.234,5“, en „1,234.5“; `places` rounds half-up."""
    loc = locale or current()
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    text = format(abs(value), "f")
    whole, _, fraction = text.partition(".")
    group, point = (".", ",") if loc == DE else (",", ".")
    return f"{sign}{_group(whole, group)}{point + fraction if fraction else ''}"


def money(value: Decimal, locale: Locale | None = None) -> str:
    """Euros with cents: de „1.234,56 €“, en „€1,234.56“ (the frontend's formats)."""
    loc = locale or current()
    text = number(value.quantize(_CENT, rounding=ROUND_HALF_UP), locale=loc)
    if loc == DE:
        return f"{text} €"
    return f"-€{text[1:]}" if text.startswith("-") else f"€{text}"


def day(value: date, locale: Locale | None = None) -> str:
    """de „15.03.2026“, en „15 Mar 2026“."""
    loc = locale or current()
    if loc == DE:
        return value.strftime("%d.%m.%Y")
    return f"{value.day} {_MONTHS_EN[value.month - 1][:3]} {value.year}"


def month(year: int, month_number: int, locale: Locale | None = None) -> str:
    """de „März 2026“, en „March 2026“."""
    names = _MONTHS_DE if (locale or current()) == DE else _MONTHS_EN
    return f"{names[month_number - 1]} {year}"


# German formats read back, for Patterns' typed placeholders.
_DE_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_DE_NUMBER = re.compile(r"^(-?)(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d+))?$")
_DE_MONTH = re.compile(r"^(" + "|".join(_MONTHS_DE) + r") (\d{4})$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def _de_decimal(text: str) -> Decimal | None:
    match = _DE_NUMBER.match(text)
    if match is None:
        return None
    sign, whole, fraction = match.groups()
    return Decimal(f"{sign}{whole.replace('.', '')}{'.' + fraction if fraction else ''}")


def _en_number_like(text: str) -> str:
    """A German number („1.234,5“) in English („1,234.5“), keeping its decimals."""
    value = _de_decimal(text)
    if value is None:
        return text
    places = len(text.partition(",")[2]) if "," in text else 0
    return number(value, places, EN)


def _convert_date(text: str) -> str:
    match = _DE_DATE.match(text)
    if match is None:
        return text
    d, m, y = (int(g) for g in match.groups())
    try:
        return day(date(y, m, d), EN)
    except ValueError:
        return text


def _convert_money(text: str) -> str:
    """„1.234,56 €“ or „9.990 €“ → „€1,234.56“, „€9,990“ (decimals kept as written)."""
    if not text.endswith(" €"):
        return text
    body = text[:-2]
    converted = _en_number_like(body)
    if converted == body and _de_decimal(body) is None:
        return text
    return f"-€{converted[1:]}" if converted.startswith("-") else f"€{converted}"


def _convert_percent(text: str) -> str:
    """„3,65 %“ → „3.65%“."""
    if not text.endswith(" %"):
        return text
    return f"{_en_number_like(text[:-2])}%"


def _convert_month(text: str) -> str:
    """„März 2026“ or „2026-03“ → „March 2026“."""
    match = _DE_MONTH.match(text)
    if match is not None:
        return month(int(match.group(2)), _MONTHS_DE.index(match.group(1)) + 1, EN)
    match = _ISO_MONTH.match(text)
    if match is not None and 1 <= int(match.group(2)) <= 12:
        return month(int(match.group(1)), int(match.group(2)), EN)
    return text


CONVERTERS: Mapping[str, Callable[[str], str]] = {
    "": lambda text: text,
    "date": _convert_date,
    "money": _convert_money,
    "number": _en_number_like,
    "percent": _convert_percent,
    "month": _convert_month,
    "count": lambda text: text,  # digits only, so a nested text before it cannot swallow it
}

# What a placeholder may match: any text by default, a plain count only digits.
_CAPTURE = {"count": r"\d+"}


# --- Catalogues -----------------------------------------------------------------------------

_FORMATTER = string.Formatter()
CATALOGUES: list["Texts | Patterns"] = []  # every catalogue, for the completeness test


def fields(template: str) -> set[str]:
    """The placeholder names of a format template („{n} Zahlungen“ → {"n"})."""
    return {
        name.split(".")[0].split("[")[0]
        for _, name, _, _ in _FORMATTER.parse(template)
        if name is not None and name != ""
    }


class Texts:
    """One module's German texts by key, each with its English beside it:
    `{"key": ("Deutsch {x}", "English {x}")}`. Calling it with a key formats the text of the
    request's language; both templates must name the same placeholders."""

    def __init__(self, name: str, entries: Mapping[str, tuple[str, str]]) -> None:
        self.name = name
        self.entries = dict(entries)
        for key, (german, english_text) in self.entries.items():
            if not german or not english_text:
                raise ValueError(f"{name}.{key}: German and English are both required")
            if fields(german) != fields(english_text):
                raise ValueError(f"{name}.{key}: the placeholders differ between de and en")
        CATALOGUES.append(self)

    def __call__(self, key: str, /, **values: object) -> str:
        german, english_text = self.entries[key]
        return (english_text if english() else german).format(**values)

    def german(self, key: str, /, **values: object) -> str:
        """The German text whatever the request asks (a paper, an event, a stored row)."""
        return self.entries[key][0].format(**values)


_PLACEHOLDER = re.compile(r"\{(\w+)(?::(\w*))?\}")


class Patterns:
    """English for German texts written where English may not sit beside them (the module
    docstring). Each entry is a German template and its English one; a German placeholder may
    name a converter (`{d:date}`, `{a:money}`, `{n:number}`, `{p:percent}`, `{m:month}`) that
    turns the captured German format into the English one, and the English template names
    the placeholder bare (`{d}`). `translate` returns the text unchanged in German, and also
    when no entry matches, so a new German text shows in German until its entry is added (the
    tests run every producer against its catalogue). `converters` adds a catalogue's own
    kinds, such as one that translates a text embedded in another with a second catalogue."""

    def __init__(
        self,
        name: str,
        entries: Sequence[tuple[str, str]],
        converters: Mapping[str, Callable[[str], str]] | None = None,
    ) -> None:
        self.name = name
        self.converters: dict[str, Callable[[str], str]] = {**CONVERTERS, **(converters or {})}
        self.entries: list[tuple[re.Pattern[str], dict[str, str], str]] = []
        for german, english_text in entries:
            pattern, converters = self._compile(german)
            if set(converters) != fields(english_text):
                raise ValueError(f"{name}: the placeholders differ: {german!r}")
            self.entries.append((pattern, converters, english_text))
        CATALOGUES.append(self)

    def _compile(self, german: str) -> tuple[re.Pattern[str], dict[str, str]]:
        converters: dict[str, str] = {}
        parts: list[str] = []
        last = 0
        for match in _PLACEHOLDER.finditer(german):
            parts.append(re.escape(german[last : match.start()]))
            name, kind = match.group(1), match.group(2) or ""
            if kind not in self.converters:
                raise ValueError(f"unknown converter {kind!r} in {german!r}")
            if name in converters:
                parts.append(f"(?P={name})")
            else:
                converters[name] = kind
                parts.append(f"(?P<{name}>{_CAPTURE.get(kind, '.+?')})")
            last = match.end()
        parts.append(re.escape(german[last:]))
        return re.compile("".join(parts), re.DOTALL), converters

    def find(self, text: str) -> str | None:
        """The English for a German text, None when no entry matches."""
        for pattern, converters, english_text in self.entries:
            match = pattern.fullmatch(text)
            if match is None:
                continue
            values = {
                name: self.converters[kind](match.group(name)) for name, kind in converters.items()
            }
            return english_text.format(**values)
        return None

    def translate(self, text: str) -> str:
        if not english():
            return text
        found = self.find(text)
        return text if found is None else found
