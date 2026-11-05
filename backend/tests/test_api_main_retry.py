"""The API's startup role check is retried while the database is unreachable, and never when
the role is refused (phase 1 step 3, §10.8 item 4). No database needed: the unreachable case
uses a real engine on a closed local port, the others a stand-in for check_database_role."""

import logging

import pytest
from sqlalchemy import exc as sa_exc

from hausbuch.api import main
from hausbuch.db.session import UnsafeDatabaseRoleError, create_app_engine

SECRET = "s3cr3t-pw"
UNREACHABLE_URL = f"postgresql+psycopg://hausbuch_app:{SECRET}@127.0.0.1:1/hausbuch"


class Clock:
    """A fake monotonic clock that sleep advances."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _unreachable() -> sa_exc.OperationalError:
    return sa_exc.OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))


def _check_failing(monkeypatch, errors: list[Exception]) -> list[int]:
    """Replace check_database_role: raise the queued errors in order, then succeed."""
    calls: list[int] = []

    def fake(_engine) -> None:
        calls.append(1)
        if errors:
            raise errors.pop(0)

    monkeypatch.setattr(main, "check_database_role", fake)
    return calls


def test_retries_connection_errors_then_succeeds(monkeypatch, caplog):
    calls = _check_failing(monkeypatch, [_unreachable(), _unreachable(), _unreachable()])
    clock = Clock()
    with caplog.at_level(logging.INFO, logger="hausbuch.api.main"):
        main.check_database_role_with_retry(object(), sleep=clock.sleep, clock=clock)
    assert len(calls) == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]  # exponential backoff
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.fields["attempt"] for r in warnings] == [1, 2, 3]
    assert caplog.records[-1].fields == {"event": "db_reachable", "attempt": 4}


def test_gives_up_after_six_attempts_within_the_budget(monkeypatch):
    calls = _check_failing(monkeypatch, [_unreachable() for _ in range(10)])
    clock = Clock()
    with pytest.raises(sa_exc.OperationalError):
        main.check_database_role_with_retry(object(), sleep=clock.sleep, clock=clock)
    assert len(calls) == main.STARTUP_ATTEMPTS == 6
    assert clock.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert sum(clock.sleeps) <= main.STARTUP_BUDGET_S


def test_stops_early_when_attempts_eat_the_budget(monkeypatch):
    """Attempts that hang count against the budget: no attempt starts past ~60 s."""
    clock = Clock()
    started: list[float] = []

    def slow_failure(_engine) -> None:
        started.append(clock.now)
        clock.now += 25.0  # each connect attempt hangs 25 s before failing
        raise _unreachable()

    monkeypatch.setattr(main, "check_database_role", slow_failure)
    with pytest.raises(sa_exc.OperationalError):
        main.check_database_role_with_retry(object(), sleep=clock.sleep, clock=clock)
    # Starts at 0, 26 (25 + 1 s wait), 53 (51 + 2 s); a fourth at 82 would pass the budget.
    assert started == [0.0, 26.0, 53.0]
    assert clock.sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    "refusal",
    [
        main.SchemaOwnerRoleError("database role 'hausbuch_owner' owns the schema's tables"),
        UnsafeDatabaseRoleError("database role 'postgres' is a superuser"),
    ],
    ids=["schema-owner", "superuser"],
)
def test_a_role_refusal_fails_at_once(monkeypatch, refusal):
    calls = _check_failing(monkeypatch, [refusal])
    clock = Clock()
    with pytest.raises(type(refusal)):
        main.check_database_role_with_retry(object(), sleep=clock.sleep, clock=clock)
    assert (len(calls), clock.sleeps) == (1, [])


def test_a_real_unreachable_database_is_retried_without_logging_the_url(caplog):
    engine = create_app_engine(UNREACHABLE_URL)
    clock = Clock()
    try:
        with (
            caplog.at_level(logging.INFO, logger="hausbuch.api.main"),
            pytest.raises(sa_exc.OperationalError),
        ):
            main.check_database_role_with_retry(engine, sleep=clock.sleep, clock=clock)
    finally:
        engine.dispose()
    assert len(clock.sleeps) == 5
    assert [r.fields["error"] for r in caplog.records] == ["OperationalError"] * 6
    logged = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    for leak in (SECRET, "127.0.0.1", "hausbuch_app"):
        assert leak not in logged
