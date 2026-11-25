"""Phase 10 §7.10 (b): a kept answer's check (§7.4, `bypass`) recomputes the household plan
from the database, never from the plan's kept reads (services.plan_inputs), so `compare_all`
still compares a kept answer with a fresh computation."""

import asyncio

import pytest

from hausbuch.services import plan_inputs

pytestmark = pytest.mark.db


def test_a_bypassed_request_reads_the_plan_from_the_database(api_app, auth_a, monkeypatch):
    kept = api_app.state.kept_answers
    stamps = []
    real = plan_inputs._stamp

    def spy(conn, today):
        stamps.append(today)
        return real(conn, today)

    monkeypatch.setattr(plan_inputs, "_stamp", spy)
    plan_inputs.clear()
    try:
        checked = asyncio.run(kept.internal_get(auth_a, "/api/household-plan", bypass=True))
        assert checked.status == 200
        assert stamps == []  # the bypass reached the route's thread: no kept read consulted
        served = asyncio.run(kept.internal_get(auth_a, "/api/household-plan"))
        assert served.status == 200
        assert stamps  # an ordinary request reads through the keep
        assert served.body == checked.body
    finally:
        plan_inputs.clear()
