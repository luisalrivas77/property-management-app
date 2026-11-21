"""The kept answers' warm step and daily check: their registration (phase 10 §7.4). What they
do is tests/api/test_kept_answers.py."""

import procrastinate
from procrastinate import testing

from hausbuch.jobs import kept_answers as job
from hausbuch.jobs import tasks


def _registered(name: str):
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[name]
    periodic = [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
    return task, periodic


def test_the_warm_step_runs_daily_at_nine_after_the_key_figures():
    task, periodic = _registered(job.WARM_TASK)
    assert task.lock == job.WARM_LOCK
    assert [p.cron for p in periodic] == [job.WARM_CRON] == ["0 9 * * *"]
    assert [p.periodic_id for p in periodic] == [job.WARM_TASK]


def test_the_check_runs_daily_at_three_in_the_afternoon():
    task, periodic = _registered(job.CHECK_TASK)
    assert task.lock == job.CHECK_LOCK
    assert [p.cron for p in periodic] == [job.CHECK_CRON] == ["0 15 * * *"]
    assert [p.periodic_id for p in periodic] == [job.CHECK_TASK]
