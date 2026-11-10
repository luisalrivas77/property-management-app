"""The yearly questions as data (docs/phases/phase-4b.md §4.2, D110): every file parses, keys
are unique, every input_kind is a real enum member, every reminder has its text, and the 2026
keys are pinned (a renamed key breaks carry-forward)."""

import pytest
from pydantic import ValidationError

from hausbuch.rules.errors import TAX_NO_RULE_SET, RuleError
from hausbuch.rules.tax.registry import questionnaire, questionnaire_years
from hausbuch.schemas.employment import Questionnaire
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.tax import TaxInputKind

KEYS_2026 = {
    "owner": (
        "owner.pay_documents",
        "owner.tax_class",
        "owner.commute",
        "owner.home_office_days",
        "owner.work_room",
        "owner.equipment_new",
        "owner.equipment_prior",
        "owner.subscriptions",
        "owner.telecom",
        "owner.account_fee",
        "owner.union_association",
        "owner.training",
        "owner.job_move",
        "owner.own_expense_trips",
        "owner.replacement_income",
        "owner.insurance_status",
        "owner.other_insurance",
    ),
    "household": (
        "household.children",
        "household.childcare",
        "household.services_35a",
        "household.donations",
        "household.extraordinary",
        "household.other_not_modelled",
    ),
    "property": ("property.trips", "property.private_costs"),
}


def test_the_shipped_years():
    assert questionnaire_years() == (2026,)


@pytest.mark.parametrize("year", questionnaire_years())
def test_each_file_loads_and_is_its_year(year):
    q = questionnaire(year)
    assert (q.tax_year, q.reused_from) == (year, None)


def test_the_2026_keys_are_pinned():
    q = questionnaire(2026)
    by_scope = {scope: tuple(x.key for x in q.questions if x.scope == scope) for scope in KEYS_2026}
    assert {s: set(k) for s, k in by_scope.items()} == {s: set(k) for s, k in KEYS_2026.items()}
    assert len(q.questions) == 25


def test_the_slots_for_two_owners_and_one_property():
    """§4.2: 17 × 2 + 6 + 2 × 1 = 42 answer slots; the progress counts slots."""
    q = questionnaire(2026)
    per = {s: sum(1 for x in q.questions if x.scope == s) for s in KEYS_2026}
    assert per["owner"] * 2 + per["household"] + per["property"] * 1 == 42


def test_every_target_names_a_real_kind_and_every_reminder_has_text():
    for question in questionnaire(2026).questions:
        for target in question.targets:
            if target.kind == "reminder":
                assert target.reminder_de
            elif target.kind == "return_input":
                assert isinstance(target.input_kind, ReturnInputKind)
            else:
                assert target.input_kind is TaxInputKind.PRIVATE_COST


def test_the_value_filters_split_shared_kinds():
    q = questionnaire(2026)
    assert q.question("owner.subscriptions").targets[0].value_match == {
        "item_class": "subscription"
    }
    assert q.question("owner.account_fee").targets[0].value_match == {
        "expense_class": "account_fee"
    }
    assert q.question("owner.account_fee").default_answer == "yes"
    assert q.question("property.trips").targets[0].value_match == {"cost_class": "travel"}


def test_spanning_kinds_are_reconfirmed_never_copied():
    q = questionnaire(2026)
    assert q.question("household.children").carry_forward == "reconfirm"
    assert q.question("owner.equipment_prior").carry_forward == "reconfirm"
    assert q.question("owner.pay_documents").carry_forward == "none"


def test_a_later_year_reuses_the_latest_file_marked():
    q = questionnaire(2027)
    assert (q.tax_year, q.reused_from) == (2027, 2026)
    assert [x.key for x in q.questions] == [x.key for x in questionnaire(2026).questions]


def test_a_year_before_every_file_has_no_questionnaire():
    with pytest.raises(RuleError) as excinfo:
        questionnaire(2025)
    assert excinfo.value.code == TAX_NO_RULE_SET


def _data(**changes) -> dict:
    return questionnaire(2026).model_dump(mode="json") | changes


def _edit(target: str, **changes) -> list[dict]:
    return [q | changes if q["key"] == target else q for q in _data()["questions"]]


@pytest.mark.parametrize(
    "questions",
    [
        _data()["questions"] + [_data()["questions"][0]],  # a duplicate key
        _edit("owner.commute", scope="household"),  # key prefix and scope disagree
        _edit("owner.tax_class", choices=None),  # a choice question without choices
        _edit("owner.telecom", choices=[{"code": "a", "label_de": "A"}]),  # yes/no with choices
        _edit("owner.job_move", carry_forward="copy"),  # a reminder never carries
        _edit("household.children", targets=[{"kind": "return_input", "input_kind": "commute"}]),
        _edit("owner.commute", targets=[{"kind": "tax_input", "input_kind": "private_cost"}]),
        _edit("owner.job_move", targets=[{"kind": "reminder"}]),  # a reminder without text
        _edit("owner.commute", targets=[{"kind": "return_input", "input_kind": "lottery"}]),
        _edit("owner.commute", key="owner.Commute"),
    ],
)
def test_an_invalid_questionnaire_is_refused(questions):
    with pytest.raises(ValidationError):
        Questionnaire.model_validate(_data(questions=questions))
