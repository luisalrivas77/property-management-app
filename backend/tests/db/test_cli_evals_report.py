"""`hausbuch evals report` against the real database, through the CLI with an owner URL (the
way Luis runs it): proposal.applied events in a provisioned workspace are counted per kind
and field, --workspace and --since filter, other workspaces stay out, nothing is written."""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import psycopg
import pytest
from db.db_support import ADMIN_URL, OWNER, make_engine
from sqlalchemy import func, select

from hausbuch import cli
from hausbuch.auth.provisioning import provision_workspace
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import event as event_table
from hausbuch.schemas.enums import ActorType, ExtractionKind
from hausbuch.schemas.events import AppliedRecord, EventType, ProposalApplied
from hausbuch.schemas.proposal import FieldDecision
from hausbuch.services.context import Actor, new_command
from hausbuch.services.events import append_event

pytestmark = pytest.mark.db

USER = Actor(ActorType.USER, "user:test")
EARLY = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
LATE = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)


def _owner_url(dbname: str) -> str:
    """A URL that logs in as the test superuser and acts as hausbuch_owner from the start
    (the test roles are NOLOGIN; tests/auth/auth_support.role_url does the same)."""
    info = psycopg.conninfo.conninfo_to_dict(ADMIN_URL)
    user, password = quote(info.get("user", "")), quote(info.get("password", ""))
    host, port = info.get("host", "localhost"), info.get("port", "5432")
    options = quote(f"-c role={OWNER}")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{dbname}?options={options}"


def _provision(dbname) -> uuid.UUID:
    owner = make_engine(dbname, OWNER)
    try:
        return provision_workspace(
            owner,
            name="Evals",
            email=f"evals-{uuid.uuid4().hex[:8]}@example.org",
            display_name="Test",
            now=EARLY - timedelta(days=30),
        ).workspace_id
    finally:
        owner.dispose()


def _apply_event(engine, ws, kind, at, records) -> None:
    payload = ProposalApplied(
        document_id=uuid.uuid4(),
        kind=kind,
        records=[
            AppliedRecord(
                key=key,
                mode="create",
                entity_type=entity,
                entity_id=uuid.uuid4(),
                decisions={name: FieldDecision(d) for name, d in decisions.items()},
            )
            for key, entity, decisions in records
        ],
    )
    with workspace_transaction(engine, ws) as conn:
        append_event(
            new_command(conn, ws, USER, now=at), EventType.PROPOSAL_APPLIED, uuid.uuid4(), payload
        )


def _count_events(engine, ws) -> int:
    with workspace_transaction(engine, ws) as conn:
        return conn.execute(select(func.count()).select_from(event_table)).scalar_one()


def _rows(out: str) -> dict[tuple[str, str, str], list[str]]:
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 10 and parts[3].isdigit():
            rows[tuple(parts[:3])] = parts[3:]
    return rows


@pytest.fixture
def seeded(dbname, app_engine, fresh_workspaces):
    ws = _provision(dbname)
    _apply_event(
        app_engine,
        ws,
        ExtractionKind.PURCHASE_CONTRACT,
        EARLY,
        [
            ("property", "property", {"purchase_price": "accepted", "notarized_on": "edited"}),
            ("unit", "unit", {"label": "skipped"}),
        ],
    )
    _apply_event(
        app_engine,
        ws,
        ExtractionKind.PURCHASE_CONTRACT,
        LATE,
        [("property", "property", {"purchase_price": "accepted", "notarized_on": "accepted"})],
    )
    _apply_event(
        app_engine,
        ws,
        ExtractionKind.INVOICE,
        LATE,
        [("cost", "acquisition_cost", {"amount": "cleared"})],
    )
    # Another workspace's event: never counted with --workspace ws.
    other, _ = fresh_workspaces
    _apply_event(
        app_engine,
        other,
        ExtractionKind.INVOICE,
        LATE,
        [("cost", "acquisition_cost", {"amount": "accepted"})],
    )
    return ws


def test_report_counts_one_workspace(dbname, app_engine, seeded, capsys):
    before = _count_events(app_engine, seeded)
    env = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}

    assert cli.main(["evals", "report", "--workspace", str(seeded)], environ=env) == cli.EXIT_OK
    out, err = capsys.readouterr()
    assert err == ""
    assert out.startswith("Pre-fill accuracy: 3 applied proposals (1 workspace)")
    rows = _rows(out)
    # proposed, accepted, edited, cleared, skipped, share
    assert rows[("purchase_contract", "property", "purchase_price")] == [
        "2", "2", "0", "0", "0", "100.0", "%",
    ]  # fmt: skip
    assert rows[("purchase_contract", "property", "notarized_on")][:6] == [
        "2", "1", "1", "0", "0", "50.0",
    ]  # fmt: skip
    assert rows[("purchase_contract", "unit", "label")][:5] == ["1", "0", "0", "0", "1"]
    assert rows[("invoice", "acquisition_cost", "amount")][:5] == ["1", "0", "0", "1", "0"]
    # money + dates: purchase_price A A, notarized_on E A, amount C -> 3 of 5
    assert "Money and date fields: 3 of 5 accepted = 60.0 % (target 90 %: NOT met)" in out
    assert _owner_url(dbname) not in out
    assert _count_events(app_engine, seeded) == before  # read-only


def test_report_since(dbname, seeded, capsys):
    env = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    args = ["evals", "report", "--workspace", str(seeded), "--since", "2026-11-15"]
    assert cli.main(args, environ=env) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("Pre-fill accuracy: 2 applied proposals (1 workspace, since 2026-11-15)")
    assert _rows(out)[("purchase_contract", "property", "notarized_on")][:3] == ["1", "1", "0"]
    assert ("purchase_contract", "unit", "label") not in _rows(out)


def test_report_over_all_provisioned_workspaces(dbname, seeded, capsys):
    env = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    assert cli.main(["evals", "report"], environ=env) == cli.EXIT_OK
    out = capsys.readouterr().out
    # Other tests may have provisioned workspaces too: at least this one's rows are in.
    amount = _rows(out)[("invoice", "acquisition_cost", "amount")]
    assert int(amount[3]) >= 1  # cleared
    assert int(_rows(out)[("purchase_contract", "property", "purchase_price")][1]) >= 2


def test_the_report_transaction_is_read_only(dbname, seeded):
    from hausbuch.evals.report import read_only_workspace

    owner = make_engine(dbname, OWNER)
    try:
        with (
            pytest.raises(Exception, match="read-only transaction"),
            read_only_workspace(owner, seeded) as conn,
        ):
            conn.execute(event_table.delete())
    finally:
        owner.dispose()
