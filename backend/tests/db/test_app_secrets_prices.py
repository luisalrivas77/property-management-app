"""A CoinGecko key typed in the app (docs/phases/phase-6.md D260): the daily price run asks
CoinGecko with each workspace's own key (typed wins over the container's), workspaces sharing
a key share its answers, and a typed key that no longer decrypts prices that workspace's coins
from Kraken alone, never with the container's key. Over the recorded files of
test_services_prices_fetch; invented keys only."""

# ruff: noqa: F811  (the imported fixtures are parameters)

import dataclasses
import logging

import pytest
from db.test_services_prices_fetch import (  # noqa: F401  (funds is a fixture)
    NOW,
    Fake,
    _prices,
    _setup,
    funds,
)
from pydantic import SecretStr

from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.fetch_prices import run_fetch_prices
from hausbuch.schemas.app_secrets import AppSecretName as N
from hausbuch.services import app_secrets
from hausbuch.services.prices.coingecko import KEY_HEADER
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

CONTAINER = "CG-container-demo-key"
TYPED = "CG-typed-demo-key"
OTHER_KEY = bytes(range(101, 133))


def _deps(app_engine, **overrides) -> JobDeps:
    settings = Settings(
        database_url="postgresql+psycopg://unused",
        iban_hmac_key=bytes(range(32)),
        session_key=bytes(range(1, 33)),
        rp_id="testserver",
        origin="https://testserver",
        blob_store="local",
        blob_root="/tmp/unused",
        **overrides,
    )
    return JobDeps(
        engine=app_engine, settings=settings, blob_store=None, model_client=None, clock=lambda: NOW
    )


def _keys(fake: Fake) -> list[str]:
    return [h[KEY_HEADER] for url, h in fake.calls if "/coins/" in url]


def _type(run, ws, deps, value=TYPED):
    run(ws, app_secrets.set_secret, N.COINGECKO_KEY, SecretStr(value), settings=deps.settings)


def test_each_workspace_asks_with_its_own_key(run, read, app_engine, fresh_workspaces, funds):  # noqa: F811
    a, b = fresh_workspaces
    _setup(run, a)
    _setup(run, b)
    deps = _deps(app_engine, coingecko_key=SecretStr(CONTAINER))
    _type(run, a, deps)
    fake = Fake()
    result = run_fetch_prices(deps, fetch=fake, sleep=lambda s: None, workspace_ids=[a, b])
    assert result.coingecko is True and result.failed_workspaces == 0
    assert sorted(set(_keys(fake))) == sorted({TYPED, CONTAINER})  # one client per key
    assert fake.asked("hist-90d") == fake.asked("navhist") == 1  # the rest stays shared
    for ws in (a, b):
        assert {r.source for r in _prices(read, ws) if r.symbol == "INV"} == {"coingecko_daily"}


def test_two_workspaces_with_the_same_typed_key_share_its_answers(
    run,
    read,
    app_engine,
    fresh_workspaces,
    funds,  # noqa: F811
):
    a, b = fresh_workspaces
    _setup(run, a)
    _setup(run, b)
    deps = _deps(app_engine)  # no container key
    _type(run, a, deps)
    _type(run, b, deps)
    fake = Fake()
    alone = Fake()
    run_fetch_prices(deps, fetch=alone, sleep=lambda s: None, workspace_ids=[a])
    run_fetch_prices(deps, fetch=fake, sleep=lambda s: None, workspace_ids=[a, b])
    assert set(_keys(fake)) == {TYPED}
    assert len(_keys(fake)) == len(_keys(alone))  # b's coin days came from a's answers


def test_an_unreadable_typed_key_prices_from_kraken_never_the_containers(
    run,
    read,
    app_engine,
    fresh_workspaces,
    funds,
    caplog,  # noqa: F811
):
    a, _ = fresh_workspaces
    _setup(run, a)
    deps = _deps(app_engine, coingecko_key=SecretStr(CONTAINER))
    _type(run, a, deps)
    rotated = dataclasses.replace(
        deps, settings=dataclasses.replace(deps.settings, session_key=OTHER_KEY)
    )
    fake = Fake()
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.fetch_prices"):
        result = run_fetch_prices(rotated, fetch=fake, sleep=lambda s: None, workspace_ids=[a])
    assert _keys(fake) == [] and fake.asked("OHLC") == 1
    assert {r.source for r in _prices(read, a) if r.symbol == "INV"} == {"kraken_daily"}
    assert result.errors == {"coingecko:key_unreadable": 1}
    assert CONTAINER not in caplog.text and TYPED not in caplog.text
