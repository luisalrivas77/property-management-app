"""rules/net_worth.py against net_worth.yaml (docs/phases/phase-6.md D167 (4), §9, §14): one
month-end's net worth by class and by owner to the euro, each position valued by
rules.valuation as the service does. The fixture's `pending` names this step, so it runs.
Plus the cases the fixture cannot hold: an uneven split, an account without shares, a
credit card, the bank balance's anchor day and a loan's balance through its schedule."""

from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from uuid import uuid4

import fixture_models_phase6 as phase6

from hausbuch.rules import net_worth, valuation
from hausbuch.rules.loan_schedule import build_schedule
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import NetWorthClass, Position, PricePoint
from hausbuch.schemas.loan import LoanTerms
from hausbuch.schemas.net_worth import AccountTab

FIXTURE = phase6.load("net_worth")
C = NetWorthClass


def _fixture_parts():
    parts = []
    snapshots = []
    on = FIXTURE.on
    for account in FIXTURE.accounts:
        cls = net_worth.class_of(account.kind)
        if account.balance is not None:
            parts.append(net_worth.Part(cls, account.balance, account.shares, account.id))
            snapshots.append((account.id, None, account.balance))
        for position in account.positions:
            asset_id = uuid4()
            point = (
                None
                if position.cash
                else PricePoint(
                    asset_id=asset_id,
                    on=position.price_on,
                    price_eur=position.price_eur,
                    source=position.source,
                    fetched_at=datetime(2026, 11, 1, tzinfo=UTC),
                )
            )
            valued = valuation.value(
                Position(account_id=uuid4(), asset_id=asset_id, units=position.units),
                point,
                on,
                cash=position.cash,
            )
            parts.append(net_worth.Part(cls, valued.value_eur, account.shares, account.id))
            snapshots.append((account.id, position.asset, valued.value_eur))
    prop = FIXTURE.property
    parts.append(net_worth.Part(C.PROPERTY, prop.estimate, prop.shares, "property"))
    parts.append(net_worth.Part(C.LIABILITIES, -prop.loan_balance, prop.shares, "loan"))
    return parts, snapshots


def test_the_fixture_month_end_by_class_and_by_owner():
    parts, snapshots = _fixture_parts()
    result = net_worth.totals(parts, FIXTURE.owners)
    expect = FIXTURE.expect
    assert result.by_class == expect.by_class
    assert result.by_owner == expect.by_owner
    assert result.total == expect.total
    assert result.unassigned == [] and result.unvalued == 0
    assert [(s.account, s.asset, s.value_eur) for s in expect.snapshots] == snapshots


def test_the_fixture_classes_follow_the_account_kind():
    assert net_worth.class_of(AccountKind.EXCHANGE) is C.CRYPTO  # its EUR too
    assert net_worth.class_of(AccountKind.WALLET) is C.CRYPTO
    assert net_worth.class_of(AccountKind.CREDIT_CARD) is C.LIABILITIES
    assert net_worth.class_of(AccountKind.BANK_SAVINGS) is C.BANK


def test_an_uneven_split_sums_to_the_figure():
    thirds = {"A": Fraction(1, 3), "B": Fraction(2, 3)}
    assert net_worth.split(Decimal("100.00"), thirds) == {
        "A": Decimal("33.33"),
        "B": Decimal("66.67"),
    }
    halves = {"B": Fraction(1, 2), "A": Fraction(1, 2)}
    # One cent left over: the tie goes to the first owner by key.
    assert net_worth.split(Decimal("0.01"), halves) == {"A": Decimal("0.01"), "B": Decimal("0.00")}
    debt = net_worth.split(Decimal("-100.01"), halves)
    assert sum(debt.values()) == Decimal("-100.01")
    assert sorted(debt.values()) == [Decimal("-50.01"), Decimal("-50.00")]


def test_an_account_without_shares_counts_in_its_class_but_in_no_owner():
    parts = [
        net_worth.Part(C.BANK, Decimal("10.00"), {}, "no_shares"),
        net_worth.Part(C.BANK, Decimal("5.00"), {"A": Fraction(1)}, "sole"),
        net_worth.Part(C.CRYPTO, None, {"A": Fraction(1)}, "no_price"),
        net_worth.Part(C.LIABILITIES, Decimal("-312.75"), {"A": Fraction(1)}, "card"),
    ]
    result = net_worth.totals(parts, ["A", "B"])
    assert result.total == Decimal("-297.75")
    assert result.by_class[C.BANK] == Decimal("15.00")
    assert result.by_owner == {"A": Decimal("-307.75"), "B": Decimal("0.00")}
    assert result.unassigned == ["no_shares"] and result.unvalued == 1


def test_the_bank_balance_counts_from_the_day_after_the_anchor():
    lines = [
        (date(2026, 9, 30), Decimal("999.00")),  # in the anchor already
        (date(2026, 10, 1), Decimal("-20.00")),
        (date(2026, 10, 31), Decimal("100.00")),
        (date(2026, 11, 1), Decimal("-5.00")),
    ]
    anchor = Decimal("1000.00")
    assert net_worth.bank_balance(anchor, date(2026, 9, 30), lines, date(2026, 10, 31)) == (
        Decimal("1080.00")
    )
    assert net_worth.bank_balance(anchor, date(2026, 9, 30), lines, date(2026, 9, 30)) == anchor


def test_a_loans_balance_by_its_schedule():
    terms = LoanTerms(
        principal=Decimal("100000.00"),
        nominal_rate=Decimal("0.036"),
        monthly_payment=Decimal("500.00"),
        disbursed_on=date(2026, 3, 15),
        first_payment_on=date(2026, 4, 30),
    )
    entries = build_schedule(terms).entries
    balance = net_worth.loan_balance
    assert balance(entries, terms.principal, terms.disbursed_on, date(2026, 3, 14)) == 0
    assert balance(entries, terms.principal, terms.disbursed_on, date(2026, 4, 29)) == (
        terms.principal
    )
    may = balance(entries, terms.principal, terms.disbursed_on, date(2026, 5, 31))
    assert may == entries[1].closing_balance < entries[0].closing_balance < terms.principal


def test_the_konten_tabs_sum_their_accounts_lines():
    """Phase 10 §7.6 Konten (2): both bank kinds are one tab, an exchange and a wallet each
    their own (they shared `by_class.crypto` before), a credit card's debt its tab's negative
    total; a line without a value counts nowhere, and a tab with no line is absent."""
    K = AccountKind
    tabs = net_worth.tab_totals(
        [
            (K.WALLET, Decimal("300.00")),
            (K.BANK_SAVINGS, Decimal("1000.00")),
            (K.BANK_CURRENT, Decimal("250.50")),
            (K.EXCHANGE, Decimal("120.00")),
            (K.EXCHANGE, None),
            (K.CREDIT_CARD, Decimal("-40.00")),
            (K.WALLET, Decimal("5.00")),
        ]
    )
    assert list(tabs.items()) == [
        (AccountTab.BANK, Decimal("1250.50")),
        (AccountTab.CARD, Decimal("-40.00")),
        (AccountTab.EXCHANGE, Decimal("120.00")),
        (AccountTab.WALLET, Decimal("305.00")),
    ]
    assert net_worth.tab_totals([(K.DEPOT, None)]) == {AccountTab.DEPOT: Decimal("0.00")}
    assert net_worth.tab_totals([]) == {}
