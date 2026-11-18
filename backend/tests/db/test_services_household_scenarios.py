"""services.household_scenarios under hausbuch_app and RLS (docs/phases/phase-9.md D248, §7.3,
§13): append-only rows, supersede and withdraw, one default in force per workspace (also
under two writes at once), the levers' checks against the stored accounts and the horizon,
the event in the same transaction (ids and flags only), and workspace isolation. Invented
data only (owners A and B)."""

import itertools
import threading
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from sqlalchemy import select

from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.rules.errors import (
    HOUSEHOLD_SCENARIO_ACCOUNT_INVALID,
    HOUSEHOLD_SCENARIO_NOT_CURRENT,
    HOUSEHOLD_SCENARIO_ONE_OFF_OUTSIDE,
    HOUSEHOLD_SCENARIO_WITHDRAWAL_INVALID,
)
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind, ActorType
from hausbuch.schemas.events import EventType
from hausbuch.schemas.plan import HouseholdScenarioCreate
from hausbuch.services import health
from hausbuch.services import household_scenarios as svc
from hausbuch.services.accounts import create_account
from hausbuch.services.context import Actor, new_command
from hausbuch.services.errors import NotFoundError, RuleViolationError

pytestmark = pytest.mark.db

# 7 Nov 2026: in a workspace without accounts every ended month counts as closed (D231 (a)
# checks the accounts there are), so the newest closed month is October 2026.
NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
ACTOR = Actor(ActorType.USER, "user:test")
_TICK = itertools.count(1)


def _cmd(engine, ws, command, *args, **kwargs):
    """One write in its own committed transaction, a second after the previous one, so rows
    written in one test do not share created_at."""
    now = NOW + timedelta(seconds=next(_TICK))
    with workspace_transaction(engine, ws) as conn:
        return command(new_command(conn, ws, ACTOR, now=now), *args, **kwargs)


def _read(engine, ws, query, *args, **kwargs):
    with workspace_transaction(engine, ws) as conn:
        return query(conn, *args, **kwargs)


def _body(**fields) -> HouseholdScenarioCreate:
    return HouseholdScenarioCreate.model_validate({"name": "Basis"} | fields)


def _codes(exc_info) -> list[str]:
    return list(exc_info.value.codes)


def _account(engine, ws, kind: AccountKind, label: str):
    body = {
        AccountKind.BANK_CURRENT: {"iban": "DE89370400440532013000", "bank": "Testbank"},
        AccountKind.EXCHANGE: {"bank": "BISON", "provider": "BISON"},
    }[kind]
    data = AccountCreate.model_validate(
        {
            "label": label,
            "kind": kind,
            "purpose": "private",
            "opening_balance": "0.00",
            "opening_balance_on": "2025-12-31",
        }
        | body
    )
    with workspace_transaction(engine, ws) as conn:
        return create_account(
            new_command(conn, ws, ACTOR, now=NOW), data, iban_key=TEST_IBAN_KEY
        ).id


def test_a_scenario_is_recorded_corrected_and_listed_with_its_event(app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    first = _cmd(app_engine, ws, svc.create_scenario, _body(is_default=True))
    assert first.is_default and first.levers.horizon_months == 60
    levers = {"buffer_percent": "20", "one_offs": [{"month": "2027-03", "amount": "-900.00"}]}
    second = _cmd(
        app_engine,
        ws,
        svc.create_scenario,
        _body(name="Elternzeit", is_default=True, supersedes_id=first.id, levers=levers),
    )
    assert second.levers.buffer_percent == Decimal("20")
    assert [s.id for s in _read(app_engine, ws, svc.list_scenarios)] == [second.id]
    history = _read(app_engine, ws, svc.list_scenarios, include_history=True)
    assert [s.id for s in history] == [first.id, second.id]
    # The default in force is the one the key figures read (health.household_levers).
    assert _read(app_engine, ws, svc.default_scenario).id == second.id
    assert _read(app_engine, ws, health.household_levers).buffer_percent == Decimal("20")

    events = _read(
        app_engine,
        ws,
        lambda conn: conn.execute(
            select(event_table.c.type, event_table.c.entity_id, event_table.c.payload)
            .where(event_table.c.entity_type == "household_scenario")
            .order_by(event_table.c.seq)
        ).all(),
    )
    assert [(e.type, e.entity_id) for e in events] == [
        (EventType.HOUSEHOLD_SCENARIO_CREATED.value, first.id),
        (EventType.HOUSEHOLD_SCENARIO_CREATED.value, second.id),
    ]
    # Ids and flags only: no name, no lever, no amount.
    assert events[1].payload == {"is_default": True, "supersedes_id": str(first.id)}


def test_a_withdrawal_repeats_the_row_and_ends_it(app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    row = _cmd(
        app_engine, ws, svc.create_scenario, _body(is_default=True, levers={"horizon_months": 24})
    )
    gone = _cmd(app_engine, ws, svc.withdraw_scenario, row.id)
    assert (gone.withdrawn, gone.supersedes_id, gone.name) == (True, row.id, row.name)
    assert gone.levers == row.levers and gone.is_default
    assert _read(app_engine, ws, svc.list_scenarios) == []
    assert _read(app_engine, ws, svc.default_scenario) is None
    assert _read(app_engine, ws, health.household_levers).horizon_months == 60  # the defaults
    # The withdrawn row can still be looked at by its id.
    assert _read(app_engine, ws, health.household_levers, row.id).horizon_months == 24
    types = _read(
        app_engine,
        ws,
        lambda conn: (
            conn.execute(select(event_table.c.type).where(event_table.c.entity_id == gone.id))
            .scalars()
            .all()
        ),
    )
    assert types == [EventType.HOUSEHOLD_SCENARIO_WITHDRAWN.value]

    with pytest.raises(RuleViolationError) as exc:
        _cmd(app_engine, ws, svc.withdraw_scenario, row.id)
    assert _codes(exc) == [HOUSEHOLD_SCENARIO_NOT_CURRENT]
    with pytest.raises(RuleViolationError) as exc:
        _cmd(app_engine, ws, svc.withdraw_scenario, gone.id)  # a withdrawal is not in force
    assert _codes(exc) == [HOUSEHOLD_SCENARIO_NOT_CURRENT]
    with pytest.raises(NotFoundError):
        _cmd(app_engine, ws, svc.withdraw_scenario, uuid.uuid4())


def test_a_withdrawal_through_the_body_repeats_the_levers(app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    row = _cmd(app_engine, ws, svc.create_scenario, _body())
    with pytest.raises(RuleViolationError) as exc:
        _cmd(
            app_engine,
            ws,
            svc.create_scenario,
            _body(supersedes_id=row.id, withdrawn=True, levers={"horizon_months": 24}),
        )
    assert _codes(exc) == [HOUSEHOLD_SCENARIO_WITHDRAWAL_INVALID]
    gone = _cmd(app_engine, ws, svc.create_scenario, _body(supersedes_id=row.id, withdrawn=True))
    assert gone.withdrawn


def test_supersedes_must_be_in_force_and_one_default(app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    default = _cmd(app_engine, ws, svc.create_scenario, _body(is_default=True))
    with pytest.raises(RuleViolationError) as exc:
        _cmd(app_engine, ws, svc.create_scenario, _body(name="Zweiter", is_default=True))
    assert _codes(exc) == [HOUSEHOLD_SCENARIO_NOT_CURRENT]
    # A second scenario that is not the default is fine; so is superseding the default.
    _cmd(app_engine, ws, svc.create_scenario, _body(name="Variante"))
    _cmd(app_engine, ws, svc.create_scenario, _body(is_default=True, supersedes_id=default.id))
    with pytest.raises(RuleViolationError) as exc:
        _cmd(app_engine, ws, svc.create_scenario, _body(supersedes_id=default.id))
    assert _codes(exc) == [HOUSEHOLD_SCENARIO_NOT_CURRENT]
    with pytest.raises(NotFoundError):
        _cmd(app_engine, ws, svc.create_scenario, _body(supersedes_id=uuid.uuid4()))


def test_reserve_accounts_are_current_savings_or_card_accounts(app_engine, fresh_workspaces):
    ws, _ = fresh_workspaces
    current = _account(app_engine, ws, AccountKind.BANK_CURRENT, "Giro A")
    exchange = _account(app_engine, ws, AccountKind.EXCHANGE, "Börse B")
    row = _cmd(
        app_engine,
        ws,
        svc.create_scenario,
        _body(levers={"reserve_accounts": {"removed": [str(current)]}}),
    )
    assert row.levers.reserve_accounts.removed == [current]
    with pytest.raises(RuleViolationError) as exc:
        _cmd(
            app_engine,
            ws,
            svc.create_scenario,
            _body(levers={"reserve_accounts": {"added": [str(exchange)]}}),
        )
    assert _codes(exc) == [HOUSEHOLD_SCENARIO_ACCOUNT_INVALID]
    with pytest.raises(NotFoundError):
        _cmd(
            app_engine,
            ws,
            svc.create_scenario,
            _body(levers={"reserve_accounts": {"added": [str(uuid.uuid4())]}}),
        )


def test_a_one_off_lies_inside_the_horizon(app_engine, fresh_workspaces):
    """Newest closed month October 2026, horizon 12: the one-offs may fall in November 2026
    to October 2027."""
    ws, _ = fresh_workspaces
    assert _read(app_engine, ws, svc.newest_closed_month, date(2026, 11, 7)) == "2026-10"
    for month in ("2026-11", "2027-10"):
        levers = {"horizon_months": 12, "one_offs": [{"month": month, "amount": "500.00"}]}
        _cmd(app_engine, ws, svc.create_scenario, _body(levers=levers))
    for month in ("2026-10", "2027-11"):
        levers = {"horizon_months": 12, "one_offs": [{"month": month, "amount": "500.00"}]}
        with pytest.raises(RuleViolationError) as exc:
            _cmd(app_engine, ws, svc.create_scenario, _body(levers=levers))
        assert _codes(exc) == [HOUSEHOLD_SCENARIO_ONE_OFF_OUTSIDE]


def test_two_defaults_at_once_cannot_both_commit(app_engine, fresh_workspaces):
    """The first write holds its transaction open while the second starts in a thread: the
    second waits on the workspace lock, then sees the first default and is refused. Its
    lock_timeout and the join's timeout turn a regression into a failure, never a hang."""
    ws, _ = fresh_workspaces
    errors: list[Exception] = []
    finished = threading.Event()

    def second():
        try:
            with workspace_transaction(app_engine, ws) as conn:
                conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
                svc.create_scenario(
                    new_command(conn, ws, ACTOR, now=NOW), _body(name="B", is_default=True)
                )
        except Exception as exc:  # noqa: BLE001 - collected for the assertions below
            errors.append(exc)
        finally:
            finished.set()

    with workspace_transaction(app_engine, ws) as conn:
        svc.create_scenario(new_command(conn, ws, ACTOR, now=NOW), _body(name="A", is_default=True))
        other = threading.Thread(target=second)
        other.start()
        assert not finished.wait(timeout=1), "the second write did not wait for the first"
    other.join(timeout=20)
    assert not other.is_alive()
    [error] = errors
    assert isinstance(error, RuleViolationError)
    assert list(error.codes) == [HOUSEHOLD_SCENARIO_NOT_CURRENT]
    assert [s.name for s in _read(app_engine, ws, svc.list_scenarios)] == ["A"]


def test_another_workspace_sees_nothing_and_cannot_supersede(app_engine, fresh_workspaces):
    a, b = fresh_workspaces
    row = _cmd(app_engine, a, svc.create_scenario, _body(is_default=True))
    assert _read(app_engine, b, svc.list_scenarios, include_history=True) == []
    assert _read(app_engine, b, svc.default_scenario) is None
    with pytest.raises(NotFoundError):
        _cmd(app_engine, b, svc.create_scenario, _body(supersedes_id=row.id))
    with pytest.raises(NotFoundError):
        _cmd(app_engine, b, svc.withdraw_scenario, row.id)
    # B's own default is independent of A's.
    _cmd(app_engine, b, svc.create_scenario, _body(is_default=True))
