"""A chain key or URL typed in the app reaches the source (docs/phases/phase-6.md D260): the
sync's run start builds its source from the workspace's typed value, a typed value wins over
the container's, and one that no longer decrypts ends the run as wallet.key_unreadable with
no row, never with the container's value or the public default. The fake Solana source of
test_wallet_sync_core records the settings its factory was handed. Invented values only."""

import dataclasses

import pytest
from db.test_wallet_sync_core import (  # noqa: F401  (world, wallet and sync are fixtures)
    ACTOR,
    SETTINGS,
    T0,
    FakeSource,
    _rows,
    sync,
    wallet,
    world,
)
from pydantic import SecretStr

from hausbuch.rules.errors import WALLET_KEY_UNREADABLE
from hausbuch.schemas.app_secrets import AppSecretName as N
from hausbuch.schemas.chain import Chain, ChainSourceKind
from hausbuch.services import app_secrets
from hausbuch.services.chain.base import register_source, source_for
from hausbuch.services.chain.sync import sync_address

pytestmark = pytest.mark.db

TYPED_URL = "https://solana.rpc.example/typed-path-with-key"
CONTAINER_URL = "https://container.rpc.example/other-key"
OTHER_KEY = bytes(range(101, 133))


@pytest.fixture
def seen(world):  # noqa: F811
    """The Solana URL each factory call was handed."""
    urls: list[str] = []

    def factory(row, settings, budget):
        urls.append(settings.solana_rpc_url.get_secret_value())
        return FakeSource(world, budget)

    register_source(ChainSourceKind.SOLANA_RPC, factory)
    yield urls
    register_source(ChainSourceKind.SOLANA_RPC, None)


def test_the_run_reads_the_typed_url_over_the_containers(run, wallet, sync, seen):  # noqa: F811
    ws, _, a, _ = wallet
    sync(ws, a)
    assert seen == [SETTINGS.solana_rpc_url.get_secret_value()]  # the public default
    run(ws, app_secrets.set_secret, N.SOLANA_RPC_URL, SecretStr(TYPED_URL), settings=SETTINGS)
    sync(ws, a)
    assert seen[-1] == TYPED_URL


def test_a_typed_url_wins_over_the_container_variable(read, wallet, run, seen):  # noqa: F811
    ws, *_ = wallet
    container = dataclasses.replace(SETTINGS, solana_rpc_url=SecretStr(CONTAINER_URL))
    source_for(Chain.SOLANA, container)  # no workspace: the container's
    read(ws, lambda conn: source_for(Chain.SOLANA, container, conn=conn))
    assert seen == [CONTAINER_URL, CONTAINER_URL]
    run(ws, app_secrets.set_secret, N.SOLANA_RPC_URL, SecretStr(TYPED_URL), settings=container)
    read(ws, lambda conn: source_for(Chain.SOLANA, container, conn=conn))
    assert seen[-1] == TYPED_URL


def test_an_unreadable_typed_url_ends_the_run_without_a_row(
    run,
    dbname,
    wallet,  # noqa: F811
    seen,
    app_engine,
    blob_store,
):
    ws, _, a, _ = wallet
    run(ws, app_secrets.set_secret, N.SOLANA_RPC_URL, SecretStr(TYPED_URL), settings=SETTINGS)
    rotated = dataclasses.replace(SETTINGS, session_key=OTHER_KEY)
    outcome = sync_address(
        app_engine,
        ws,
        a,
        settings=rotated,
        store=blob_store,
        actor=ACTOR,
        clock=lambda: T0,
    )
    assert (outcome.sync_id, outcome.error_code) == (None, WALLET_KEY_UNREADABLE)
    assert seen == []  # the factory never ran: neither the default nor the container's URL
    assert _rows(dbname, "SELECT count(*) FROM wallet_sync WHERE address_id = %s", a)[0][0] == 0
