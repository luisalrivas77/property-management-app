"""Correcting events through append_event, as hausbuch_app under RLS."""

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import event as sa_event

from hausbuch.db.session import workspace_transaction
from hausbuch.rules.validation import CORRECTION_MISMATCH
from hausbuch.schemas.enums import ActorType
from hausbuch.schemas.events import Empty, EventType, PurchaseNotarized
from hausbuch.services.context import Actor, new_command
from hausbuch.services.events import (
    EventNotFoundError,
    InvalidEventError,
    append_event,
    latest_in_chain,
    list_events,
    milestone_root,
)

NOW = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")


@contextmanager
def recorded_sql(conn):
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    sa_event.listen(conn, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        sa_event.remove(conn, "before_cursor_execute", record)


def _scope():
    return {"property_id": uuid.uuid4(), "unit_id": uuid.uuid4()}


def _started(ctx, tenancy_id, scope, day, corrects=None):
    return append_event(
        ctx,
        EventType.TENANCY_STARTED,
        tenancy_id,
        Empty(),
        effective_date=day,
        corrects_event_id=corrects,
        **scope,
    )


def test_a_chain_goes_forward_and_latest_in_chain_follows_it(app_engine, workspaces):
    a, _ = workspaces
    tenancy_id, scope = uuid.uuid4(), _scope()
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        first = _started(ctx, tenancy_id, scope, date(2024, 7, 15))
        second = _started(ctx, tenancy_id, scope, date(2024, 7, 11), corrects=first.id)
        third = _started(ctx, tenancy_id, scope, date(2024, 7, 1), corrects=second.id)

    assert (second.corrects_event_id, third.corrects_event_id) == (first.id, second.id)
    with workspace_transaction(app_engine, a) as conn:
        for e in (first, second, third):
            assert latest_in_chain(conn, e.id) == third
        assert milestone_root(conn, EventType.TENANCY_STARTED, tenancy_id) == first


def test_only_the_latest_event_of_a_chain_can_be_corrected(app_engine, workspaces):
    a, _ = workspaces
    tenancy_id, scope = uuid.uuid4(), _scope()
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        first = _started(ctx, tenancy_id, scope, date(2024, 7, 15))
        second = _started(ctx, tenancy_id, scope, date(2024, 7, 1), corrects=first.id)

    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        already = f"already corrected.*{second.id}"
        with recorded_sql(conn) as statements, pytest.raises(InvalidEventError, match=already):
            _started(ctx, tenancy_id, scope, date(2024, 7, 2), corrects=first.id)
        assert not any(s.lstrip().upper().startswith("INSERT") for s in statements)
        assert len(list_events(conn, entity_id=tenancy_id)) == 2


REJECTED = {
    "other-type": (
        CORRECTION_MISMATCH,
        lambda tenancy_id, scope: dict(
            type=EventType.TENANCY_ENDED, entity_id=tenancy_id, payload=Empty(), **scope
        ),
    ),
    "other-entity": (
        CORRECTION_MISMATCH,
        lambda _tenancy_id, scope: dict(
            type=EventType.TENANCY_STARTED, entity_id=uuid.uuid4(), payload=Empty(), **scope
        ),
    ),
    "other-entity-type": (
        CORRECTION_MISMATCH,
        lambda tenancy_id, scope: dict(
            type=EventType.PROPERTY_PURCHASE_NOTARIZED,
            entity_id=tenancy_id,
            payload=PurchaseNotarized(purchase_price="1.00"),
            property_id=scope["property_id"],
        ),
    ),
    "other-unit": (
        None,
        lambda tenancy_id, scope: dict(
            type=EventType.TENANCY_STARTED,
            entity_id=tenancy_id,
            payload=Empty(),
            property_id=scope["property_id"],
            unit_id=uuid.uuid4(),
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(REJECTED))
def test_a_mismatched_correction_is_rejected_before_any_insert(app_engine, workspaces, case):
    a, _ = workspaces
    code, build = REJECTED[case]
    tenancy_id, scope = uuid.uuid4(), _scope()
    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        original = _started(ctx, tenancy_id, scope, date(2024, 7, 15))
        kwargs = build(tenancy_id, scope)
        with recorded_sql(conn) as statements, pytest.raises(InvalidEventError) as raised:
            append_event(
                ctx,
                kwargs.pop("type"),
                kwargs.pop("entity_id"),
                kwargs.pop("payload"),
                effective_date=date(2024, 7, 1),
                corrects_event_id=original.id,
                **kwargs,
            )
        assert raised.value.code == code
        if code is None:
            assert "History scope" in str(raised.value)
        assert not any(s.lstrip().upper().startswith("INSERT") for s in statements)
        assert latest_in_chain(conn, original.id) == original


def test_two_concurrent_corrections_of_one_event_cannot_both_commit(app_engine, workspaces):
    a, _ = workspaces
    tenancy_id, scope = uuid.uuid4(), _scope()
    with workspace_transaction(app_engine, a) as conn:
        original = _started(
            new_command(conn, a, ACTOR, now=NOW), tenancy_id, scope, date(2024, 7, 15)
        )

    errors: list[Exception] = []

    def second_correction():
        try:
            with workspace_transaction(app_engine, a) as conn2:
                ctx2 = new_command(conn2, a, ACTOR, now=NOW)
                _started(ctx2, tenancy_id, scope, date(2024, 7, 2), corrects=original.id)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    with workspace_transaction(app_engine, a) as conn1:
        ctx1 = new_command(conn1, a, ACTOR, now=NOW)
        _started(ctx1, tenancy_id, scope, date(2024, 7, 1), corrects=original.id)
        # The second command passes the read-only checks (it cannot see the first, which is
        # uncommitted), then waits on the UNIQUE index until the first commits.
        other = threading.Thread(target=second_correction)
        other.start()
        other.join(timeout=1)
        assert other.is_alive()
    other.join(timeout=10)

    assert not other.is_alive()
    [error] = errors
    assert isinstance(error, InvalidEventError) and "concurrently" in str(error)
    with workspace_transaction(app_engine, a) as conn:
        assert latest_in_chain(conn, original.id).effective_date == date(2024, 7, 1)
        assert len(list_events(conn, entity_id=tenancy_id)) == 2


def test_correcting_an_unknown_or_foreign_event_is_rejected(app_engine, workspaces):
    a, b = workspaces
    tenancy_id, scope = uuid.uuid4(), _scope()
    with workspace_transaction(app_engine, b) as conn:
        foreign = _started(
            new_command(conn, b, ACTOR, now=NOW), tenancy_id, scope, date(2024, 7, 1)
        )

    with workspace_transaction(app_engine, a) as conn:
        ctx = new_command(conn, a, ACTOR, now=NOW)
        for target in (uuid.uuid4(), foreign.id):
            with pytest.raises(InvalidEventError, match="not found"):
                _started(ctx, tenancy_id, scope, date(2024, 7, 2), corrects=target)
        with pytest.raises(EventNotFoundError):
            latest_in_chain(conn, foreign.id)
        with pytest.raises(EventNotFoundError):
            milestone_root(conn, EventType.TENANCY_STARTED, tenancy_id)
