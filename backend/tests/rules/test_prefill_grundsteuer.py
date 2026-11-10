"""rules.prefill.grundsteuer_schedule_record and grundsteuer_schedule_value (phase 5 D126):
the Bescheid's schedule as a pre-fill record. The context is phase 2's fixture case
`grundsteuer_typing_and_link` (synthetic); its own records stay as that fixture pins them."""

import pytest
from fixture_models_phase2 import PrefillFixture, load

from hausbuch.rules.prefill import (
    GRUNDSTEUER_SCHEDULE_KEY,
    build_proposal,
    grundsteuer_schedule_record,
    grundsteuer_schedule_value,
)
from hausbuch.schemas.ai import ExtractedValue, SourceRef

FX = load("prefill")
assert isinstance(FX, PrefillFixture)
CASE = next(c for c in FX.cases if c.id == "grundsteuer_typing_and_link")


def _with(**values):
    return CASE.extraction_model().model_copy(update=values)


def _cited(value, quote="…"):
    return ExtractedValue(
        value=value, source=SourceRef(page=2, quote=quote), checks=["cited"], confidence="high"
    )


def _values(record):
    return {f: v.value for f, v in record.fields.items()}


@pytest.mark.rule
def test_the_fixture_bescheid_gives_four_quarters_with_their_quotes():
    record = grundsteuer_schedule_record(CASE.extraction_model(), CASE.context)
    assert record is not None
    assert (record.key, record.entity_type, record.mode) == (
        GRUNDSTEUER_SCHEDULE_KEY,
        "tax_input",
        "create",
    )
    values = _values(record)
    assert values["tax_year"] == 2025
    assert values["value.annual_amount"] == "493.80"
    assert [values[f"value.due_on_{n}"] for n in range(1, 5)] == [
        "2025-02-15",
        "2025-05-15",
        "2025-08-15",
        "2025-11-15",
    ]
    assert {values[f"value.amount_{n}"] for n in range(1, 5)} == {"123.45"}
    assert values["value.rhythm"] == "quarterly"
    for n in range(1, 5):
        assert record.fields[f"value.due_on_{n}"].source.quote.startswith("fällig am")
    assert record.notes == []
    # build_proposal itself is unchanged (phase 2's fixture pins typing and link:property).
    keys = [r.key for r in build_proposal(CASE.kind, CASE.extraction_model(), CASE.context)]
    assert GRUNDSTEUER_SCHEDULE_KEY not in keys


@pytest.mark.rule
def test_dates_need_the_first_year():
    record = grundsteuer_schedule_record(_with(tax_year=ExtractedValue.missing()), CASE.context)
    values = _values(record)
    assert [values[f"value.due_on_{n}"] for n in range(1, 5)] == [None] * 4
    assert record.fields["value.due_on_1"].source is not None  # the quote is kept
    assert any("Jahr" in note for note in record.notes)


@pytest.mark.rule
def test_one_text_of_dates_and_a_sum_that_does_not_fit():
    record = grundsteuer_schedule_record(
        _with(
            due_dates=_cited("15.02.2025; 15.08.2025"),
            installment_amount=_cited("200.00"),
            yearly_amount=_cited("493.80"),
        ),
        CASE.context,
    )
    values = _values(record)
    assert (values["value.due_on_1"], values["value.due_on_2"]) == ("2025-02-15", "2025-08-15")
    assert "value.due_on_3" not in values
    assert values["value.rhythm"] == "half_yearly"
    assert any("Jahresbetrag" in note for note in record.notes)


@pytest.mark.rule
def test_no_dates_read_leaves_four_slots_to_type():
    record = grundsteuer_schedule_record(_with(due_dates=ExtractedValue.missing()), CASE.context)
    values = _values(record)
    assert [values[f"value.{f}_{n}"] for n in range(1, 5) for f in ("due_on", "amount")] == [
        None
    ] * 8
    assert values["value.rhythm"] is None


@pytest.mark.rule
def test_no_property_no_record():
    context = CASE.context.model_copy(update={"properties": []})
    assert grundsteuer_schedule_record(CASE.extraction_model(), context) is None


@pytest.mark.rule
def test_the_value_from_the_confirmed_slots():
    value = grundsteuer_schedule_value(
        {
            "annual_amount": "300.00",
            "rhythm": None,
            "due_on_2": "2027-08-15",
            "amount_2": "150.00",
            "due_on_1": "2027-02-15",
            "amount_1": "150.00",
            "due_on_3": None,
            "amount_3": None,
        }
    )
    assert value == {
        "annual_amount": "300.00",
        "rhythm": None,
        "instalments": [
            {"due_on": "2027-02-15", "amount": "150.00"},
            {"due_on": "2027-08-15", "amount": "150.00"},
        ],
    }
