"""Phase 6 step 5b's price rules against their fixtures (docs/phases/phase-6.md D167, D174):
valuation.yaml (rules.valuation.price_on, value, to_eur) and issuer_nav.yaml (nav_rows,
failed_days). Step 5b builds `value` as well as `price_on` (the Anlagen Übersicht values each
position), so every valuation case runs here; step 6 sums them into the net worth."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import errors as E
from hausbuch.rules import valuation
from hausbuch.schemas.household import Position, PricePoint, PriceSourceKind
from hausbuch.schemas.prices import FxRate

VALUATION = phase6.load("valuation")
ISSUER_NAV = phase6.load("issuer_nav")
ASSET = uuid4()
FETCHED = datetime(2026, 7, 1, 7, tzinfo=UTC)


def _points(rows) -> list[PricePoint]:
    return [
        PricePoint(
            asset_id=ASSET, on=r.on, price_eur=r.price_eur, source=r.source, fetched_at=FETCHED
        )
        for r in rows
    ]


@pytest.mark.rule
@pytest.mark.parametrize("case", VALUATION.cases, ids=lambda c: c.id)
def test_value(case):
    position = Position(
        account_id=uuid4(),
        asset_id=ASSET,
        units=case.position.units,
        reconciled_through=case.position.reconciled_through,
    )
    price = None if case.position.cash else valuation.price_on(_points(case.prices), case.on)
    result = valuation.value(position, price, case.on, cash=case.position.cash)
    expect = case.expect
    if expect.price is None:
        assert result.price is None
    else:
        assert (result.price.on, result.price.source) == (expect.price.on, expect.price.source)
    assert result.stale is expect.stale
    assert result.value_eur == expect.value_eur
    assert result.not_valued_reason == expect.not_valued_reason
    warning = E.VALUATION_STALE_PRICE if result.stale else None
    assert warning == expect.warning


@pytest.mark.rule
@pytest.mark.parametrize("case", VALUATION.fx_cases, ids=lambda c: c.id)
def test_to_eur(case):
    rates = [FxRate(on=r.on, per_eur=r.per_eur) for r in case.rates]
    assert valuation.to_eur(case.figure.amount, case.figure.on, rates) == (
        case.expect.rate_on,
        case.expect.price_eur,
    )


@pytest.mark.rule
def test_latest_fetch_wins_within_one_source():
    early = PricePoint(
        asset_id=ASSET,
        on=date(2026, 6, 30),
        price_eur=Decimal("10"),
        source=PriceSourceKind.STATEMENT,
        fetched_at=FETCHED,
    )
    late = early.model_copy(
        update={"price_eur": Decimal("11"), "fetched_at": FETCHED + timedelta(hours=1)}
    )
    assert valuation.price_on([late, early], date(2026, 6, 30)) == late
    assert valuation.price_on([early, late], date(2026, 6, 29)) is None


@pytest.mark.rule
def test_no_price_is_no_source():
    position = Position(account_id=uuid4(), asset_id=ASSET, units=Decimal("1"))
    result = valuation.value(position, None, date(2026, 6, 30))
    assert result.not_valued_reason == E.PRICE_NO_SOURCE
    assert result.value_eur is None


@pytest.mark.rule
def test_no_reconciled_date_is_valued():
    position = Position(account_id=uuid4(), asset_id=ASSET, units=Decimal("2"))
    (price,) = _points([phase6.PriceRow(on=date(2026, 6, 30), price_eur="5", source="user")])
    assert valuation.value(position, price, date(2026, 7, 1)).value_eur == Decimal("10.00")


@pytest.mark.rule
@pytest.mark.parametrize("case", ISSUER_NAV.cases, ids=lambda c: c.id)
def test_issuer_nav(case):
    rates = [FxRate(on=r.on, per_eur=r.per_eur) for r in case.rates]
    navs = [] if case.unparsable or case.no_adapter else case.navs
    rows = valuation.nav_rows(navs, case.first_movement_on, rates)
    assert [(r.on, r.price_eur) for r in rows] == [(s.on, s.price_eur) for s in case.expect.stored]
    if case.failed_on:
        runs = [(day, True) for day in case.failed_on]
        card = E.PRICE_SOURCE_FAILED if valuation.source_failed(runs) else None
        assert card == case.expect.card
    if case.unparsable:
        last = PricePoint(
            asset_id=ASSET,
            on=case.last_price_on,
            price_eur=Decimal("1"),
            source=PriceSourceKind.ISSUER_NAV,
            fetched_at=FETCHED,
        )
        assert valuation.is_stale(last, case.today) is case.expect.stale
    if case.prices or case.no_adapter:
        stored = [
            phase6.PriceRow(on=r.on, price_eur=r.price_eur, source=PriceSourceKind.ISSUER_NAV)
            for r in rows
        ]
        points = _points(case.prices + stored)
        best = valuation.price_on(points, max(p.on for p in points))
        assert best.source is case.expect.valued_by


@pytest.mark.rule
def test_nav_rows_skips_stored_days_and_a_day_without_a_rate():
    navs = [
        phase6.NavPoint(on=date(2026, 6, 24), nav="10", currency="USD"),
        phase6.NavPoint(on=date(2026, 6, 25), nav="11", currency="USD"),
        phase6.NavPoint(on=date(2026, 6, 26), nav="12", currency="USD"),
    ]
    rates = [FxRate(on=date(2026, 6, 25), per_eur="1.1")]
    rows = valuation.nav_rows(navs, date(2026, 6, 1), rates, stored_days=[date(2026, 6, 25)])
    assert [(r.on, r.price_eur, r.rate_on) for r in rows] == [
        (date(2026, 6, 26), Decimal("10.90909091"), date(2026, 6, 25))
    ]


@pytest.mark.rule
def test_failed_days_ends_at_a_success_or_a_gap():
    d = date(2026, 6, 30)
    runs = [(d - timedelta(days=i), True) for i in range(8)]
    assert valuation.failed_days(runs) == 8
    assert valuation.failed_days([*runs, (d - timedelta(days=3), False)]) == 3
    gap = [(d, True), (d - timedelta(days=1), True), (d - timedelta(days=3), True)]
    assert valuation.failed_days(gap) == 2
    assert valuation.failed_days([(d, False)]) == 0
    assert valuation.failed_days([]) == 0
