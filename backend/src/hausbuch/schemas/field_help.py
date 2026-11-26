"""Field help for „Eure Angaben“ (phase 10 §7.8 (b), D294): per form and field, a plain
sentence of what it is, where to find it and an invented example, in German and English.

Rule data per tax year (rules/tax/field_help/<year>.yaml), read by registry.field_help and
served read-only by GET /tax/{tax_year}/field-help. It holds no figure any rule computes with.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from .common import Schema, TaxYear

# The forms that have help: every return_input kind with a field (no_pay is a button with an
# empty value, D195), the income input expected_gross_pay and the owner's church tax and
# Bundesland (`owner`). tests/rules/test_field_help.py checks each against its value model.
FieldHelpForm = Literal[
    "wage_certificate",
    "payslip",
    "commute",
    "home_office",
    "work_room",
    "work_equipment",
    "telecom",
    "professional_association",
    "training",
    "other_expense",
    "other_insurance",
    "replacement_income",
    "vorsorge_override",
    "child",
    "childcare_cost",
    "household_services",
    "special_expense",
    "expected_gross_pay",
    "owner",
]

_Text = Field(min_length=1, max_length=400)


class FieldHelpText(Schema):
    """One language's help for one field. `example` uses invented numbers only."""

    what: str = _Text
    where: str = _Text
    example: str = _Text


class FieldHelpEntry(Schema):
    """One field's help. rare: the screen shows the field behind „Weitere Angaben“ unless it
    is filled. source: a key of FieldHelp.sources, the document `where` points into."""

    rare: bool
    source: str = Field(min_length=1)
    de: FieldHelpText
    en: FieldHelpText


class FieldHelpSource(Schema):
    """A document the help points into. official: an official sample or instruction (a line
    number in `where` was read from it); false for a paper with no official sample (a
    payslip), whose labels differ by issuer."""

    official: bool
    url: str | None = Field(default=None, min_length=1)
    de: str = Field(min_length=1)
    en: str = Field(min_length=1)


class FieldHelp(Schema):
    """The year's field help. reused_from: set by registry.field_help when the year has no
    file of its own; the help is then the latest earlier year's."""

    tax_year: TaxYear
    reused_from: TaxYear | None = None
    sources: dict[str, FieldHelpSource] = Field(min_length=1)
    forms: dict[FieldHelpForm, dict[str, FieldHelpEntry]] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.reused_from is not None and self.reused_from >= self.tax_year:
            raise ValueError("reused_from is an earlier year")
        unknown = sorted(
            f"{form}.{field}"
            for form, fields in self.forms.items()
            for field, entry in fields.items()
            if entry.source not in self.sources
        )
        if unknown:
            raise ValueError(f"unknown source for {', '.join(unknown)}")
        return self
