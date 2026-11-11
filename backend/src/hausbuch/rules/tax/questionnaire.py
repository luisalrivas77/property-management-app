"""§5.11, D110: the yearly questionnaire ("Jahresfragen") as rules over its data file.

Pure functions; the year's questions come from `rules/tax/questionnaire/<year>.yaml` through
`registry.questionnaire` (passed in). One **slot** per question and scope target, in file order:
an owner question once per owner, a property question once per property, a household question
once.

- `prefill`: per slot, the current answer of Y−1 for the same key and scope (a key that Y's file
  no longer has gets no slot), the proposed answer (the prior answer when the question carries
  and it was not "not_applicable"; the question's default when there is no prior answer), the
  rows a `copy` target proposes for Y (the Y−1 rows of the kind, dates moved one year, with
  `carried_from_id`; a target without a `value_match` leaves the rows other questions' filters
  claim), and the spanning rows a `reconfirm` target lists (a child with months in Y, an
  equipment item with a slice in Y). A spanning kind is never copied (`carried_allowed`: of
  work_equipment only a subscription, a yearly item, carries).
- `progress`: a slot is answered when a current answer of Y exists (yes, no or not_applicable).
- `reminders`: the `reminder_de` of every reminder target whose question is answered yes.
- `extraordinary_threshold`: the zumutbare Belastung (§33 Abs. 3 EStG) over the bands.
"""

import calendar
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from enum import Enum
from uuid import UUID

from hausbuch.rules.errors import RuleError
from hausbuch.rules.tax.anlage_n._base import SPANNING_KINDS, ZERO, current
from hausbuch.rules.tax.anlage_n.children import child_months
from hausbuch.rules.tax.anlage_n.equipment import equipment_amount
from hausbuch.rules.tax.rounding import cents
from hausbuch.schemas.employment import (
    AnlageNRuleSet,
    CommuteFact,
    EquipmentFact,
    OpenQuestion,
    OtherExpenseFact,
    Question,
    Questionnaire,
    QuestionnaireAnswer,
    QuestionnaireProgress,
    QuestionPrefill,
    ReturnInputCreate,
    ReturnInputFact,
    carried_allowed,
)
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.performance import Tariff
from hausbuch.schemas.tax import TaxInputCreate, TaxInputFact

ACCOUNT_FEE_MATCH = {"expense_class": "account_fee"}
ACCOUNT_FEE_LABEL = "Kontoführung"

Slot = tuple[Question, UUID | None, UUID | None]


def slots(questionnaire: Questionnaire, owners: Sequence[UUID], properties: Sequence[UUID]):
    """(question, owner_id, property_id) per answer slot, in file order."""
    out: list[Slot] = []
    for question in questionnaire.questions:
        if question.scope == "owner":
            out += [(question, o, None) for o in owners]
        elif question.scope == "property":
            out += [(question, None, p) for p in properties]
        else:
            out.append((question, None, None))
    return out


def _plus_years(day: date, years: int) -> date:
    """`day` moved by `years`; 29 Feb becomes 28 Feb in a year without it."""
    year = day.year + years
    return day.replace(year=year, day=min(day.day, calendar.monthrange(year, day.month)[1]))


def shift(value, years: int = 1):
    """A carried value's dates move to the new year (a route's period, an item's purchase)."""
    if isinstance(value, CommuteFact):
        return value.model_copy(
            update={
                "period_from": _plus_years(value.period_from, years),
                "period_to": _plus_years(value.period_to, years),
            }
        )
    if isinstance(value, EquipmentFact):
        return value.model_copy(update={"bought_on": _plus_years(value.bought_on, years)})
    return value


def _coded(value) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def matches(value, match: dict[str, str]) -> bool:
    return all(_coded(getattr(value, k, None)) == v for k, v in match.items())


def _answer_of(
    answers: Iterable[QuestionnaireAnswer], key: str, owner: UUID | None, prop: UUID | None
) -> QuestionnaireAnswer | None:
    return next(
        (
            a
            for a in answers
            if a.question_key == key and a.owner_id == owner and a.property_id == prop
        ),
        None,
    )


def _in_force(row: ReturnInputFact, tax_year: int, rs: AnlageNRuleSet) -> bool:
    """A spanning row still counting in `tax_year`: a child with months, an item with a slice."""
    if row.kind is K.CHILD:
        return child_months(row.value, tax_year)[0] > 0
    try:
        return equipment_amount(row.value, tax_year, rs) > 0
    except RuleError:
        return False


def _recurring(row: TaxInputFact) -> bool:
    """A row read from a bill (phase 2 D135: its document, a category) is a one-off cost: a
    copy confirmed next year would deduct it twice and count it twice in the 15 % test (phase 3
    D137 review F2). Hand-typed rows (postage, a membership, trips) are copied as before."""
    return row.document_id is None and getattr(row.value, "category", None) is None


def prefill(
    tax_year: int,
    questionnaire: Questionnaire,
    prior_answers: Sequence[QuestionnaireAnswer],
    prior_rows: Sequence[ReturnInputFact],
    owners: Sequence[UUID],
    properties: Sequence[UUID],
    *,
    prior_tax_rows: Sequence[TaxInputFact] = (),
    rule_set: AnlageNRuleSet,
) -> list[QuestionPrefill]:
    """One QuestionPrefill per slot of `tax_year` from Y−1's answers and rows (the module
    docstring). Nothing is written: the rows are proposals the user confirms."""
    prior_year = tax_year - 1
    answers = [a for a in current(prior_answers) if a.tax_year == prior_year]
    rows = current(prior_rows)
    tax_rows = current(prior_tax_rows)
    filters: dict[str, list[dict[str, str]]] = defaultdict(list)
    for question in questionnaire.questions:
        for t in question.targets:
            if t.input_kind is not None and t.value_match:
                filters[t.input_kind.value].append(t.value_match)

    out = []
    for question, owner, prop in slots(questionnaire, owners, properties):
        prior = _answer_of(answers, question.key, owner, prop)
        proposed_answer = None
        if (
            question.carry_forward != "none"
            and prior is not None
            and prior.answer != "not_applicable"
        ):
            proposed_answer = prior.answer
        if prior is None and question.default_answer is not None:
            proposed_answer = question.default_answer
        proposed: list[ReturnInputCreate | TaxInputCreate] = []
        reconfirm: list[ReturnInputFact] = []
        for t in question.targets:
            if t.kind == "reminder":
                continue
            kind = t.input_kind.value

            def wanted(value, t=t, kind=kind) -> bool:
                if t.value_match:
                    return matches(value, t.value_match)
                return not any(matches(value, m) for m in filters.get(kind, []))

            if question.carry_forward == "copy" and t.kind == "return_input":
                for r in rows:
                    if (
                        r.kind.value == kind
                        and carried_allowed(r.kind, r.value)
                        and r.tax_year == prior_year
                        and r.owner_id == owner
                        and wanted(r.value)
                    ):
                        proposed.append(
                            ReturnInputCreate(
                                owner_id=r.owner_id,
                                kind=r.kind,
                                tax_year=tax_year,
                                value=shift(r.value),
                                carried_from_id=r.id,
                            )
                        )
                if (
                    not proposed
                    and prior is None
                    and question.default_answer == "yes"
                    and t.value_match == ACCOUNT_FEE_MATCH
                ):
                    proposed.append(
                        ReturnInputCreate(
                            owner_id=owner,
                            kind=K.OTHER_EXPENSE,
                            tax_year=tax_year,
                            value=OtherExpenseFact(
                                expense_class="account_fee",
                                label=ACCOUNT_FEE_LABEL,
                                amount=rule_set.account_fee_flat,
                            ),
                        )
                    )
            elif question.carry_forward == "copy" and t.kind == "tax_input":
                for r in tax_rows:
                    if (
                        r.kind.value == kind
                        and r.tax_year == prior_year
                        and r.property_id == prop
                        and wanted(r.value)
                        and _recurring(r)
                    ):
                        proposed.append(
                            TaxInputCreate(
                                property_id=r.property_id,
                                kind=r.kind,
                                tax_year=tax_year,
                                value=r.value,
                                carried_from_id=r.id,
                            )
                        )
            elif question.carry_forward == "reconfirm":
                reconfirm += [
                    r
                    for r in rows
                    if r.kind.value == kind
                    and r.kind in SPANNING_KINDS
                    and r.owner_id == owner
                    and r.tax_year <= prior_year
                    and _in_force(r, tax_year, rule_set)
                ]
        out.append(
            QuestionPrefill(
                key=question.key,
                scope=question.scope,
                owner_id=owner,
                property_id=prop,
                prior_answer=prior,
                proposed_answer=proposed_answer,
                proposed_rows=proposed,
                reconfirm_rows=reconfirm,
            )
        )
    return out


def progress(
    tax_year: int,
    questionnaire: Questionnaire,
    answers: Sequence[QuestionnaireAnswer],
    owners: Sequence[UUID],
    properties: Sequence[UUID],
) -> QuestionnaireProgress:
    """Answered and open slots of `tax_year` (a current answer of the year answers a slot)."""
    of_year = [a for a in current(answers) if a.tax_year == tax_year]
    answered, open_slots = 0, []
    for question, owner, prop in slots(questionnaire, owners, properties):
        if _answer_of(of_year, question.key, owner, prop) is not None:
            answered += 1
        else:
            open_slots.append(
                OpenQuestion(
                    key=question.key, scope=question.scope, owner_id=owner, property_id=prop
                )
            )
    return QuestionnaireProgress(
        answered=answered, total=answered + len(open_slots), open=open_slots
    )


def reminders(answers: Sequence[QuestionnaireAnswer], questionnaire: Questionnaire) -> list[str]:
    """The reminder sentences of the questions answered yes in the questionnaire's year, in
    file order, each once."""
    yes = {
        a.question_key
        for a in current(answers)
        if a.tax_year == questionnaire.tax_year and a.answer == "yes"
    }
    return [
        t.reminder_de
        for question in questionnaire.questions
        if question.key in yes
        for t in question.targets
        if t.kind == "reminder" and t.reminder_de
    ]


def extraordinary_threshold(gde: Decimal, children: int, joint: bool, tariff: Tariff) -> Decimal:
    """§33 Abs. 3 EStG: the zumutbare Belastung summed over the bands (BFH VI R 75/14: each
    rate on its band only), to the cent. Without children the rate is the splitting or the
    basic tariff's; with one or two, or three and more, the children's rate."""
    bands = tariff.extraordinary_burden_bands
    assert bands is not None, "the tariff's §33 bands"
    total, lower = ZERO, ZERO
    for band in bands:
        if children == 0:
            rate = band.rate_joint if joint else band.rate_single
        elif children <= 2:
            rate = band.rate_one_two_children
        else:
            rate = band.rate_three_plus
        upper = gde if band.up_to is None else min(gde, band.up_to)
        if upper > lower:
            total += (upper - lower) * rate
        if band.up_to is None or gde <= band.up_to:
            break
        lower = band.up_to
    return cents(total)
