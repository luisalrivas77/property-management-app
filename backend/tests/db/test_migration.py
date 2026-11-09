"""The phase 1 migrations: structure, row-level security, and the invariants they enforce.

The auth tables of 0003 and their auth_lookup policy are tested in test_auth_tables.py."""

import hashlib
import uuid

import pytest
from db.db_support import (
    APP,
    OWNER,
    alembic,
    create_database,
    drop_database,
    insert_event,
    insert_property,
    insert_tenancy,
    insert_unit,
)
from psycopg import errors
from psycopg.types.json import Jsonb

MIGRATION_TABLES = {
    "workspace",
    "app_user",
    "owner",
    "document_file",
    "document",
    "document_extraction",
    "property",
    "property_ownership",
    "property_acquisition_cost",
    "property_price_split",
    "unit",
    "tenant",
    "tenancy",
    "tenancy_tenant",
    "rent_schedule",
    "loan",
    "document_link",
    "event",
    "auth_passkey",
    "auth_session",
    "auth_invite",
    # Phase 2 (docs/phases/phase-2.md §3)
    "ai_call",
    "proposal",
    "account",
    "statement_import",
    "transaction",
    "possible_duplicate",
    "allocation",
    "matching_rule",
    # Phase 3 (docs/phases/phase-3.md §6)
    "tax_input",
    "tax_export",
}
# Procrastinate's own tables (0005): not RLS'd, no workspace_id (D20). The one exclusion.
QUEUE_TABLE_PREFIX = "procrastinate_"


def test_round_trip_upgrade_downgrade_upgrade():
    name = create_database()
    try:
        alembic(name, "upgrade", "head")
        alembic(name, "downgrade", "base")
        alembic(name, "upgrade", "head")
    finally:
        drop_database(name)


def test_every_table_has_forced_rls_and_a_policy(connect):
    with connect(OWNER) as conn:
        rows = conn.execute("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
        """).fetchall()
    rows = [r for r in rows if not r[0].startswith(QUEUE_TABLE_PREFIX)]
    assert {r[0] for r in rows} == MIGRATION_TABLES
    assert all(r[1] and r[2] and r[3] for r in rows), [r for r in rows if not all(r[1:])]


def test_every_table_has_workspace_id(connect):
    with connect(OWNER) as conn:
        tables = {
            r[0]
            for r in conn.execute("""
            SELECT table_name FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'workspace_id'
        """)
        }
    assert tables == MIGRATION_TABLES - {"workspace"}


# --- Row-level security ------------------------------------------------------


def test_workspace_sees_only_its_own_rows(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as ca, connect(APP, b) as cb:
        pa = insert_property(ca, a, label="A's flat")
        insert_property(cb, b, label="B's flat")
        ca.commit()
        cb.commit()
        assert [r[0] for r in ca.execute("SELECT id FROM property")] == [pa]
        assert [r[0] for r in ca.execute("SELECT id FROM workspace")] == [a]


def test_cannot_write_into_another_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn, pytest.raises(errors.InsufficientPrivilege):
        insert_property(conn, b)


def test_no_workspace_set_means_no_rows_and_no_writes(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        insert_property(conn, a)
        conn.commit()
    with connect(APP) as conn:
        assert conn.execute("SELECT count(*) FROM property").fetchone()[0] == 0
        with pytest.raises(errors.InsufficientPrivilege):
            insert_property(conn, a)


def test_cannot_reference_another_workspaces_row_by_id(connect, workspaces):
    """Composite FKs: RLS hides A's property from B, and B cannot point at it either."""
    a, b = workspaces
    with connect(APP, a) as conn:
        pa = insert_property(conn, a)
        conn.commit()
    with connect(APP, b) as conn, pytest.raises(errors.ForeignKeyViolation):
        insert_unit(conn, b, pa)


# --- Append-only -------------------------------------------------------------


def test_app_role_has_no_update_on_event(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        eid = insert_event(conn, a)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("UPDATE event SET actor_id = 'x' WHERE id = %s", (eid,))


@pytest.mark.parametrize(
    "statement",
    ["UPDATE event SET actor_id = 'x' WHERE id = %s", "DELETE FROM event WHERE id = %s"],
)
def test_event_is_append_only_even_for_the_owner(connect, workspaces, statement):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        eid = insert_event(conn, a)
        with pytest.raises(errors.RestrictViolation, match="append-only"):
            conn.execute(statement, (eid,))


def test_event_cannot_be_truncated(connect, workspaces):
    with connect(OWNER, workspaces[0]) as conn, pytest.raises(errors.RestrictViolation):
        conn.execute("TRUNCATE event")


def test_event_type_must_match_entity_type(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn, pytest.raises(errors.CheckViolation):
        insert_event(conn, a, type="tenancy.started", entity_type="property")


def test_event_is_corrected_at_most_once(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        first = insert_event(conn, a)
        insert_event(conn, a, corrects_event_id=first)
        with pytest.raises(errors.UniqueViolation):
            insert_event(conn, a, corrects_event_id=first)


def _rent_row(conn, ws, tenancy_id, **kw):
    cols = {
        "workspace_id": ws,
        "tenancy_id": tenancy_id,
        "effective_from": "2024-07-01",
        "cold_rent": "950.00",
        "reason": "initial",
    } | kw
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return conn.execute(
        f"INSERT INTO rent_schedule ({names}) VALUES ({marks}) RETURNING id", list(cols.values())
    ).fetchone()[0]


def test_rent_schedule_is_append_only_and_corrections_stay_in_their_tenancy(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        unit = insert_unit(conn, a, insert_property(conn, a))
        t1 = insert_tenancy(conn, a, unit, "2024-07-01", "2025-06-30")
        t2 = insert_tenancy(conn, a, unit, "2025-07-01")
        row = _rent_row(conn, a, t1)
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("UPDATE rent_schedule SET cold_rent = 1 WHERE id = %s", (row,))
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _rent_row(conn, a, t1, supersedes_id=row)  # correction needs reason 'correction'
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            _rent_row(conn, a, t2, reason="correction", supersedes_id=row)
        _rent_row(conn, a, t1, reason="correction", supersedes_id=row, cold_rent="960.00")


def test_document_file_is_immutable_and_deduplicated_per_workspace(connect, workspaces):
    a, b = workspaces
    digest = hashlib.sha256(b"Kaufvertrag").digest()

    def put(conn, ws):
        conn.execute(
            "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
            " VALUES (%s, %s, 11, 'application/pdf', %s)",
            (ws, digest, f"{ws}/sha256/{digest.hex()}"),
        )

    with connect(OWNER, a) as conn:
        put(conn, a)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            put(conn, a)
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("UPDATE document_file SET media_type = 'text/plain'")
    with connect(OWNER, b) as conn:
        put(conn, b)  # same bytes, other workspace: separate original


# --- Domain constraints ------------------------------------------------------


def test_tenancies_on_one_unit_cannot_overlap(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        unit = insert_unit(conn, a, insert_property(conn, a))
        insert_tenancy(conn, a, unit, "2024-07-01", "2025-06-30")
        insert_tenancy(conn, a, unit, "2025-07-01")  # adjacent: fine
        with pytest.raises(errors.ExclusionViolation), conn.transaction():
            insert_tenancy(conn, a, unit, "2025-06-30", "2025-12-31")  # shares a day
        with pytest.raises(errors.ExclusionViolation), conn.transaction():
            insert_tenancy(conn, a, unit, "2030-01-01")  # inside the open-ended one


def test_at_most_one_primary_tenant(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        unit = insert_unit(conn, a, insert_property(conn, a))
        tenancy = insert_tenancy(conn, a, unit, "2024-07-01")
        t1, t2 = (
            conn.execute(
                "INSERT INTO tenant (workspace_id, full_name) VALUES (%s, %s) RETURNING id",
                (a, name),
            ).fetchone()[0]
            for name in ("Erika Muster", "Max Muster")
        )
        sql = (
            "INSERT INTO tenancy_tenant (workspace_id, tenancy_id, tenant_id, role)"
            " VALUES (%s, %s, %s, %s)"
        )
        conn.execute(sql, (a, tenancy, t1, "primary"))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(sql, (a, tenancy, t2, "primary"))


def test_erased_tenant_keeps_no_personal_data(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        tid = conn.execute(
            "INSERT INTO tenant (workspace_id, full_name, email) VALUES (%s, 'Erika', 'e@x.de')"
            " RETURNING id",
            (a,),
        ).fetchone()[0]
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE tenant SET erased_at = now() WHERE id = %s", (tid,))
        conn.execute(
            "UPDATE tenant SET erased_at = now(), full_name = NULL, email = NULL WHERE id = %s",
            (tid,),
        )


def test_appraisal_split_needs_evidence_and_selected_split_must_be_the_propertys(
    connect, workspaces
):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        p1, p2 = insert_property(conn, a), insert_property(conn, a, label="Other")
        split_sql = (
            "INSERT INTO property_price_split (workspace_id, property_id, source, building_share)"
            " VALUES (%s, %s, %s, 0.72) RETURNING id"
        )
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(split_sql, (a, p1, "appraisal"))
        s2 = conn.execute(split_sql, (a, p2, "purchase_contract")).fetchone()[0]
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            conn.execute("UPDATE property SET selected_price_split_id = %s WHERE id = %s", (s2, p1))
        conn.execute("UPDATE property SET selected_price_split_id = %s WHERE id = %s", (s2, p2))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute(
                "UPDATE property_price_split SET building_share = 0.8 WHERE id = %s", (s2,)
            )


def test_document_link_has_exactly_one_target(connect, workspaces):
    a, _ = workspaces
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    with connect(APP, a) as conn:
        prop = insert_property(conn, a)
        unit = insert_unit(conn, a, prop)
        conn.execute(
            "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
            " VALUES (%s, %s, 1, 'application/pdf', %s)",
            (a, digest, digest.hex()),
        )
        doc = conn.execute(
            "INSERT INTO document (workspace_id, file_sha256, source) VALUES (%s, %s, 'upload')"
            " RETURNING id",
            (a, digest),
        ).fetchone()[0]
        link = (
            "INSERT INTO document_link (workspace_id, document_id, property_id, unit_id)"
            " VALUES (%s, %s, %s, %s)"
        )
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(link, (a, doc, prop, unit))
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(link, (a, doc, None, None))
        conn.execute(link, (a, doc, prop, None))
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            conn.execute(link, (a, doc, prop, None))


def test_events_accept_jsonb_object_payloads_only(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn, pytest.raises(errors.CheckViolation):
        insert_event(conn, a, payload=Jsonb([1, 2]))
