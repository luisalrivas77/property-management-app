"""Field help for „Eure Angaben“ (phase 10 §7.8 (b), D294): rules/tax/field_help/<year>.yaml.

Every field of every form the guided tab shows has help in both languages, and nothing else
does; a year without its own file reuses the latest earlier one, marked reused_from.
"""

import re

import pytest

from hausbuch.rules.errors import TAX_NO_RULE_SET, RuleError
from hausbuch.rules.tax import registry
from hausbuch.schemas.employment import RETURN_INPUT_VALUES
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.field_help import FieldHelp, FieldHelpForm
from hausbuch.schemas.performance import ExpectedGrossPay

# Fields a person never types: the wage certificate's `unread` lists what a reading could not
# fill and is set by code (phase 4b D96).
NOT_TYPED = {"wage_certificate": {"unread"}}
# The owner fields the tab asks (the rest of the owner form is not tax data).
OWNER_FIELDS = {"church_tax", "tax_state"}


def _expected() -> dict[str, set[str]]:
    forms = {
        kind.value: set(model.model_fields) - NOT_TYPED.get(kind.value, set())
        for kind, model in RETURN_INPUT_VALUES.items()
        if kind is not ReturnInputKind.NO_PAY  # a button, no field (D195)
    }
    forms["expected_gross_pay"] = set(ExpectedGrossPay.model_fields)
    forms["owner"] = OWNER_FIELDS
    return forms


def test_every_field_of_every_form_has_help_and_nothing_else():
    help_ = registry.field_help(2026)
    expected = _expected()
    assert set(help_.forms) == set(expected)
    assert set(FieldHelpForm.__args__) == set(expected)
    for form, fields in expected.items():
        assert set(help_.forms[form]) == fields, form


def test_every_entry_has_both_languages_in_full():
    help_ = registry.field_help(2026)
    for form, fields in help_.forms.items():
        for field, entry in fields.items():
            for lang in (entry.de, entry.en):
                assert lang.what.strip() and lang.where.strip() and lang.example.strip(), (
                    form,
                    field,
                )
            assert entry.de.what != entry.en.what, (form, field)


def test_the_payslip_names_no_line_number_and_its_source_is_not_official():
    """A payslip has no official sample, so its help names labels only (D294)."""
    help_ = registry.field_help(2026)
    assert help_.sources["payslip"].official is False
    for entry in help_.forms["payslip"].values():
        assert entry.source == "payslip"
        assert "Nr." not in entry.de.where


def _models() -> dict[str, type]:
    from hausbuch.schemas.property import OwnerCreate

    models: dict[str, type] = {
        kind.value: model
        for kind, model in RETURN_INPUT_VALUES.items()
        if kind is not ReturnInputKind.NO_PAY
    }
    models["expected_gross_pay"] = ExpectedGrossPay
    models["owner"] = OwnerCreate
    return models


def test_no_required_field_is_rare():
    """A rare field goes behind „Weitere Angaben“ (D294 item 4); a required one there would
    leave a form that cannot be saved without opening it."""
    models = _models()
    for form, fields in registry.field_help(2026).forms.items():
        for field, entry in fields.items():
            if entry.rare:
                assert not models[form].model_fields[field].is_required(), (form, field)


def test_only_the_wage_certificate_sample_is_cited_by_line_number():
    """Line numbers are named only where an official sample was read (the BMF's
    Lohnsteuerbescheinigung 2026); every other form is cited by its section (D294)."""
    for form, fields in registry.field_help(2026).forms.items():
        for field, entry in fields.items():
            if re.search(r"(Nr\.|Zeile) ?\d", entry.de.where):
                assert entry.source == "lstb_2026", (form, field)


def test_a_later_year_reuses_the_latest_file():
    later = registry.field_help(2027)
    assert (later.tax_year, later.reused_from) == (2027, 2026)
    assert later.forms == registry.field_help(2026).forms


def test_a_year_before_every_file_has_no_help():
    with pytest.raises(RuleError) as caught:
        registry.field_help(2025)
    assert caught.value.code == TAX_NO_RULE_SET


def test_an_entry_naming_an_unknown_source_is_refused():
    text = {"what": "x", "where": "y", "example": "z"}
    with pytest.raises(ValueError, match="unknown source"):
        FieldHelp.model_validate(
            {
                "tax_year": 2026,
                "sources": {"a": {"official": True, "de": "A", "en": "A"}},
                "forms": {
                    "telecom": {"months": {"rare": True, "source": "b", "de": text, "en": text}}
                },
            }
        )


def test_only_a_checked_sample_is_marked_official():
    """A source is official only when its lines were read from it: the BMF's
    Lohnsteuerbescheinigung sample; the Anleitung sections were not checked (26 Nov 2026)."""
    sources = registry.field_help(2026).sources
    assert {code for code, s in sources.items() if s.official} == {"lstb_2026"}
