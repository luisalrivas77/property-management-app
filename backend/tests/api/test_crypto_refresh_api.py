"""„Jetzt aktualisieren“ over HTTP (docs/phases/phase-10.md §7.7, D291): POST queues one
refresh_crypto job per household, a second press while it runs or within two minutes of its
end queues nothing and returns that run, GET reports the state, and another household sees
none of it. Invented data only."""

import pytest
from api_support import ok
from db.db_support import _admin

from hausbuch.jobs.refresh_crypto import REFRESH_CRYPTO_TASK

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
