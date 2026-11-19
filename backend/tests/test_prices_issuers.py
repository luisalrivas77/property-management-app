"""D174 amended 19 Nov 2026: issuer_nav.yaml's read, catalogue, discovery, ECB history and
first-read check cases replayed on the recorded samples (no network, no database)."""

import fixture_models_phase6 as phase6
import pytest

from hausbuch.rules import valuation
from hausbuch.services.prices import ecb_fx
from hausbuch.services.prices.assets import NavSource
from hausbuch.services.prices.fetch import Sources
from hausbuch.services.prices.http import SourceError
from hausbuch.services.prices.issuer_nav import ADAPTERS, invesco, read_points

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


# --- the review of the build (D174 amended after the review, 19 Nov 2026) -------------------


def test_a_lookup_miss_is_counted_apart_from_errors():
    """(d), (k): Xtrackers answers 404 for an ISIN it does not list: no failure and no
    error, one lookup miss for the run's log line."""
    sources = Sources(fetch=Recorder({}), coingecko=None)
    assert sources.discover("IE000INVENT5", "Xtrackers Invented UCITS ETF") is None
    assert dict(sources.misses) == {("issuer_nav:xtrackers", "HTTPError", 404): 1}
    assert not sources.errors


@pytest.mark.parametrize("second", ["Invesco Invented Equity UCITS ETF Dist", None])
def test_invesco_refuses_a_workbook_with_more_than_one_share_class_block(second, monkeypatch):
    """(b), (i): a second block, named for another share class or for the same one, makes
    the workbook unreadable, whichever block names the JSON's share class."""
    case = next(c for c in ISSUER_NAV.read_cases if c.id == "invesco_parse")
    one = invesco.xlsx.rows((phase6.SAMPLES / "invesco_performance.xlsx").read_bytes())
    name = next(r["A"] for r in one if set(r) == {"A"} and r["A"].startswith("Invesco"))
    block = [{"A": second or name}, {"A": "Date", "D": "NAV"}, {"A": "46203.0", "D": "23.41768"}]
    monkeypatch.setattr(invesco.xlsx, "rows", lambda body: one + block)
    adapter = ADAPTERS["invesco"](Recorder(case.answers))
    with pytest.raises(SourceError) as raised:
        read_points(adapter, case.isin, case.isin)
    assert raised.value.kind == "parse"


BNP_ISIN, BNP_NAME = "IE000INVENT8", "BNPP Invented Equity ETF"


def test_a_stored_bnp_currency_is_never_moved_to_another():
    """(h): the stored EUR reference has no rows any more while USD has: the lookup finds
    nothing, the fund is unresolved, and the reference is not switched."""
    fetch = Recorder({"currency=EUR": "bnp_navs_empty.json", "currency=USD": "bnp_navs.json"})
    sources = Sources(fetch=fetch, coingecko=None)
    stored = NavSource(issuer="bnp", fund_ref=f"EUR:{BNP_ISIN}", isin=BNP_ISIN)
    assert isinstance(sources.nav(stored, None), SourceError)
    assert sources.relocate(stored, BNP_NAME) is None
    assert sources.is_unresolved(stored, BNP_ISIN)
    assert len(fetch.calls) == 4  # the stored EUR, then USD, GBP and CHF once each


def test_a_stored_bnp_currency_no_other_currency_answers_fails_as_before():
    """(h): no currency has rows: a failed fetch as before, not unresolved."""
    sources = Sources(fetch=Recorder({"currency=": "bnp_navs_empty.json"}), coingecko=None)
    stored = NavSource(issuer="bnp", fund_ref=f"EUR:{BNP_ISIN}", isin=BNP_ISIN)
    assert isinstance(sources.nav(stored, None), SourceError)
    assert sources.relocate(stored, BNP_NAME) is None
    assert not sources.is_unresolved(stored, BNP_ISIN)


class _GbpOutage(Recorder):
    def __call__(self, url, headers, timeout, max_bytes, *body):
        if "currency=GBP" in url:
            self.calls.append((url, None))
            return 503, b""
        return super().__call__(url, headers, timeout, max_bytes, *body)


def test_bnp_discovery_that_cannot_tell_names_nothing():
    """(h): GBP answers 503: the lookup ends, nothing is found, the fund is not named as
    unresolved, and the outage is counted in the run's errors."""
    sources = Sources(fetch=_GbpOutage({"currency=USD": "bnp_navs.json"}), coingecko=None)
    assert sources.discover(BNP_ISIN, BNP_NAME) is None
    assert not sources.is_unresolved(None, BNP_ISIN)
    assert ("issuer_nav:bnp", "HTTPError", 503) in sources.errors


def test_bnp_discovery_with_no_currency_answering_is_unresolved():
    """(h): none of the four currencies has rows: nothing found, the fund named."""
    sources = Sources(fetch=Recorder({"currency=": "bnp_navs_empty.json"}), coingecko=None)
    assert sources.discover(BNP_ISIN, BNP_NAME) is None
    assert sources.is_unresolved(None, BNP_ISIN)


def test_a_name_no_fund_page_matches_is_a_lookup_miss():
    """(d), (k): HANetf's sitemap lists no page the fund's name starts: no page is read, and
    the run's log line counts one lookup miss, no error."""
    fetch = Recorder({"fund-sitemap.xml": "hanetf_sitemap.xml"})
    sources = Sources(fetch=fetch, coingecko=None)
    assert sources.discover("IE000INVENT5", "HANetf Nothing Matches Here UCITS ETF") is None
    assert dict(sources.misses) == {("issuer_nav:hanetf", "no_candidate", None): 1}
    assert not sources.errors and len(fetch.calls) == 1
