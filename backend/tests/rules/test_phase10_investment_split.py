"""rules/investment_split against tests/fixtures/phase10/investment_split.yaml
(docs/phases/phase-10.md §7.11 (a) 2, (a) 6, (f) 1, D301, D304)."""

from uuid import NAMESPACE_URL, UUID, uuid5

import fixture_models_phase10 as phase10
import pytest

from hausbuch.rules import investment_split as X
from hausbuch.rules.plan.savings_plans import PlanLine
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.plan import InvestmentSplit, ReserveAccounts, SavingsPlan, ValueChange

FIXTURE = phase10.load("investment_split")


def _account(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"hausbuch:fixture:phase10:investment_split:{name}")


def _reserve(names) -> frozenset[UUID]:
    return frozenset(_account(n) for n in names)


def _split(s) -> InvestmentSplit | None:
    return None if s is None else InvestmentSplit(depot=s.depot, crypto=s.crypto)


def _lines(purchases) -> list[PlanLine]:
    return [
        PlanLine(
            booked=p.booked,
            amount=p.amount,
            account_id=None if p.account is None else _account(p.account),
            account_kind=p.account_kind,
            payee=p.payee,
            category=p.category,
        )
        for p in purchases
    ]


def _plans(case) -> list[SavingsPlan]:
    return [
        SavingsPlan(
            key=p.key,
            destination_account_id=None if p.account is None else _account(p.account),
            destination_payee=p.payee,
            asset_class=p.asset_class,
            monthly=p.monthly,
            months=[p.monthly] * 3,
        )
        for p in case.plans
    ]


@pytest.mark.parametrize("case", FIXTURE.mix, ids=lambda c: c.id)
def test_the_effective_split(case):
    held = [X.Held(_account(h.account), h.asset_class, h.value) for h in case.held]
    got = X.effective_split(
        _split(case.saved),
        _plans(case),
        held,
        ValueChange(depot=case.rates.depot, crypto=case.rates.crypto),
        _reserve(case.reserve_added),
    )
    want = case.expect
    assert got.source.value == want.source
    assert got.shares == _split(want.shares)
    assert got.left_out == want.left_out
    if want.todo is None:
        assert got.todos == []
    else:
        (todo,) = got.todos
        assert todo.action == want.todo.action
        assert todo.text.startswith(f"Für {want.todo.names} fehlt ")


@pytest.mark.parametrize("case", FIXTURE.prefill, ids=lambda c: c.id)
def test_the_prefill(case):
    got = X.prefill(_lines(case.purchases), case.window, _reserve(case.reserve_added))
    if case.expect is None:
        assert got is None
        return
    assert got is not None
    assert (got.from_month, got.to_month) == (case.window[0], case.window[-1])
    assert got.bought == {"depot": case.expect.depot, "crypto": case.expect.crypto}
    assert got.split == _split(case.expect.split)


@pytest.mark.parametrize("case", FIXTURE.not_invested, ids=lambda c: c.id)
def test_not_yet_invested(case):
    got = X.not_invested(
        _lines(case.purchases),
        case.window,
        _reserve(case.reserve_added),
        _split(case.saved),
        case.mode,
    )
    if case.expect is None:
        assert got is None
        return
    assert got is not None
    assert got.invested == case.expect.invested
    assert got.amount == case.expect.amount
    assert [(x.asset_class, x.target_percent, x.actual_percent, x.amount) for x in got.lines] == [
        (x.asset_class, x.target_percent, x.actual_percent, x.amount) for x in case.expect.lines
    ]


def test_the_reserve_set_mirrors_the_default_kinds_added_and_removed():
    giro, tagesgeld, miete, depot, karte, boerse = (uuid5(NAMESPACE_URL, n) for n in "abcdef")
    kinds = {
        giro: AccountKind.BANK_CURRENT,
        tagesgeld: AccountKind.BANK_SAVINGS,
        miete: AccountKind.BANK_CURRENT,
        depot: AccountKind.DEPOT,
        karte: AccountKind.CREDIT_CARD,
        boerse: AccountKind.EXCHANGE,
    }
    lever = ReserveAccounts(added=[depot], removed=[tagesgeld])
    assert X.reserve_account_ids(kinds, frozenset({miete}), lever) == {giro, depot, karte}


def test_shares_round_half_up_and_add_up_to_a_hundred():
    from decimal import Decimal

    got = X.shares({"depot": Decimal("1.00"), "crypto": Decimal("2.00")})
    assert (got.depot, got.crypto) == (Decimal("33.3"), Decimal("66.7"))
    assert X.shares({"depot": Decimal("0.00"), "crypto": Decimal("0.00")}) is None
