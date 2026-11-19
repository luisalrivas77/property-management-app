"""D174 amended 19 Nov 2026: issuer_nav.yaml's read, catalogue, discovery, ECB history and
first-read check cases replayed on the recorded samples (no network, no database)."""

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import valuation
from hausbuch.services.prices import ecb_fx
from hausbuch.services.prices.fetch import Sources
from hausbuch.services.prices.http import SourceError
from hausbuch.services.prices.issuer_nav import ADAPTERS, read_points

ISSUER_NAV = phase6.load("issuer_nav")


class Recorder:
    """A Fetch that answers recorded samples by URL substring (the longest key first) and
    records every call; a POST body is recorded with its URL."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = {
            part: (phase6.SAMPLES / sample).read_bytes()
            for part, sample in sorted(answers.items(), key=lambda a: -len(a[0]))
        }
        self.calls: list[tuple[str, bytes | None]] = []

    def __call__(self, url, headers, timeout, max_bytes, *body):
        self.calls.append((url, body[0] if body else None))
        for part, answer in self.answers.items():
            if part in url:
                return 200, answer
        return 404, b""


def _adapter(issuer: str):
    if issuer not in ADAPTERS:
        pytest.skip(f"no {issuer} adapter yet (D174 amended 19 Nov 2026, not built)")
    return ADAPTERS[issuer]


@pytest.mark.parametrize("case", ISSUER_NAV.read_cases, ids=lambda c: c.id)
def test_read_cases(case):
    adapter = _adapter(case.issuer)(Recorder(case.answers))
    if case.expect.error is not None:
        with pytest.raises(SourceError) as raised:
            read_points(adapter, case.fund_ref or case.isin, case.isin)
        assert raised.value.kind == case.expect.error
        return
    found = read_points(adapter, case.fund_ref or case.isin, case.isin)
    assert found.isin == case.expect.isin
    assert found.currency == case.expect.currency
    assert sorted(found.points, key=lambda p: p.on) == sorted(case.expect.navs, key=lambda p: p.on)


@pytest.mark.parametrize("case", ISSUER_NAV.catalogue_cases, ids=lambda c: c.id)
def test_catalogue_cases(case):
    adapter = _adapter(case.issuer)
    assert adapter.parse_catalogue((phase6.SAMPLES / case.sample).read_bytes()) == case.expect


@pytest.mark.parametrize("case", ISSUER_NAV.discovery_cases, ids=lambda c: c.id)
def test_discovery_cases(case):
    for issuer in ("amundi", "bnp", "hanetf"):  # the adapters the cases' answers belong to
        _adapter(issuer)
    fetch = Recorder(case.answers)
    sources = Sources(fetch=fetch, coingecko=None)
    found = sources.discover(case.isin, case.name)
    if case.expect.issuer is None:
        assert found is None
    else:
        assert found is not None
        assert (found.issuer, found.fund_ref, found.isin) == (
            case.expect.issuer,
            case.expect.fund_ref,
            case.isin,
        )
    assert len(fetch.calls) == case.expect.requests


@pytest.mark.parametrize("case", ISSUER_NAV.ecb_history_cases, ids=lambda c: c.id)
def test_ecb_history_cases(case):
    if not hasattr(ecb_fx, "parse_history"):
        pytest.skip("the ECB history ZIP is not read yet (D174 amended 19 Nov 2026)")
    rates = ecb_fx.parse_history((phase6.SAMPLES / case.sample).read_bytes())
    for code, expected in case.expect.items():
        assert [(r.on, r.per_eur) for r in rates[code]] == [(r.on, r.per_eur) for r in expected]


@pytest.mark.parametrize("case", ISSUER_NAV.guard_cases, ids=lambda c: c.id)
def test_guard_cases(case):
    if not hasattr(valuation, "nav_check"):
        pytest.skip("the first-read check is not built yet (D174 amended 19 Nov 2026)")
    assert valuation.NAV_CHECK_TOLERANCE == phase6.NAV_CHECK_TOLERANCE
    reports = {r.on: r.price_eur for r in case.reports}
    check = valuation.nav_check(case.navs, case.rates, reports)
    if case.expect.checked_on is None:
        assert check is None
    else:
        assert check is not None
        assert (check.on, check.refused) == (case.expect.checked_on, case.expect.refused)


def test_every_adapter_has_a_case():
    named = {c.issuer for c in ISSUER_NAV.parse_cases + ISSUER_NAV.read_cases}
    assert set(ADAPTERS) <= named
