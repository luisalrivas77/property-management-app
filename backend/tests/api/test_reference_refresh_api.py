"""POST /household-goal/reference-returns/refresh over HTTP (phase 10 §7.11 (c) 3, point 8):
one refresh_reference_returns job per household under the download lock, a second press
while it waits or runs queues nothing and returns that job, a finished one lets the next
press queue again, and another household's press is its own."""

import pytest
from api_support import ok
from db.db_support import _admin

from hausbuch.services.reference_returns import LOCK, REFRESH_TASK

pytestmark = pytest.mark.db

URL = "/api/household-goal/reference-returns/refresh"


@pytest.fixture
def jobs(dbname):
    def _jobs(workspace_id) -> list[tuple[int, dict, str, str]]:
        with _admin(dbname) as conn:
            rows = conn.execute(
                "SELECT id, args, status::text, lock FROM procrastinate_jobs"
                " WHERE task_name = %s AND args->>'workspace_id' = %s ORDER BY id",
                (REFRESH_TASK, str(workspace_id)),
            ).fetchall()
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    return _jobs


def _set(dbname, job_id: int, status: str) -> None:
    with _admin(dbname) as conn:
        conn.execute("UPDATE procrastinate_jobs SET status = %s WHERE id = %s", (status, job_id))


def test_one_refresh_at_a_time_per_household(client_a, client_b, auth_a, jobs, dbname):
    first = ok(client_a.post(URL), 202)
    [(job_id, args, status, lock)] = jobs(auth_a.workspace_id)
    assert first == {"job_id": job_id, "queued": True}
    assert args == {"workspace_id": str(auth_a.workspace_id)}
    assert (status, lock) == ("todo", LOCK)

    assert ok(client_a.post(URL), 202) == {"job_id": job_id, "queued": False}
    _set(dbname, job_id, "doing")
    assert ok(client_a.post(URL), 202) == {"job_id": job_id, "queued": False}

    other = ok(client_b.post(URL), 202)
    assert other["queued"] is True and other["job_id"] != job_id
    assert [j[0] for j in jobs(auth_a.workspace_id)] == [job_id]

    _set(dbname, job_id, "failed")
    again = ok(client_a.post(URL), 202)
    assert again["queued"] is True and again["job_id"] not in (job_id, other["job_id"])
    assert len(jobs(auth_a.workspace_id)) == 2
