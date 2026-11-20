"""A depot document in plain words (docs/phases/phase-6.md D177).

The texts are rule data (`depot_explain.yaml`, principle 5), written by people; this module
only picks the entry for a document's kind and fills in the document's own figures, which
the caller formats from stored data with the helpers below (principle 1: the model reads,
code explains). Nothing here is sent to the model or written into an event.

Each entry's English (`en`, phase 10 D284) sits beside its German in the same file, sentence
for sentence with the same placeholders; `explain` and the formatters follow the request's
language (hausbuch.i18n), German outside a request.
"""

import re
import string
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from functools import cache
from importlib.resources import files
from typing import Self

import yaml
from pydantic import Field, model_validator

from hausbuch import i18n
from hausbuch.schemas.common import Schema
from hausbuch.schemas.document import DocumentExplanation
from hausbuch.schemas.enums import DepotDocumentKind

from . import depot

# The placeholders a sentence may use; the caller fills those it has.
PLACEHOLDERS = frozenset(
    {
        "konto",
        "datum",
        "stichtag",
        "fonds",
        "anteile",
        "betrag",
        "brutto",
        "netto",
        "gebuehren",
        "steuern",
        "positionen",
        "gesamtwert",
        "umsaetze",
    }
)
_FORMATTER = string.Formatter()


def _fields(sentence: str) -> set[str]:
    return {name for _text, name, _spec, _conv in _FORMATTER.parse(sentence) if name is not None}


class Wording(Schema):
    """An entry's three parts in English (phase 10 D284)."""

    was: str = Field(min_length=1)
    bedeutung: list[str] = Field(default_factory=list)
    aktion: str = Field(min_length=1)


class KindEntry(Schema):
    kind: DepotDocumentKind
    titles: list[str] = Field(min_length=1)
    was: str = Field(min_length=1)
    bedeutung: list[str] = Field(default_factory=list)
    aktion: str = Field(min_length=1)
    en: Wording

    @model_validator(mode="after")
    def _valid(self) -> Self:
        for pattern in self.titles:
            re.compile(pattern)
        for sentence in self.bedeutung:
            unknown = _fields(sentence) - PLACEHOLDERS
            if unknown or "" in _fields(sentence):
                raise ValueError(f"{self.kind}: unknown placeholder in {sentence!r}")
        # The English says the same sentence for sentence, so a missing figure leaves out the
        # same sentence in either language.
        if len(self.en.bedeutung) != len(self.bedeutung) or any(
            _fields(de) != _fields(en)
            for de, en in zip(self.bedeutung, self.en.bedeutung, strict=True)
        ):
            raise ValueError(f"{self.kind}: the English sentences differ from the German ones")
        return self

    def wording(self) -> Wording:
        """The three parts in the request's language."""
        if i18n.english():
            return self.en
        return Wording(was=self.was, bedeutung=self.bedeutung, aktion=self.aktion)


class Catalogue(Schema):
    kinds: list[KindEntry]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        kinds = [entry.kind for entry in self.kinds]
        if len(kinds) != len(set(kinds)):
            raise ValueError("depot_explain.yaml: each kind once")
        return self

    def entry(self, kind: DepotDocumentKind) -> KindEntry | None:
        return next((e for e in self.kinds if e.kind is kind), None)


@cache
def catalogue() -> Catalogue:
    """The validated catalogue. Cached: the file is immutable at runtime."""
    text = (files("hausbuch.rules") / "depot_explain.yaml").read_text(encoding="utf-8")
    return Catalogue.model_validate(yaml.safe_load(text))


@cache
def _patterns() -> tuple[tuple[re.Pattern[str], DepotDocumentKind], ...]:
    return tuple(
        (re.compile(pattern), entry.kind) for entry in catalogue().kinds for pattern in entry.titles
    )


def document_kind(text: str) -> DepotDocumentKind | None:
    """The kind of a depot document by its text layer's heading: the first entry whose
    pattern matches, in the catalogue's order. None for a document that does not name the
    depot (rules.depot.is_depot_document) or whose heading no entry knows."""
    if not text or not depot.is_depot_document(text):
        return None
    return next((kind for pattern, kind in _patterns() if pattern.search(text)), None)


def with_side(kind: DepotDocumentKind, side: str | None) -> DepotDocumentKind:
    """A trade note whose heading names no side takes the side of its reading."""
    if kind is DepotDocumentKind.WERTPAPIERABRECHNUNG:
        if side == "Kauf":
            return DepotDocumentKind.WERTPAPIERABRECHNUNG_KAUF
        if side == "Verkauf":
            return DepotDocumentKind.WERTPAPIERABRECHNUNG_VERKAUF
    return kind


def explain(kind: DepotDocumentKind, facts: Mapping[str, str | None]) -> DocumentExplanation | None:
    """The kind's three parts, its sentences filled from `facts` (already formatted). A
    sentence one of whose placeholders has no value is left out, never guessed; None when the
    catalogue has no entry for the kind."""
    entry = catalogue().entry(kind)
    if entry is None:
        return None
    wording = entry.wording()
    known = {name: value for name, value in facts.items() if value}
    sentences = [
        sentence.format_map(known)
        for sentence in wording.bedeutung
        if _fields(sentence) <= set(known)
    ]
    return DocumentExplanation(
        was=wording.was, bedeutung=" ".join(sentences) or None, aktion=wording.aktion
    )


# --- Formatting, in the request's language ------------------------------------------------


def money(amount: Decimal) -> str:
    """Decimal("-1234.5") → "1.234,50 €" (en "€1,234.50"): the sentence says which way it
    went."""
    return i18n.money(abs(amount))


def day(value: date) -> str:
    """ "14.05.2026" (en "14 May 2026")."""
    return i18n.day(value)


def units(value: Decimal) -> str:
    """Decimal("4.8000") → "4,8"; Decimal("1234.5") → "1.234,5" (en "1,234.5"): every
    decimal it has, no trailing zeros."""
    return i18n.number(abs(value).normalize())


def count(value: int) -> str:
    return str(value)
