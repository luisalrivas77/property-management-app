"""„Jetzt aktualisieren“ over HTTP (docs/phases/phase-10.md §7.7, D291): POST queues one
refresh_crypto job per household, a second press while it runs or within two minutes of its
end queues nothing and returns that run, GET reports the state with a succeeded run's counts
from its own event, and another household sees none of it. Invented data only."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from api_support import ok
from db.db_support import _admin

from hausbuch.jobs.refresh_crypto import REFRESH_CRYPTO_TASK, RefreshResult, record_run
from hausbuch.services.prices.spot import SpotResult

pytestmark = pytest.mark.db


@pytest.fixture
def jobs(dbname):
    def _jobs(workspace_id) -> list[tuple[int, dict, str, str]]:
        with _admin(dbname) as conn:
            rows = conn.execute(
                "SELECT id, args, status::text, lock FROM procrastinate_jobs"
                " WHERE task_name = %s AND args->>'workspace_id' = %s ORDER BY id",
                (REFRESH_CRYPTO_TASK, str(workspace_id)),
            ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    return _jobs


@pytest.fixture
def admin_sql(dbname):
    def _sql(sql: str, *params) -> None:
        with _admin(dbname) as conn:
            conn.execute(sql, params)

    return _sql


def _finish(admin_sql, job_id: int, status: str = "succeeded") -> None:
    admin_sql("UPDATE procrastinate_jobs SET status = 'doing' WHERE id = %s", job_id)
    admin_sql("UPDATE procrastinate_jobs SET status = %s WHERE id = %s", status, job_id)


def _age(admin_sql, job_id: int, minutes: int) -> None:
    admin_sql(
        "UPDATE procrastinate_events SET at = now() - make_interval(mins => %s) WHERE job_id = %s",
        minutes,
        job_id,
    )


def test_one_refresh_at_a_time_and_none_within_two_minutes_of_the_last(
    client_a, client_b, auth_a, jobs, admin_sql
):
    assert ok(client_a.get("/api/crypto/refresh")) == {
        "job_id": None,
        "status": "idle",
        "queued": False,
        "finished_at": None,
        "spot_read_at": None,
        "stored": None,
        "unpriced": None,
        "wallets_failed": None,
        "wallets_aborted": None,
    }

    first = ok(client_a.post("/api/crypto/refresh"), 202)
    assert first["queued"] is True and first["status"] == "running"
    [(job_id, args, status, lock)] = jobs(auth_a.workspace_id)
    assert first["job_id"] == job_id
    assert args == {"workspace_id": str(auth_a.workspace_id)}
    assert (status, lock) == ("todo", "wallets:sync")

    # A second press while it waits or runs: that run, nothing queued.
    again = ok(client_a.post("/api/crypto/refresh"), 202)
    assert (again["queued"], again["job_id"], again["status"]) == (False, job_id, "running")
    assert ok(client_a.get("/api/crypto/refresh"))["status"] == "running"
    admin_sql("UPDATE procrastinate_jobs SET status = 'doing' WHERE id = %s", job_id)
    assert ok(client_a.post("/api/crypto/refresh"), 202)["queued"] is False

    # Finished just now: still refused, the finished run returned.
    admin_sql("UPDATE procrastinate_jobs SET status = 'succeeded' WHERE id = %s", job_id)
    done = ok(client_a.post("/api/crypto/refresh"), 202)
    assert (done["queued"], done["job_id"], done["status"]) == (False, job_id, "succeeded")
    assert done["finished_at"] is not None
    assert len(jobs(auth_a.workspace_id)) == 1

    # Another household never sees this one's run, and may press its own.
    assert ok(client_b.get("/api/crypto/refresh"))["status"] == "idle"
    other = ok(client_b.post("/api/crypto/refresh"), 202)
    assert other["queued"] is True and other["job_id"] != job_id
    assert ok(client_a.get("/api/crypto/refresh"))["job_id"] == job_id

    # Three minutes after its end a new press queues a new run.
    _age(admin_sql, job_id, 3)
    fresh = ok(client_a.post("/api/crypto/refresh"), 202)
    assert fresh["queued"] is True and fresh["job_id"] not in (job_id, other["job_id"])
    assert ok(client_a.get("/api/crypto/refresh"))["job_id"] == fresh["job_id"]


def test_a_failed_run_is_reported_and_refuses_a_press_for_two_minutes(client_a, admin_sql):
    job_id = ok(client_a.post("/api/crypto/refresh"), 202)["job_id"]
    _finish(admin_sql, job_id, "failed")
    state = ok(client_a.get("/api/crypto/refresh"))
    assert (state["status"], state["job_id"]) == ("failed", job_id)
    assert ok(client_a.post("/api/crypto/refresh"), 202)["queued"] is False
    _age(admin_sql, job_id, 3)
    assert ok(client_a.post("/api/crypto/refresh"), 202)["queued"] is True


def _record(app_engine, workspace_id, job_id, **counts) -> None:
    prices = SpotResult(coins=3, stored=counts.pop("stored"), unpriced=counts.pop("unpriced"))
    deps = SimpleNamespace(engine=app_engine, clock=lambda: datetime.now(UTC))
    record_run(deps, workspace_id, RefreshResult(prices=prices, **counts), job_id)


def _counts(state: dict) -> tuple:
    return tuple(state[k] for k in ("stored", "unpriced", "wallets_failed", "wallets_aborted"))


def test_a_succeeded_run_reports_its_own_counts_and_no_older_ones(
    client_a, client_b, auth_a, admin_sql, app_engine
):
    first = ok(client_a.post("/api/crypto/refresh"), 202)["job_id"]
    _finish(admin_sql, first)
    # Finished but no event (a run that ended before writing it): no counts, never invented.
    assert _counts(ok(client_a.get("/api/crypto/refresh"))) == (None, None, None, None)
    _record(
        app_engine, auth_a.workspace_id, first, stored=1, unpriced=2, addresses=4, wallets_failed=3
    )
    state = ok(client_a.get("/api/crypto/refresh"))
    assert (state["status"], _counts(state)) == ("succeeded", (1, 2, 3, False))
    # The refused press returns the same counts.
    assert _counts(ok(client_a.post("/api/crypto/refresh"), 202)) == (1, 2, 3, False)
    assert _counts(ok(client_b.get("/api/crypto/refresh"))) == (None, None, None, None)

    # A newer run shows only its own event: none while it runs, and none once it failed.
    _age(admin_sql, first, 3)
    second = ok(client_a.post("/api/crypto/refresh"), 202)["job_id"]
    assert _counts(ok(client_a.get("/api/crypto/refresh"))) == (None, None, None, None)
    _finish(admin_sql, second, "failed")
    assert _counts(ok(client_a.get("/api/crypto/refresh"))) == (None, None, None, None)

    _age(admin_sql, second, 3)
    third = ok(client_a.post("/api/crypto/refresh"), 202)["job_id"]
    _finish(admin_sql, third)
    _record(app_engine, auth_a.workspace_id, third, stored=0, unpriced=3, wallets_aborted=True)
    assert _counts(ok(client_a.get("/api/crypto/refresh"))) == (0, 3, 0, True)
