"""The daily price fetch as hausbuch_app under RLS (docs/phases/phase-6.md D167 (3), D174),
with the recorded files of fixtures/phase6/samples/ behind a fake GET: the ECB rates first,
the held coin from Kraken alone or CoinGecko with the key, the held ETFs' NAV (EUR and USD),
nothing before the first movement, nothing twice, and D174's event after seven failed days.
Invented coins, ISINs and prices only."""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from pydantic import SecretStr
from sqlalchemy import insert, select

from hausbuch.db.tables import asset as asset_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import price as price_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.fetch_prices import run_fetch_prices
from hausbuch.rules.household.assets import FundEntry
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.schemas.events import EventType
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor
from hausbuch.services.prices import fetch as prices_fetch
from hausbuch.services.prices.coingecko import KEY_HEADER, CoinGeckoDaily
from hausbuch.services.prices.fetch import Sources, run_workspace
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

SAMPLES = __import__("fixture_models_phase6").SAMPLES
ACTOR = Actor(ActorType.SYSTEM, "fetch_prices")
NOW = datetime(2026, 6, 27, 7, 0, tzinfo=UTC)
FIRST = datetime(2026, 6, 24, 9, 30, tzinfo=UTC)
KEY = "CG-invented-demo-key"
EUR_FUND = FundEntry(
    isin="IE000INVENT1", name="Invented EUR", issuer="spdr", fund_ref="inv-gy", nav_currency="EUR"
)
USD_FUND = FundEntry(
    isin="IE000INVENT2", name="Invented USD", issuer="ishares", fund_ref="1", nav_currency="USD"
)


def _bytes(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


class Fake:
    """A Fetch answering recorded files by URL part; `down` parts answer 503."""

    def __init__(self, down: tuple[str, ...] = ()) -> None:
        self.down = down
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append((url, dict(headers)))
        if any(part in url for part in self.down):
            return 503, b""
        for part, name in (
            ("eurofxref-daily", "ecb_daily.xml"),
            ("eurofxref-hist-90d", "ecb_hist_90d.xml"),
            ("/coins/invented-coin/history", "coingecko_history.json"),
            ("OHLC?pair=INVEUR", "kraken_ohlc.json"),
            ("navhist-emea-en-inv-gy", "spdr_navhist.xlsx"),
            ("ishares", "ishares_fund_download.xls"),
        ):
            if part in url:
                return 200, _bytes(name)
        return 404, b""

    def asked(self, part: str) -> int:
        return sum(part in url for url, _ in self.calls)


@pytest.fixture
def funds(monkeypatch):
    by_isin = {EUR_FUND.isin: EUR_FUND, USD_FUND.isin: USD_FUND}
    monkeypatch.setattr(prices_fetch, "fund_source", lambda isin: by_isin.get(isin or ""))


def _asset(ctx, **values):
    return ctx.conn.execute(
        insert(asset_table)
        .values(workspace_id=ctx.workspace_id, created_at=ctx.now, updated_at=ctx.now, **values)
        .returning(asset_table.c.id)
    ).scalar_one()


def _held(ctx, account_id, asset_id, units="2", at=FIRST, status="confirmed"):
    ctx.conn.execute(
        insert(movement_table).values(
            workspace_id=ctx.workspace_id,
            account_id=account_id,
            asset_id=asset_id,
            kind="acquisition",
            quantity=Decimal(units),
            occurred_at=at,
            source="user",
            status=status,
            created_at=ctx.now,
        )
    )


def _setup(run, ws):
    account = run(
        ws,
        create_account,
        AccountCreate(
            label="Depot",
            bank="Beispiel",
            kind=AccountKind.DEPOT,
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 6, 1),
        ),
        iban_key=TEST_IBAN_KEY,
        now=FIRST,
    )

    def seed(ctx):
        coin = _asset(
            ctx,
            kind="crypto",
            symbol="INV",
            name="Invented Coin",
            coingecko_id="invented-coin",
            kraken_pair="INVEUR",
        )
        eur = _asset(ctx, kind="etf", isin=EUR_FUND.isin, name=EUR_FUND.name)
        usd = _asset(ctx, kind="etf", isin=USD_FUND.isin, name=USD_FUND.name)
        sold = _asset(ctx, kind="crypto", symbol="BTC", name="Bitcoin")
        for asset_id in (coin, eur, usd):
            _held(ctx, account.id, asset_id)
        _held(ctx, account.id, sold, "1")
        _held(ctx, account.id, sold, "-1")  # bought and sold: not held, not priced
        return {"coin": coin, "eur": eur, "usd": usd, "sold": sold}

    return run(ws, seed, now=FIRST)


def _prices(read, ws):
    def query(conn):
        p, a = price_table.c, asset_table.c
        return conn.execute(
            select(a.symbol, a.isin, p.asset_id, p.on, p.price_eur, p.source, p.raw)
            .join(asset_table, a.id == p.asset_id)
            .order_by(p.source, a.symbol, a.isin, p.on)
        ).all()

    return read(ws, query)


def _events(read, ws, type_):
    def query(conn):
        e = event_table.c
        return conn.execute(
            select(e.entity_id, e.payload).where(e.type == type_.value).order_by(e.seq)
        ).all()

    return read(ws, query)


def test_ecb_first_then_kraken_then_nav_none_before_the_first_movement(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, other = fresh_workspaces
    ids = _setup(run, ws)
    fake = Fake()
    new, out = run_workspace(app_engine, ws, ACTOR, Sources(fetch=fake, coingecko=None), NOW)

    # the order of D167 (3): ECB, the coin (Kraken without the key), the funds
    order = [url for url, _ in fake.calls]
    assert "eurofxref-hist-90d" in order[0]  # nothing stored yet: the 90-day file
    assert "OHLC?pair=INVEUR" in order[1]
    assert all("history" not in url for url in order)
    rows = _prices(read, ws)
    by_source: dict[str, list] = {}
    for r in rows:
        by_source.setdefault(r.source, []).append(r)

    ecb = by_source["ecb_fx"]
    assert {r.symbol for r in ecb} == {"USD", "GBP", "CHF"} and len(ecb) == 9
    usd_26 = next(r for r in ecb if r.symbol == "USD" and r.on == date(2026, 6, 26))
    assert usd_26.raw == {"per_eur": "1.0850"}
    assert usd_26.price_eur == Decimal("0.92165899")  # 1 / 1.0850, 8 places half up

    kraken = by_source["kraken_daily"]
    assert [(r.on, r.price_eur) for r in kraken] == [
        (date(2026, 6, 24), Decimal("90.50000000")),
        (date(2026, 6, 25), Decimal("91.75000000")),
        (date(2026, 6, 26), Decimal("92.40000001")),
    ]  # the 27th's candle is still open

    nav = by_source["issuer_nav"]
    eur_rows = [(r.on, r.price_eur) for r in nav if r.asset_id == ids["eur"]]
    assert eur_rows == [  # the 23rd is before the first movement
        (date(2026, 6, 24), Decimal("81.10000000")),
        (date(2026, 6, 25), Decimal("81.50000000")),
        (date(2026, 6, 26), Decimal("81.70000000")),
    ]
    usd_25, usd_row = [r for r in nav if r.asset_id == ids["usd"]]  # May is before the 24th
    assert (usd_25.on, usd_row.on) == (date(2026, 6, 25), date(2026, 6, 26))
    assert usd_25.raw["rate_on"] == "2026-06-25"
    assert usd_row.price_eur == (Decimal("81.20") / Decimal("1.0850")).quantize(
        Decimal("0.00000001")
    )
    assert usd_row.raw == {"nav": "81.20000000", "currency": "USD", "rate_on": "2026-06-26"}
    assert ids["sold"] not in {r.asset_id for r in rows}

    assert new == len(rows) == 9 + 3 + 3 + 2 and out.failed == []
    [event] = _events(read, ws, EventType.WORKSPACE_PRICES_FETCHED)
    assert event.entity_id == ws
    assert event.payload["sources"] == ["ecb_fx", "kraken_daily", "issuer_nav"]
    assert (event.payload["rows_new"], event.payload["failed_asset_ids"]) == (17, [])
    assert _prices(read, other) == []  # RLS: the other workspace saw nothing

    # A second run the same day stores nothing twice and asks the daily ECB file.
    again = Fake()
    new, _ = run_workspace(app_engine, ws, ACTOR, Sources(fetch=again, coingecko=None), NOW)
    assert new == 0 and len(_prices(read, ws)) == 17
    assert again.asked("eurofxref-daily") == 1 and again.asked("hist-90d") == 0


def test_coingecko_with_the_key_and_kraken_when_it_fails(
    run, read, app_engine, fresh_workspaces, funds
):
    ws, _ = fresh_workspaces
    ids = _setup(run, ws)
    fake = Fake()
    gecko = CoinGeckoDaily(key=SecretStr(KEY), fetch=fake, sleep=lambda s: None)
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=fake, coingecko=gecko), NOW)
    coin = [r for r in _prices(read, ws) if r.asset_id == ids["coin"]]
    assert {r.source for r in coin} == {"coingecko_daily"}
    assert [r.on for r in coin] == [date(2026, 6, d) for d in (24, 25, 26, 27)]
    assert coin[0].price_eur == Decimal("93.30142712")
    assert fake.asked("OHLC") == 0  # CoinGecko priced every day
    history = [(url, h) for url, h in fake.calls if "history" in url]
    assert history[0][0].endswith("date=27-06-2026&localization=false")  # newest first
    assert all(h[KEY_HEADER] == KEY and KEY not in url for url, h in history)
    assert all(KEY_HEADER not in h for url, h in fake.calls if "history" not in url)


def test_coingecko_down_kraken_fills_the_days(run, read, app_engine, fresh_workspaces, funds):
    ws, _ = fresh_workspaces
    ids = _setup(run, ws)
    fake = Fake(down=("/coins/",))
    gecko = CoinGeckoDaily(key=SecretStr(KEY), fetch=fake, sleep=lambda s: None)
    sources = Sources(fetch=fake, coingecko=gecko)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW)
    coin = [r for r in _prices(read, ws) if r.asset_id == ids["coin"]]
    assert {r.source for r in coin} == {"kraken_daily"} and len(coin) == 3
    assert out.failed == []  # Kraken answered: the coin did not fail
    assert fake.asked("history") == 1  # the first failure ends CoinGecko for the run
    assert dict(sources.errors) == {("coingecko_daily", "HTTPError", 503): 1}


def test_seven_failed_days_raise_the_event_once(run, read, app_engine, fresh_workspaces, funds):
    ws, _ = fresh_workspaces
    ids = _setup(run, ws)
    down = Fake(down=("navhist-emea-en-inv-gy",))
    for day in range(8):
        now = NOW + timedelta(days=day)
        run_workspace(app_engine, ws, ACTOR, Sources(fetch=down, coingecko=None), now)
        raised = _events(read, ws, EventType.ASSET_PRICE_SOURCE_FAILED)
        assert len(raised) == (0 if day < 6 else 1), day
    [card] = raised
    assert card.entity_id == ids["eur"]
    assert (card.payload["source"], card.payload["failed_days"]) == ("issuer_nav", 7)
    assert card.payload["last_price_on"] is None
    fetched = _events(read, ws, EventType.WORKSPACE_PRICES_FETCHED)
    assert all(e.payload["failed_asset_ids"] == [str(ids["eur"])] for e in fetched)
    # the iShares fund kept being priced
    assert any(r.asset_id == ids["usd"] for r in _prices(read, ws))

    # A success ends the streak; a new streak of seven raises it again.
    up = Fake()
    later = NOW + timedelta(days=8)
    run_workspace(app_engine, ws, ACTOR, Sources(fetch=up, coingecko=None), later)
    for day in range(9, 16):
        now = NOW + timedelta(days=day)
        run_workspace(app_engine, ws, ACTOR, Sources(fetch=down, coingecko=None), now)
    raised = _events(read, ws, EventType.ASSET_PRICE_SOURCE_FAILED)
    assert len(raised) == 2
    assert raised[1].payload["last_price_on"] == "2026-06-26"


def test_a_file_naming_another_isin_stores_nothing(
    run, read, app_engine, fresh_workspaces, monkeypatch
):
    ws, _ = fresh_workspaces
    ids = _setup(run, ws)
    wrong = EUR_FUND.model_copy(update={"isin": "IE00B4L5YC18"})
    monkeypatch.setattr(
        prices_fetch, "fund_source", lambda isin: wrong if isin == EUR_FUND.isin else None
    )
    sources = Sources(fetch=Fake(), coingecko=None)
    _, out = run_workspace(app_engine, ws, ACTOR, sources, NOW)
    assert ids["eur"] in out.failed
    assert all(r.asset_id != ids["eur"] for r in _prices(read, ws))
    assert dict(sources.errors) == {("issuer_nav:spdr", "isin_mismatch", None): 1}


def test_nothing_held_asks_nothing_and_writes_no_event(read, app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    fake = Fake()
    new, out = run_workspace(app_engine, ws, ACTOR, Sources(fetch=fake, coingecko=None), NOW)
    assert (new, fake.calls, out.sources) == (0, [], set())
    assert _events(read, ws, EventType.WORKSPACE_PRICES_FETCHED) == []


def test_one_answer_per_source_across_workspaces(run, read, app_engine, fresh_workspaces, funds):
    a, b = fresh_workspaces
    _setup(run, a)
    _setup(run, b)
    fake = Fake()
    sources = Sources(fetch=fake, coingecko=None)
    run_workspace(app_engine, a, ACTOR, sources, NOW)
    run_workspace(app_engine, b, ACTOR, sources, NOW)
    assert fake.asked("hist-90d") == fake.asked("OHLC") == fake.asked("navhist") == 1
    assert len(_prices(read, a)) == len(_prices(read, b)) == 17


def test_the_job_runs_every_workspace_and_logs_counts_only(
    run, app_engine, fresh_workspaces, funds, caplog
):
    a, b = fresh_workspaces
    _setup(run, a)
    settings = Settings(
        database_url="postgresql+psycopg://unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
        coingecko_key=SecretStr(KEY),
    )
    deps = JobDeps(
        engine=app_engine, settings=settings, blob_store=None, model_client=None, clock=lambda: NOW
    )
    fake = Fake(down=("/coins/", "navhist"))
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.fetch_prices"):
        result = run_fetch_prices(deps, fetch=fake, sleep=lambda s: None, workspace_ids=[a, b])
    assert (result.workspaces, result.failed_workspaces, result.coingecko) == (1, 0, True)
    assert result.assets_failed == 1 and result.rows_new == 9 + 3 + 2
    assert result.errors == {
        "coingecko_daily:HTTPError:503": 1,
        "issuer_nav:spdr:HTTPError:503": 1,
    }
    [record] = caplog.records
    assert record.levelno == logging.WARNING and record.fields["errors"] == result.errors
    assert KEY not in caplog.text and "invented-coin" not in caplog.text
