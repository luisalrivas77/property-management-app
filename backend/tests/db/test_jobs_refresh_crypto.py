"""The refresh_crypto job (docs/phases/phase-10.md §7.7, D291) on the database, with mocked
HTTP: a failed wallet read never stops the prices, each price is stored as `spot` with its
moment and pair, the CoinGecko key stays in its header, the write moves the data version
(§7.4), the log carries counts only, and each finished run leaves its counts in one
workspace.crypto_refreshed event (the addresses not read, the coins without a current price).
Invented coin and amounts only."""

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from db.test_services_prices_fetch import _asset, _held, _setup
from pydantic import SecretStr
from sqlalchemy import select

from hausbuch.api.kept_answers import read_data_version
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import price as price_table
from hausbuch.jobs import refresh_crypto as job
from hausbuch.jobs.document_read import JobDeps
from hausbuch.schemas.chain import Chain, WalletSyncStatus
from hausbuch.services.chain.sync import SyncOutcome
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


def _runs(read, ws) -> list[dict]:
    """The workspace.crypto_refreshed payloads, oldest first."""

    def query(conn):
        e = event_table.c
        return conn.execute(
            select(e.payload).where(e.type == "workspace.crypto_refreshed").order_by(e.seq)
        ).scalars()

    return list(read(ws, lambda conn: list(query(conn))))


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
        result = job.run_refresh_crypto(
            _deps(app_engine, blob_store), workspace_id=ws, fetch=fake, job_id=41
        )

    assert (result.wallets_aborted, result.wallets_failed) == (True, 0)
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
    assert done.fields["stored"] == 1 and done.fields["wallets_aborted"] is True
    assert _runs(read, ws) == [
        {
            "job_id": 41,
            "coins": 1,
            "stored": 1,
            "unpriced": 0,
            "addresses": 0,
            "wallets_failed": 0,
            "wallets_aborted": True,
        }
    ]
    assert _runs(read, other) == []


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
    # No address: nothing to read, nothing failed.
    assert (first.wallets_failed, first.wallets_aborted) == (0, False)
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


def test_the_run_counts_addresses_not_read_and_coins_without_a_current_price(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch, caplog
):
    ws, _ = fresh_workspaces
    _setup(run, ws)
    outcomes = [
        SyncOutcome(uuid4(), Chain.SOLANA, uuid4(), WalletSyncStatus.SUCCEEDED),
        SyncOutcome(uuid4(), Chain.SOLANA, uuid4(), WalletSyncStatus.FAILED),
        SyncOutcome(uuid4(), Chain.BITCOIN, uuid4(), WalletSyncStatus.THROTTLED),
        SyncOutcome(uuid4(), Chain.BITCOIN, None, None, error_code="wallet.key_missing"),
    ]
    monkeypatch.setattr(job, "sync_wallets", lambda *a, **k: outcomes)

    def broken_relink(*args, **kwargs):
        raise RuntimeError("relink")

    monkeypatch.setattr(job, "relink_after_sync", broken_relink)
    fake = Fake({"Ticker?pair=INVEUR": (503, b"")})  # and no CoinGecko key: no price
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.refresh_crypto"):
        result = job.run_refresh_crypto(
            _deps(app_engine, blob_store), workspace_id=ws, fetch=fake, job_id=42
        )

    # A relink that fails after the reads is logged; it is not a failed read.
    assert (result.addresses, result.wallets_failed, result.wallets_aborted) == (4, 3, False)
    assert any(r.fields.get("event") == "crypto_refresh_relink_failed" for r in caplog.records)
    assert (result.prices.coins, result.prices.stored, result.prices.unpriced) == (1, 0, 1)
    assert _spot_rows(read, ws) == []
    assert _runs(read, ws) == [
        {
            "job_id": 42,
            "coins": 1,
            "stored": 0,
            "unpriced": 1,
            "addresses": 4,
            "wallets_failed": 3,
            "wallets_aborted": False,
        }
    ]


def test_a_failed_price_step_records_no_run(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch
):
    ws, _ = fresh_workspaces
    _setup(run, ws)
    monkeypatch.setattr(job, "sync_wallets", lambda *a, **k: [])

    def broken(*args, **kwargs):
        raise RuntimeError("prices")

    monkeypatch.setattr(job, "refresh_prices", broken)
    with pytest.raises(RuntimeError):
        job.run_refresh_crypto(_deps(app_engine, blob_store), workspace_id=ws, job_id=43)
    assert _runs(read, ws) == []


def test_a_failed_event_write_never_fails_a_run_whose_prices_are_stored(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch, caplog
):
    ws, _ = fresh_workspaces
    _setup(run, ws)
    monkeypatch.setattr(job, "sync_wallets", lambda *a, **k: [])

    def broken(*args, **kwargs):
        raise RuntimeError("event")

    monkeypatch.setattr(job, "record_run", broken)
    fake = Fake({"Ticker?pair=INVEUR": (200, TICKER)})
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.refresh_crypto"):
        result = job.run_refresh_crypto(
            _deps(app_engine, blob_store), workspace_id=ws, fetch=fake, job_id=44
        )
    assert result.prices.stored == 1 and len(_spot_rows(read, ws)) == 1
    assert any(r.fields.get("event") == "crypto_refresh_record_failed" for r in caplog.records)
    assert _runs(read, ws) == []


def test_the_prices_are_stored_before_any_wallet_is_read(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch
):
    """A press values the coins within seconds (23 Nov 2026: 43 s when the prices came last):
    the wallets are read only once the held coins' prices are stored."""
    ws, _ = fresh_workspaces
    held = _setup(run, ws)
    seen: list = []

    def wallets(*args, **kwargs):
        seen.append([r.asset_id for r in _spot_rows(read, ws)])
        return [SyncOutcome(uuid4(), Chain.SOLANA, uuid4(), WalletSyncStatus.SUCCEEDED)]

    monkeypatch.setattr(job, "sync_wallets", wallets)
    fake = Fake({"Ticker?pair=INVEUR": (200, TICKER)})
    result = job.run_refresh_crypto(
        _deps(app_engine, blob_store), workspace_id=ws, fetch=fake, job_id=45
    )
    assert seen == [[held["coin"]]]  # one wallet step, after the price was stored
    # No coin held since: the second read asks no source.
    assert [url for url, _ in fake.calls] == ["https://api.kraken.com/0/public/Ticker?pair=INVEUR"]
    assert (result.addresses, result.prices.coins, result.prices.stored) == (1, 1, 1)
    [recorded] = _runs(read, ws)
    assert (recorded["job_id"], recorded["coins"], recorded["stored"]) == (45, 1, 1)


def test_a_coin_first_held_after_the_wallet_read_is_priced_too(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch
):
    ws, _ = fresh_workspaces
    held = _setup(run, ws)
    new: dict = {}

    def wallets(*args, **kwargs):  # the read books a coin the household did not hold before
        def seed(ctx):
            m = movement_table.c
            account_id = ctx.conn.execute(
                select(m.account_id).where(m.asset_id == held["coin"]).limit(1)
            ).scalar_one()
            new["coin"] = _asset(
                ctx, kind="crypto", symbol="NEWINV", name="New Coin", kraken_pair="NEWINVEUR"
            )
            _held(ctx, account_id, new["coin"])

        run(ws, seed, now=NOW)
        return [SyncOutcome(uuid4(), Chain.SOLANA, uuid4(), WalletSyncStatus.SUCCEEDED)]

    monkeypatch.setattr(job, "sync_wallets", wallets)
    new_ticker = json.dumps({"error": [], "result": {"NEWINVEUR": {"c": ["2.5", "1"]}}}).encode()
    fake = Fake({"pair=INVEUR": (200, TICKER), "pair=NEWINVEUR": (200, new_ticker)})
    result = job.run_refresh_crypto(
        _deps(app_engine, blob_store), workspace_id=ws, fetch=fake, job_id=46
    )
    # The old coin once, before the read; the new one once, after it.
    assert [url for url, _ in fake.calls] == [
        "https://api.kraken.com/0/public/Ticker?pair=INVEUR",
        "https://api.kraken.com/0/public/Ticker?pair=NEWINVEUR",
    ]
    assert {(r.asset_id, r.price_eur) for r in _spot_rows(read, ws)} == {
        (held["coin"], Decimal("120.50000000")),
        (new["coin"], Decimal("2.50000000")),
    }
    p = result.prices
    assert (p.coins, p.kraken, p.stored, p.unpriced) == (2, 2, 2, 0)
    [recorded] = _runs(read, ws)
    assert (recorded["coins"], recorded["stored"], recorded["unpriced"]) == (2, 2, 0)


def test_a_failed_first_price_read_still_reads_the_wallets_then_fails_the_run(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch, caplog
):
    ws, _ = fresh_workspaces
    _setup(run, ws)
    calls: list[str] = []

    def wallets(*args, **kwargs):
        calls.append("wallets")
        return []

    def broken(*args, **kwargs):
        calls.append("prices")
        raise RuntimeError("prices")

    monkeypatch.setattr(job, "sync_wallets", wallets)
    monkeypatch.setattr(job, "refresh_prices", broken)
    with (
        caplog.at_level(logging.INFO, logger="hausbuch.jobs.refresh_crypto"),
        pytest.raises(RuntimeError),
    ):
        job.run_refresh_crypto(_deps(app_engine, blob_store), workspace_id=ws, job_id=47)
    assert calls == ["prices", "wallets"]  # never asked again after the wallets
    assert _runs(read, ws) == []
    events = [r.fields.get("event") for r in caplog.records if hasattr(r, "fields")]
    assert events == ["crypto_refresh_prices_failed", "crypto_refreshed"]


def test_a_failed_read_of_new_coins_keeps_the_first_prices_and_records_the_run(
    run, read, app_engine, blob_store, fresh_workspaces, monkeypatch, caplog
):
    ws, _ = fresh_workspaces
    held = _setup(run, ws)
    real = job.refresh_prices

    def second_breaks(*args, **kwargs):
        if kwargs.get("skip"):
            raise RuntimeError("database")
        return real(*args, **kwargs)

    monkeypatch.setattr(job, "sync_wallets", lambda *a, **k: [])
    monkeypatch.setattr(job, "refresh_prices", second_breaks)
    fake = Fake({"Ticker?pair=INVEUR": (200, TICKER)})
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.refresh_crypto"):
        result = job.run_refresh_crypto(
            _deps(app_engine, blob_store), workspace_id=ws, fetch=fake, job_id=48
        )
    assert result.prices.stored == 1
    assert [r.asset_id for r in _spot_rows(read, ws)] == [held["coin"]]
    assert any(
        getattr(r, "fields", {}).get("event") == "crypto_refresh_new_prices_failed"
        for r in caplog.records
    )
    [recorded] = _runs(read, ws)
    assert (recorded["job_id"], recorded["coins"], recorded["stored"]) == (48, 1, 1)
