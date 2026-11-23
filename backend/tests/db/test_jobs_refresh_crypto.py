"""The refresh_crypto job (docs/phases/phase-10.md §7.7, D291) on the database, with mocked
HTTP: a failed wallet read never stops the prices, each price is stored as `spot` with its
moment and pair, the CoinGecko key stays in its header, the write moves the data version
(§7.4), and the log carries counts only. Invented coin and amounts only."""

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.test_services_prices_fetch import _setup
from pydantic import SecretStr
from sqlalchemy import select

from hausbuch.api.kept_answers import read_data_version
from hausbuch.db.tables import price as price_table
from hausbuch.jobs import refresh_crypto as job
from hausbuch.jobs.document_read import JobDeps
from hausbuch.services.prices import overview
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 23, 13, 32, tzinfo=UTC)
TODAY = date(2026, 11, 23)
KEY = "CG-invented-demo-key"
TICKER = json.dumps({"error": [], "result": {"INVEUR": {"c": ["120.5", "0.1"]}}}).encode()


class Fake:
    def __init__(self, answers: dict[str, tuple[int, bytes]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append((url, dict(headers)))
        for part, answer in self.answers.items():
            if part in url:
                return answer
        return 404, b""


def _deps(app_engine, blob_store, key: str | None = None) -> JobDeps:
    settings = Settings(
        database_url="postgresql+psycopg://unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
        coingecko_key=SecretStr(key) if key else None,
    )
    return JobDeps(
        engine=app_engine,
        settings=settings,
        blob_store=blob_store,
        model_client=None,
        clock=lambda: NOW,
    )


def _spot_rows(read, ws):
    def query(conn):
        p = price_table.c
        return conn.execute(
            select(p.asset_id, p.on, p.price_eur, p.fetched_at, p.raw).where(p.source == "spot")
        ).all()

    return read(ws, query)


def test_a_failed_wallet_read_does_not_stop_the_prices(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch, caplog
):
    ws, other = fresh_workspaces
    held = _setup(run, ws)

    def broken(*args, **kwargs):
        raise RuntimeError("an address that says something private")

    monkeypatch.setattr(job, "sync_wallets", broken)
    before = read_data_version(app_engine, ws)
    fake = Fake({"Ticker?pair=INVEUR": (200, TICKER)})
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.refresh_crypto"):
        result = job.run_refresh_crypto(_deps(app_engine, blob_store), workspace_id=ws, fetch=fake)

    assert result.wallets_failed is True
    assert (result.prices.coins, result.prices.kraken, result.prices.stored) == (1, 1, 1)
    [row] = _spot_rows(read, ws)
    assert row.asset_id == held["coin"] and row.on == TODAY
    assert row.price_eur == Decimal("120.50000000") and row.fetched_at == NOW
    assert row.raw == {"pair": "INVEUR", "last": "120.50000000", "read_at": NOW.isoformat()}
    # Only the coin was asked: funds keep their daily NAV.
    assert [url for url, _ in fake.calls] == ["https://api.kraken.com/0/public/Ticker?pair=INVEUR"]
    assert read_data_version(app_engine, ws) != before
    assert _spot_rows(read, other) == []

    # The live read of today values the coin by it and names its moment; any other is not.
    live = read(ws, overview.assets_overview, TODAY, today=TODAY)
    [coin] = [p for acc in live.accounts for p in acc.positions if p.asset_id == held["coin"]]
    assert coin.price is not None and coin.price.read_at == NOW
    plain = read(ws, overview.assets_overview, TODAY)
    [coin] = [p for acc in plain.accounts for p in acc.positions if p.asset_id == held["coin"]]
    assert coin.price is None or coin.price.read_at is None

    text = caplog.text
    assert "private" not in text and "120.5" not in text and "INVEUR" not in text
    assert any(r.fields.get("event") == "crypto_refresh_wallets_failed" for r in caplog.records)
    [done] = [r for r in caplog.records if r.fields.get("event") == "crypto_refreshed"]
    assert done.fields["stored"] == 1 and done.fields["wallets_failed"] is True


def test_kraken_down_coingecko_with_the_key_and_a_second_read_the_same_day(
    run, read, app_engine, blob_store, fresh_workspaces, caplog
):
    ws, _ = fresh_workspaces
    held = _setup(run, ws)
    fake = Fake(
        {
            "Ticker?pair=INVEUR": (503, b""),
            "simple/price": (200, b'{"invented-coin": {"eur": 99.25}}'),
        }
    )
    deps = _deps(app_engine, blob_store, key=KEY)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.refresh_crypto"):
        first = job.run_refresh_crypto(deps, workspace_id=ws, fetch=fake)
        second = job.run_refresh_crypto(deps, workspace_id=ws, fetch=fake)
    assert first.wallets_failed is False  # no address: nothing to read, nothing failed
    assert (first.prices.coingecko, second.prices.coingecko) == (1, 1)
    rows = _spot_rows(read, ws)
    assert len(rows) == 2  # several a day (migration 0053)
    assert {(r.asset_id, r.price_eur, r.raw["pair"]) for r in rows} == {
        (held["coin"], Decimal("99.25000000"), "invented-coin")
    }
    assert all(KEY not in url for url, _ in fake.calls)
    assert KEY not in caplog.text
    [errors, _] = [r.fields["errors"] for r in caplog.records if "errors" in r.fields]
    assert errors == {"kraken_ticker:HTTPError:503": 1}
