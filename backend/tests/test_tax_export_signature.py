"""The signed download URL of a tax export (services/tax_export.py): valid for its workspace,
export and expiry only, and only within DOWNLOAD_TTL_S. Also: the worker knows the job."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import procrastinate

from hausbuch.jobs import tasks
from hausbuch.jobs.enqueue import TAX_EXPORT_TASK, export_lock
from hausbuch.services.tax_export import DOWNLOAD_TTL_S, download_url, signature_valid

KEY = b"k" * 32
WS = UUID(int=1)
OTHER_WS = UUID(int=2)
EXPORT = UUID(int=3)
NOW = datetime(2027, 1, 15, 9, 30, tzinfo=UTC)


def _params(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return parts.path, int(query["expires"][0]), query["signature"][0]


def test_url_names_the_export_and_verifies():
    path, expires, signature = _params(download_url(KEY, WS, EXPORT, now=NOW))
    assert path == f"/api/tax-exports/{EXPORT}/file"
    assert expires == int(NOW.timestamp()) + DOWNLOAD_TTL_S
    assert signature_valid(KEY, WS, EXPORT, expires=expires, signature=signature, now=NOW)


def test_signature_is_bound_to_workspace_export_expiry_and_key():
    _, expires, signature = _params(download_url(KEY, WS, EXPORT, now=NOW))
    check = dict(expires=expires, signature=signature, now=NOW)
    assert not signature_valid(KEY, OTHER_WS, EXPORT, **check)
    assert not signature_valid(KEY, WS, UUID(int=4), **check)
    assert not signature_valid(b"x" * 32, WS, EXPORT, **check)
    assert not signature_valid(KEY, WS, EXPORT, expires=expires + 1, signature=signature, now=NOW)


def test_signature_expires():
    _, expires, signature = _params(download_url(KEY, WS, EXPORT, now=NOW))
    later = NOW + timedelta(seconds=DOWNLOAD_TTL_S + 1)
    assert not signature_valid(KEY, WS, EXPORT, expires=expires, signature=signature, now=later)
    # An expiry further out than one TTL was not issued by download_url.
    far = int(NOW.timestamp()) + 10 * DOWNLOAD_TTL_S
    assert not signature_valid(KEY, WS, EXPORT, expires=far, signature=signature, now=NOW)


def test_the_worker_registers_the_export_job():
    app = tasks.register_tasks(procrastinate.App(connector=procrastinate.SyncPsycopgConnector()))
    assert TAX_EXPORT_TASK == "tax_export" and TAX_EXPORT_TASK in app.tasks
    assert export_lock(EXPORT) == f"export:{EXPORT}"
