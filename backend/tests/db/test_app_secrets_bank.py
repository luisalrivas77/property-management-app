"""The FinTS product id typed in the app (docs/phases/phase-6.md D260): a container without
one syncs with the workspace's typed id, a typed id wins over the container's, the daily
schedule queues a workspace that has only a typed id, and a typed id that no longer decrypts
is bank.not_configured, never the container's number. Over the fake FinTS client of
test_jobs_bank_sync (invented values only)."""

import dataclasses

import pytest
from db.test_jobs_bank_sync import (  # noqa: F401  (bank is a fixture)
    PIN,
    PRODUCT_ID,
    _good,
    _settings,
    bank,
)
from pydantic import SecretStr

from hausbuch.db.tables import bank_sync as sync_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.app_secrets import AppSecretName as N
from hausbuch.services import app_secrets

pytestmark = pytest.mark.db

TYPED = "TYPEDPRODUCTID0042"
OTHER_KEY = bytes(range(101, 133))


def _type(bank, value=TYPED):  # noqa: F811
    bank.run(
        bank.ws,
        app_secrets.set_secret,
        N.FINTS_PRODUCT_ID,
        SecretStr(value),
        settings=bank.settings,
    )


def test_a_typed_product_id_alone_syncs(bank, tmp_path):  # noqa: F811
    bank.settings = _settings(tmp_path, fints_product_id=None)
    with pytest.raises(RuleError) as refused:
        bank.start()
    assert refused.value.code == E.BANK_NOT_CONFIGURED
    _type(bank)
    sync = bank.start()
    assert bank.job(sync.id, _good()) == "done"
    client = bank.clients[0]
    assert client.init["pin"] == PIN and client.init["product_id"] == TYPED


def test_a_typed_product_id_wins_over_the_containers(bank):  # noqa: F811
    _type(bank)
    sync = bank.start()
    assert bank.job(sync.id, _good()) == "done"
    assert bank.clients[0].init["product_id"] == TYPED != PRODUCT_ID


def test_deleting_it_falls_back_to_the_containers(bank):  # noqa: F811
    _type(bank)
    assert bank.run(bank.ws, app_secrets.delete_secret, N.FINTS_PRODUCT_ID) is True
    sync = bank.start()
    assert bank.job(sync.id, _good()) == "done"
    assert bank.clients[0].init["product_id"] == PRODUCT_ID


def test_the_schedule_queues_a_workspace_with_only_a_typed_id(bank, tmp_path):  # noqa: F811
    bank.auto_sync()
    bare = _settings(tmp_path, fints_product_id=None)
    assert bank.schedule(settings=bare).status == "not_configured"
    assert bank.rows(sync_table) == []
    _type(bank)
    ran = bank.schedule(settings=bare)
    assert (ran.status, dict(ran.outcomes)) == ("done", {"queued": 1})


def test_an_unreadable_typed_id_is_not_configured_never_the_containers(bank):  # noqa: F811
    _type(bank)
    sync = bank.start()
    bank.settings = dataclasses.replace(bank.settings, session_key=OTHER_KEY)
    assert bank.job(sync.id, _good()) == "skipped"
    assert bank.sync(sync.id).error_code == E.BANK_NOT_CONFIGURED
    assert bank.clients == []  # no dialog with the container's number
    with pytest.raises(RuleError) as refused:
        bank.start()
    assert refused.value.code == E.BANK_NOT_CONFIGURED
