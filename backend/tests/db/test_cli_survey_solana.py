"""`hausbuch survey-solana --workspace` (services/chain/survey.py): the RPC address typed in
the app is read in a read-only transaction and used, never printed; the classifier knows the
workspace's own addresses; a typed address without HAUSBUCH_SESSION_KEY stops the command
rather than falling back. Every key and address is invented."""

import pytest
from db.test_cli_evals_report import _owner_url
from db.test_wallet_sync_core import SETTINGS, SOL_B, wallet  # noqa: F401  (a fixture)
from pydantic import SecretStr
from solana_chain_fake import FakeChain, Records

from hausbuch import cli
from hausbuch.schemas.app_secrets import AppSecretName as N
from hausbuch.services import app_secrets
from hausbuch.services.chain import survey

pytestmark = pytest.mark.db

A = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"  # invented, not one of the wallet's
TYPED_URL = "https://solana.rpc.example/typed-path-with-k3y"


@pytest.fixture
def surveyed(monkeypatch, tmp_path):
    r = Records(A)
    chain = FakeChain([r.sol_in(1), r.sol_out(2)])
    monkeypatch.setattr(survey, "default_post", lambda: chain)
    seen = {}
    original = survey.run_survey

    def spy(address, **kw):
        seen["ctx"] = kw["ctx"]
        return original(address, **kw)

    monkeypatch.setattr(survey, "run_survey", spy)
    address_file = tmp_path / "address.txt"
    address_file.write_text(A)
    return chain, seen, address_file


def _argv(ws, address_file, tmp_path):
    return [
        "survey-solana",
        "--address-file",
        str(address_file),
        "--out-dir",
        str(tmp_path / "out"),
        "--workspace",
        str(ws),
        "--rps",
        "1000",
    ]


def test_the_typed_url_and_the_workspace_context(
    dbname,
    run,
    wallet,  # noqa: F811
    surveyed,
    tmp_path,
    capsys,
):
    ws, *_ = wallet
    chain, seen, address_file = surveyed
    run(ws, app_secrets.set_secret, N.SOLANA_RPC_URL, SecretStr(TYPED_URL), settings=SETTINGS)
    environ = {
        "HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname),
        "HAUSBUCH_SESSION_KEY": SETTINGS.session_key.hex(),
        "HAUSBUCH_IBAN_HMAC_KEY": SETTINGS.iban_hmac_key.hex(),
    }
    assert cli.main(_argv(ws, address_file, tmp_path), environ) == cli.EXIT_OK
    assert set(chain.urls) == {TYPED_URL}
    assert {A, SOL_B} <= seen["ctx"].own_addresses
    captured = capsys.readouterr()
    assert "the address typed in the app" in captured.err
    for secret in (A, TYPED_URL, "k3y"):
        assert secret not in captured.out + captured.err


def test_a_typed_url_without_the_session_key_stops(
    dbname,
    run,
    wallet,  # noqa: F811
    surveyed,
    tmp_path,
    capsys,
):
    ws, *_ = wallet
    chain, _, address_file = surveyed
    run(ws, app_secrets.set_secret, N.SOLANA_RPC_URL, SecretStr(TYPED_URL), settings=SETTINGS)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    assert cli.main(_argv(ws, address_file, tmp_path), environ) == cli.EXIT_ERROR
    assert "HAUSBUCH_SESSION_KEY" in capsys.readouterr().err
    assert chain.requests == []  # never the public endpoint instead
