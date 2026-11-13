"""services/prices without a network or a database (docs/phases/phase-6.md D167 (3), D174):
the issuer adapters replayed on the recorded files of issuer_nav.yaml's parse_cases, the ECB,
CoinGecko and Kraken readers on their recorded answers, the one GET's failures, the job's
registration at 07:00, and that the CoinGecko key never leaves its header."""

import http.server
import io
import logging
import re
import threading
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import fixture_models_phase6 as phase6
import procrastinate
import pytest
from procrastinate import testing
from pydantic import SecretStr

from hausbuch.jobs import fetch_prices as job
from hausbuch.jobs import tasks
from hausbuch.jobs.document_read import JobDeps
from hausbuch.services.prices import coingecko, ecb_fx, kraken
from hausbuch.services.prices.http import (
    MAX_BYTES,
    ResponseTooLarge,
    SourceError,
    get,
    urllib_fetch,
)
from hausbuch.services.prices.issuer_nav import ADAPTERS, ISharesAdapter, SpdrAdapter, read_points
from hausbuch.services.prices.issuer_nav.ishares import _number as ishares_number
from hausbuch.settings import Settings, load_settings

SAMPLES = phase6.SAMPLES
ISSUER_NAV = phase6.load("issuer_nav")
KEY = "CG-invented-demo-key-0123456789"
PRICES = Path(coingecko.__file__).parent


def _sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


class Recorder:
    """A Fetch that answers recorded bodies by URL substring and records every call."""

    def __init__(self, answers: dict[str, tuple[int, bytes] | Exception]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, str], float, int]] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append((url, dict(headers), timeout, max_bytes))
        for part, answer in self.answers.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return 404, b""


# --- issuer adapters -----------------------------------------------------------------------


@pytest.mark.parametrize("case", ISSUER_NAV.parse_cases, ids=lambda c: c.id)
def test_parse_cases(case):
    adapter = ADAPTERS[case.issuer]
    body = _sample(case.sample)
    if case.expect.error is not None:
        with pytest.raises(SourceError) as raised:
            adapter.parse(body)
        assert raised.value.kind == case.expect.error
        return
    found = adapter.parse(body)
    assert found.isin == case.expect.isin
    assert found.currency == case.expect.currency
    assert sorted(found.points, key=lambda p: p.on) == sorted(case.expect.navs, key=lambda p: p.on)


def test_every_adapter_has_a_parse_case():
    assert {c.issuer for c in ISSUER_NAV.parse_cases} == set(ADAPTERS)


def test_an_adapter_refuses_a_file_naming_another_isin():
    fetch = Recorder({"navhist-emea-en-abc-gy.xlsx": (200, _sample("spdr_navhist.xlsx"))})
    adapter = SpdrAdapter(fetch)
    assert read_points(adapter, "abc-gy", "IE000INVENT1").isin == "IE000INVENT1"
    with pytest.raises(SourceError) as raised:
        read_points(adapter, "abc-gy", "IE00B4L5YC18")
    assert raised.value.kind == "isin_mismatch"


def test_an_adapter_reads_one_url_per_fund_and_checks_the_reference():
    spdr = SpdrAdapter(Recorder({}))
    assert spdr.url("sppw-gy").endswith("/navhist-emea-en-sppw-gy.xlsx")
    ishares = ISharesAdapter(Recorder({}))
    assert "251858" in ishares.url("251858")
    for adapter, bad in ((spdr, "../x"), (spdr, "SPPW GY"), (ishares, "25185a")):
        with pytest.raises(SourceError) as raised:
            adapter.url(bad)
        assert raised.value.kind == "fund_ref"


def test_a_file_the_adapter_does_not_understand_is_a_parse_error():
    for adapter in (SpdrAdapter, ISharesAdapter):
        for body in (b"", b"<html>moved</html>", b"PK\x03\x04garbage"):
            with pytest.raises(SourceError) as raised:
                adapter.parse(body)
            assert raised.value.kind == "parse"


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("58,82", Decimal("58.82")),
        ("1.002,50", Decimal("1002.50")),
        ("1002,50", Decimal("1002.50")),
        ("81", Decimal("81")),
        ("58.82", None),  # an English decimal point: never read as 5882
        ("1.002", None),  # a dot without a comma could be either
        ("1.02,50", None),
        ("-", None),
        ("", None),
    ],
)
def test_ishares_reads_german_numbers_only(cell, expected):
    assert ishares_number(cell) == expected


def test_an_ishares_file_with_english_numbers_is_a_parse_error():
    def english(match: re.Match[bytes]) -> bytes:
        return b">" + match.group(1).replace(b".", b"") + b"." + match.group(2) + b"<"

    body = re.sub(rb">([\d.]+),(\d+)<", english, _sample("ishares_fund_download.xls"))
    assert b">81.20<" in body and b">1002.50<" in body
    with pytest.raises(SourceError) as raised:
        ISharesAdapter.parse(body)
    assert raised.value.kind == "parse"


def test_spdr_refuses_a_zip_that_unpacks_too_large():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", b"0" * (41 * 1024 * 1024))
    with pytest.raises(SourceError) as raised:
        SpdrAdapter.parse(buffer.getvalue())
    assert raised.value.kind == "parse"


def test_an_adapter_answer_over_its_cap_is_refused():
    def too_large(url, headers, timeout, max_bytes):
        raise ResponseTooLarge

    with pytest.raises(SourceError) as raised:
        SpdrAdapter(too_large).read("sppw-gy")
    assert raised.value.kind == "ResponseTooLarge"


# --- the one GET ----------------------------------------------------------------------------


def test_get_returns_a_200_body_and_sends_the_user_agent():
    fetch = Recorder({"x": (200, b"ok")})
    assert get(fetch, "https://example.org/x") == b"ok"
    [(url, headers, timeout, max_bytes)] = fetch.calls
    assert headers["User-Agent"].startswith("Hausbuch/")
    assert (timeout, max_bytes) == (20.0, MAX_BYTES)


def test_get_reports_status_and_class_only():
    with pytest.raises(SourceError) as raised:
        get(Recorder({"x": (503, b"secret body")}), "https://example.org/x")
    assert (raised.value.kind, raised.value.status, str(raised.value)) == (
        "HTTPError",
        503,
        "HTTPError 503",
    )
    with pytest.raises(SourceError) as raised:
        get(Recorder({"x": TimeoutError("https://example.org/x?key=1")}), "https://example.org/x")
    assert (raised.value.kind, raised.value.status, str(raised.value)) == (
        "TimeoutError",
        None,
        "TimeoutError",
    )
    assert raised.value.__cause__ is None


def test_a_redirect_is_a_failed_status_and_is_never_followed():
    """The CoinGecko key travels in a header: a 3xx must not carry it to another URL."""
    seen: list[tuple[str, str | None]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the stdlib's name
            seen.append((self.path, self.headers.get("x-cg-demo-api-key")))
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"followed")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/start"
        with pytest.raises(SourceError) as raised:
            get(urllib_fetch, url, {"x-cg-demo-api-key": KEY})
    finally:
        server.shutdown()
        server.server_close()
    assert (raised.value.kind, raised.value.status) == ("HTTPError", 302)
    assert seen == [("/start", KEY)]  # one request, none to the redirect's target


# --- ECB --------------------------------------------------------------------------------------


def test_ecb_daily_reads_the_three_currencies_and_ignores_the_rest():
    rates = ecb_fx.parse(_sample("ecb_daily.xml"))
    assert set(rates) == {"USD", "GBP", "CHF"}
    assert [(r.on, r.per_eur) for r in rates["USD"]] == [(date(2026, 6, 26), Decimal("1.0850"))]
    assert rates["GBP"][0].per_eur == Decimal("0.84500")


def test_ecb_history_is_sorted_oldest_first():
    rates = ecb_fx.parse(_sample("ecb_hist_90d.xml"))
    assert [r.on for r in rates["USD"]] == [date(2026, 6, 24), date(2026, 6, 25), date(2026, 6, 26)]
    assert rates["USD"][0].per_eur == Decimal("1.0800")


def test_ecb_fetch_picks_the_file_by_backfill():
    fetch = Recorder(
        {
            "eurofxref-daily.xml": (200, _sample("ecb_daily.xml")),
            "eurofxref-hist-90d.xml": (200, _sample("ecb_hist_90d.xml")),
        }
    )
    assert len(ecb_fx.fetch(fetch, backfill=False)["USD"]) == 1
    assert len(ecb_fx.fetch(fetch, backfill=True)["USD"]) == 3
    assert [c[0] for c in fetch.calls] == [ecb_fx.DAILY_URL, ecb_fx.HIST_90D_URL]


@pytest.mark.parametrize(
    "body",
    [
        b"not xml",
        b"<a/>",
        _sample("ecb_daily.xml").replace(b"rate='1.0850'", b"rate='x'"),
        _sample("ecb_daily.xml").replace(b"rate='1.0850'", b"rate='0'"),
        _sample("ecb_daily.xml").replace(b"2026-06-26", b"26.06.2026"),
    ],
)
def test_ecb_refuses_another_file(body):
    with pytest.raises(SourceError) as raised:
        ecb_fx.parse(body)
    assert raised.value.kind == "parse"


# --- CoinGecko ---------------------------------------------------------------------------------


def test_coingecko_reads_eur_to_eight_places_and_nothing_before_listing():
    assert coingecko.parse(_sample("coingecko_history.json")) == Decimal("93.30142712")
    assert coingecko.parse(_sample("coingecko_history_before_listing.json")) is None
    with pytest.raises(SourceError):
        coingecko.parse(b"<html>")


def test_coingecko_key_travels_in_its_header_only_and_calls_are_spaced():
    fetch = Recorder({"/coins/invented-coin/history": (200, _sample("coingecko_history.json"))})
    slept: list[float] = []
    client = coingecko.CoinGeckoDaily(key=SecretStr(KEY), fetch=fetch, sleep=slept.append)
    days = [date(2026, 6, 26), date(2026, 6, 25)]
    prices, error = client.daily("invented-coin", days)
    assert error is None and set(prices) == set(days)
    assert slept == [coingecko.MIN_INTERVAL_S]
    for url, headers, _, _ in fetch.calls:
        assert headers[coingecko.KEY_HEADER] == KEY
        assert KEY not in url
    assert fetch.calls[0][0].endswith("/history?date=26-06-2026&localization=false")
    assert KEY not in repr(client)


def test_coingecko_stops_at_the_first_failure_and_keeps_what_it_had():
    answers = iter([(200, _sample("coingecko_history.json")), (429, b""), (200, b"{}")])
    calls: list[str] = []

    def fetch(url, headers, timeout, max_bytes):
        calls.append(url)
        return next(answers)

    client = coingecko.CoinGeckoDaily(key=SecretStr(KEY), fetch=fetch, sleep=lambda s: None)
    prices, error = client.daily("invented-coin", [date(2026, 6, d) for d in (26, 25, 24)])
    assert list(prices) == [date(2026, 6, 26)]
    assert (error.kind, error.status) == ("HTTPError", 429)
    assert len(calls) == 2
    assert KEY not in str(error)


def test_coingecko_reaches_back_365_days():
    client = coingecko.CoinGeckoDaily(key=SecretStr(KEY), fetch=Recorder({}))
    assert client.oldest_day(date(2026, 11, 13)) == date(2025, 11, 14)


# --- Kraken -----------------------------------------------------------------------------------


def test_kraken_skips_the_open_candle():
    prices = kraken.parse(_sample("kraken_ohlc.json"), "INVEUR")
    assert prices == {
        date(2026, 6, 24): Decimal("90.50"),
        date(2026, 6, 25): Decimal("91.75"),
        date(2026, 6, 26): Decimal("92.40000001"),
    }


def test_kraken_reads_its_own_key_for_the_pair():
    prices = kraken.parse(_sample("kraken_ohlc.json"), "XINVZEUR")
    assert len(prices) == 3


@pytest.mark.parametrize(
    "body",
    [
        b'{"error": ["EQuery:Unknown asset pair"]}',
        b'{"error": [], "result": {"last": 1}}',
        b'{"error": [], "result": {"INVEUR": [[1, "a"], [2, "b"]]}}',
        b"[]",
        b"<html>",
    ],
)
def test_kraken_refuses_another_answer(body):
    with pytest.raises(SourceError) as raised:
        kraken.parse(body, "INVEUR")
    assert raised.value.kind == "parse"


def test_kraken_asks_the_daily_interval_without_a_key():
    fetch = Recorder({"OHLC?pair=INVEUR&interval=1440": (200, _sample("kraken_ohlc.json"))})
    assert len(kraken.daily(fetch, "INVEUR")) == 3
    [(_, headers, _, _)] = fetch.calls
    assert coingecko.KEY_HEADER not in headers


# --- settings and the job ---------------------------------------------------------------------

ENV = {
    "HAUSBUCH_DATABASE_URL": "postgresql+psycopg://hausbuch_app@localhost/hausbuch",
    "HAUSBUCH_IBAN_HMAC_KEY": "0f" * 32,
    "HAUSBUCH_SESSION_KEY": "1e" * 32,
    "HAUSBUCH_RP_ID": "hausbuch.example",
    "HAUSBUCH_ORIGIN": "https://hausbuch.example",
    "HAUSBUCH_BLOB_STORE": "s3",
    "HAUSBUCH_S3_BUCKET": "hausbuch-originals",
}


def test_the_coingecko_key_is_a_secret_setting():
    assert load_settings(ENV).coingecko_key is None
    settings = load_settings(ENV | {"HAUSBUCH_COINGECKO_KEY": KEY})
    assert settings.coingecko_key.get_secret_value() == KEY
    assert KEY not in repr(settings)


def test_the_task_is_registered_at_seven_with_a_lock():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[job.FETCH_PRICES_TASK]
    assert task.lock == job.FETCH_PRICES_LOCK
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
    assert [p.cron for p in periodic] == [job.FETCH_PRICES_CRON] == ["0 7 * * *"]


def _settings(key: str | None) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
        coingecko_key=None if key is None else SecretStr(key),
    )


def test_coingecko_runs_only_with_the_key():
    def deps(key):
        return JobDeps(engine=None, settings=_settings(key), blob_store=None, model_client=None)

    assert job.sources_for(deps(None)).coingecko is None
    assert job.sources_for(deps(KEY)).coingecko is not None


def test_no_workspace_logs_one_line_and_reads_nothing(caplog):
    deps = JobDeps(
        engine=None,
        settings=_settings(None),
        blob_store=None,
        model_client=None,
        clock=lambda: datetime(2026, 6, 27, 7, tzinfo=UTC),
    )
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.fetch_prices"):
        result = job.run_fetch_prices(deps, fetch=Recorder({}), workspace_ids=[])
    assert (result.workspaces, result.rows_new, result.coingecko) == (0, 0, False)
    [record] = caplog.records
    assert record.fields["event"] == "prices_fetched"


@pytest.mark.parametrize(
    "module",
    sorted(p for p in PRICES.rglob("*.py")) + [Path(job.__file__)],
    ids=lambda p: p.name,
)
def test_the_sources_log_classes_and_statuses_only(module):
    source = module.read_text()
    assert "print(" not in source and "exc_info" not in source
    for call in re.findall(r"logger\.\w+\((.*?)\)\n", source, flags=re.S):
        call = re.sub(r'"[^"]*"', '""', call)  # the message text may say "prices"
        for word in ("url", "key", "str(exc)", "price", "body", "isin"):
            assert word not in call.lower(), (module.name, call)
    # the key is read in one place, for its header
    assert source.count("get_secret_value()") <= 1
