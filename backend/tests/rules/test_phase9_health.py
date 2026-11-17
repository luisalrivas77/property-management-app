"""Phase 9 step 1: the real rules of rules/health/ against the fixtures of
tests/fixtures/phase9/ (docs/phases/phase-9.md §4, §5, §15.1), pure, no database.

fixture_models_phase9.py recomputes each `expect` on load with its own reference functions;
this file runs the production functions on the same cases and compares, so a rule and its
fixture can only agree by computing the same figure. A case still marked `pending` is skipped.
Plus what the fixtures cannot hold: the to-do texts, several properties, a month not closed in
the series, the D234 recode of a flagged line, and two stale accounts."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import fixture_models_phase9 as phase9
import pytest

from hausbuch.rules.health import month as M
from hausbuch.rules.health import window as W
from hausbuch.rules.household.cashflow import month_cashflow
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.health import (
    AccountCoverage,
    ClosedReasonCode,
    HealthTodoCode,
    MonthFacts,
    MonthLine,
)

CLOSED = phase9.load("closed_month")
MONTH = phase9.load("household_month")
ZERO = Decimal("0.00")
SAVING_KINDS = {"savings", "depot", "exchange"}  # services/transfers.saved_in's in-legs


def _live(case) -> None:
    if case.pending:
        pytest.skip(f"pending step {case.pending}")


def uid(key: str) -> UUID:
    return phase9.key_uuid(key)


# --- closed_month.yaml ---------------------------------------------------------------


def facts(case, month, covered_to, own_opening) -> MonthFacts:
    return MonthFacts(
        month=month,
        accounts=[
            AccountCoverage(
                account_id=uid(a.key),
                label=a.key,
                credit_card=a.kind == "credit_card",
                first_day=a.first_day,
                covered_to=covered_to.get(a.key),
                idle=a.key in case.idle_card,
                own_opening=own_opening,
            )
            for a in case.accounts
        ],
        lines_waiting=case.lines_waiting,
        transfers_open=case.transfers_open,
    )


@pytest.mark.parametrize("case", CLOSED.cases, ids=lambda c: c.id)
def test_closed_month(case):
    _live(case)
    if case.closed_months is not None:
        got = W.window(case.closed_months)
        assert got.months == case.expect_window.months
        assert got.enough == case.expect_window.enough
        assert [t.code for t in W.window_todos(got)] == case.expect_todos
        return
    runs = [(case.month, case.covered_to, False, case.expect)]
    runs += [(v.month, v.covered_to, v.own_opening, v.expect) for v in case.variants]
    for month, covered_to, own, expect in runs:
        if month is None or expect is None:
            continue
        got = W.closed(facts(case, month, covered_to, own))
        assert got.closed == expect.closed, (case.id, month)
        assert [(r.code, r.account_id, r.count) for r in got.reasons] == [
            (r.code, None if r.account is None else uid(r.account), r.count) for r in expect.reasons
        ], (case.id, month)
        assert [t.code for t in got.todos] == expect.todos, (case.id, month)


def test_closed_month_todos_name_the_button():
    month = MonthFacts(
        month="2027-03",
        accounts=[
            AccountCoverage(
                account_id=uid("card"),
                label="Karte",
                credit_card=True,
                first_day=date(2026, 1, 1),
                covered_to=date(2027, 2, 28),
            ),
            AccountCoverage(account_id=uid("new"), label="Neu"),  # nothing imported yet
        ],
        lines_waiting=1,
        transfers_open=3,
    )
    got = W.closed(month)
    assert [r.code for r in got.reasons] == [
        ClosedReasonCode.ACCOUNT_NOT_COVERED,
        ClosedReasonCode.ACCOUNT_NOT_COVERED,
        ClosedReasonCode.LINES_WAITING,
        ClosedReasonCode.TRANSFER_OPEN,
    ]
    card, new, waiting, transfers = got.todos
    assert card.action == "Kreditkartenabrechnung hochladen" and "„Karte“" in card.text
    assert card.href == f"/accounts/{uid('card')}?tab=kontoauszuege"
    assert new.action == "Kontoauszug hochladen"
    assert waiting.action == "Zu prüfen öffnen" and "Ein Umsatz" in waiting.text
    assert transfers.action == "Gegenbuchung zuordnen" and "3 Umbuchungen" in transfers.text


def test_a_month_missing_from_the_mapping_is_not_closed():
    got = W.window({"2027-01": True, "2027-03": True, "2027-04": True})
    assert got.months == ["2027-03", "2027-04"] and not got.enough
    assert W.window({}).months == [] and W.window_todos(W.window({}))[0].code is (
        HealthTodoCode.TOO_FEW_MONTHS
    )


# --- household_month.yaml ------------------------------------------------------------


def household(month, lines):
    """What the service hands the rule: the private lines (D234's flag from the Beleg link),
    the rental lines per property (one here), and the saved amount of the transfer links
    whose in-leg is a savings, depot or exchange account (phase 6 saved_in)."""
    ids = {id(line): uuid4() for line in lines}

    def as_line(line):
        return MonthLine(
            allocation_id=ids[id(line)],
            category=line.category,
            amount=line.amount,
            purchase_cost=(line.private_cost_for or "").startswith("nebenkosten_"),
        )

    private = [as_line(li) for li in lines if li.account != "rental"]
    rental = [as_line(li) for li in lines if li.account == "rental"]
    legs = defaultdict(list)
    for line in lines:
        if line.links:
            legs[line.links].append(line)
    saved, link_ids = ZERO, []
    for pair in legs.values():
        ins = [li for li in pair if li.amount > 0 and li.account in SAVING_KINDS]
        outs = [li for li in pair if li.amount < 0 and li.account not in SAVING_KINDS]
        if len(pair) == 2 and ins and outs:
            saved += ins[0].amount
            link_ids += [ids[id(li)] for li in pair]
    flow = month_cashflow(month, M.cashflow_rows(private), saved=saved)
    blocks = [M.rental_month(month, uid("flat"), rental)] if rental else []
    got = M.household_month(flow, blocks, lines=private, link_sources=link_ids)
    return got, blocks, ids


def compare(got, blocks, expect, where):
    want = expect.model_dump(exclude_none=True)
    have = got.model_dump()
    have["interest"] = sum((b.interest for b in blocks), ZERO)
    for name, value in want.items():
        assert have[name] == value, (where, name, have[name], value)


@pytest.mark.parametrize("case", MONTH.cases, ids=lambda c: c.id)
def test_household_month(case):
    _live(case)
    if case.months:
        net = ZERO
        for m in case.months:
            got, blocks, _ = household(m.month, m.lines)
            compare(got, blocks, m.expect, (case.id, m.month))
            net += got.invested_property
        assert net == case.net_invested_property
        return
    got, blocks, ids = household(case.month, case.lines)
    compare(got, blocks, case.expect, case.id)
    # principle 2: every id a field names is one of the month's lines
    for name, named in got.sources.items():
        assert set(named) <= set(ids.values()), (case.id, name)


def test_a_flagged_line_counts_as_invested_whatever_it_was_booked_as():
    """D234's recode in the rule: the notary's bill booked as Sonstiges still leaves spending
    once its document is the Beleg of a Nebenkosten row; flagging is idempotent."""
    salary = MonthLine(allocation_id=uuid4(), category=C.HH_SALARY, amount=Decimal("3000.00"))
    notary = MonthLine(
        allocation_id=uuid4(),
        category=C.HH_OTHER,
        amount=Decimal("-1800.00"),
        purchase_cost=True,
    )
    lines = [salary, notary]
    flow = month_cashflow("2027-03", M.cashflow_rows(lines), saved=ZERO)
    got = M.household_month(flow, lines=lines)
    assert got.private_spending == ZERO and got.saved == Decimal("3000.00")
    assert got.invested_property == Decimal("1800.00") and got.invested == Decimal("1800.00")
    assert got.sources["invested_property"] == [notary.allocation_id]
    already = notary.model_copy(update={"category": C.HH_INVESTMENT_PROPERTY})
    assert M.cashflow_rows([already]) == M.cashflow_rows([notary])


def test_two_properties_add_up():
    def rent(prop, amount, principal):
        return M.rental_month(
            "2027-03",
            uid(prop),
            [
                MonthLine(allocation_id=uuid4(), category=C.RENT_COLD, amount=Decimal(amount)),
                MonthLine(
                    allocation_id=uuid4(), category=C.LOAN_PRINCIPAL, amount=Decimal(principal)
                ),
            ],
        )

    flow = month_cashflow("2027-03", [], saved=ZERO)
    got = M.household_month(
        flow, [rent("one", "900.00", "-300.00"), rent("two", "700.00", "-200.00")]
    )
    assert got.rental_result == Decimal("1600.00") and got.principal == Decimal("500.00")
    assert got.rental_cash == Decimal("1100.00") and len(got.sources["rental_result"]) == 2


def test_lines_that_do_not_match_the_cashflow_are_refused():
    line = MonthLine(allocation_id=uuid4(), category=C.HH_SALARY, amount=Decimal("10.00"))
    flow = month_cashflow("2027-03", [], saved=ZERO)
    with pytest.raises(ValueError):
        M.household_month(flow, lines=[line])
    other = M.rental_month("2027-04", uid("flat"), [])
    with pytest.raises(ValueError):
        M.household_month(flow, [other])
