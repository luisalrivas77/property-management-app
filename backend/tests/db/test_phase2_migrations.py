"""The phase 2 migrations (docs/phases/phase-2.md §3): grants, immutability triggers, the
CHECKs that encode §3.4's invariants, and the Procrastinate schema of 0005 (D20).

RLS, workspace_id and the table list are covered for every table by test_migration.py."""

import hashlib
import importlib.resources
import uuid
from pathlib import Path

import pytest
from db.db_support import APP, BACKEND, OWNER, _insert
from psycopg import errors
from psycopg.types.json import Jsonb

from hausbuch.schemas.enums import ExtractionKind

VENDORED = Path(BACKEND) / "migrations" / "sql" / "procrastinate_3.10.0_schema.sql"
PROCRASTINATE_TABLES = {
    "procrastinate_workers",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
    "procrastinate_events",
}
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")

# Table-wide privileges of hausbuch_app on the phase 2 tables (column grants checked apart).
EXPECTED_GRANTS = {
    "ai_call": {"SELECT", "INSERT", "DELETE"},
    "proposal": {"SELECT", "INSERT", "UPDATE"},
    "account": {"SELECT", "INSERT", "UPDATE"},
    "statement_import": {"SELECT", "INSERT", "UPDATE"},
    "transaction": {"SELECT", "INSERT"},  # never DELETE (immutable as imported)
    "possible_duplicate": {"SELECT", "INSERT", "UPDATE"},
}
# Columns the app role may UPDATE on tables without a table-wide UPDATE.
COLUMN_UPDATES = {
    "ai_call": {"output", "raw_text"},
    "transaction": {"excluded_as_duplicate_of"},
}


def _granted(conn, table: str) -> set[str]:
    return {
        p
        for p in PRIVILEGES
        if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, table, p)).fetchone()[0]
    }


def _updatable_columns(conn, table: str) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            """
            SELECT attname FROM pg_attribute
            WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped
              AND has_column_privilege(%s, attrelid, attnum, 'UPDATE')
            """,
            (table, APP),
        )
    }


@pytest.mark.parametrize("table", sorted(EXPECTED_GRANTS))
def test_app_role_grants(connect, table):
    with connect(OWNER) as conn:
        assert _granted(conn, table) == EXPECTED_GRANTS[table]
        if table in COLUMN_UPDATES:
            assert _updatable_columns(conn, table) == COLUMN_UPDATES[table]


# --- Seed helpers ----------------------------------------------------------------


def _document(conn, ws) -> uuid.UUID:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/pdf', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    return _insert(
        conn, "document", {"workspace_id": ws, "file_sha256": digest, "source": "upload"}
    )


def _ai_call(conn, ws, document_id, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "job": "extract",
        "document_id": document_id,
        "model": "claude-sonnet-5-5",
        "prompt_version": "extract.purchase_contract.v1",
        "inference_geo": "us",
        "input_sha256": hashlib.sha256(b"request").digest(),
        "output": Jsonb({"notarized_on": "2024-03-15"}),
        "raw_text": "notarized_on: 2024-03-15",
    } | overrides
    return _insert(conn, "ai_call", row)


def _user(conn, ws) -> uuid.UUID:
    return _insert(
        conn,
        "app_user",
        {"workspace_id": ws, "email": f"{uuid.uuid4().hex}@example.org", "display_name": "Luis"},
    )


# --- ai_call: immutable except the scrub (D18) -----------------------------------


def test_ai_call_scrub_nulls_output_and_raw_text_only(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        call = _ai_call(conn, a, _document(conn, a))
        # The app role cannot touch any other column at all.
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE ai_call SET model = 'x' WHERE id = %s", (call,))
        # Changing a scrub column to a value (not null) is a mutation.
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("UPDATE ai_call SET raw_text = 'other' WHERE id = %s", (call,))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("UPDATE ai_call SET output = '{}' WHERE id = %s", (call,))
        conn.execute("UPDATE ai_call SET output = NULL, raw_text = NULL WHERE id = %s", (call,))
        row = conn.execute("SELECT output, raw_text, model FROM ai_call WHERE id = %s", (call,))
        assert row.fetchone() == (None, None, "claude-sonnet-5-5")
        conn.execute("UPDATE ai_call SET output = NULL WHERE id = %s", (call,))  # idempotent


def test_ai_call_is_immutable_even_for_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        call = _ai_call(conn, a, _document(conn, a))
        for statement in (
            "UPDATE ai_call SET model = 'x' WHERE id = %s",
            "UPDATE ai_call SET output = NULL, error_code = 'x' WHERE id = %s",
            "UPDATE ai_call SET created_at = now() - interval '1 day' WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation), conn.transaction():
                conn.execute(statement, (call,))


def test_ai_call_goes_with_its_document(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        doc = _document(conn, a)
        call = _ai_call(conn, a, doc)
        _insert(
            conn,
            "document_extraction",
            {
                "workspace_id": a,
                "document_id": doc,
                "version": 1,
                "source": "ai",
                "kind": "purchase_contract",
                "ai_call_id": call,
                "fields": Jsonb({}),
            },
        )
        proposal = _insert(conn, "proposal", {"workspace_id": a, "document_id": doc})
        conn.execute("DELETE FROM document WHERE id = %s", (doc,))
        for table, row_id in (("ai_call", call), ("proposal", proposal)):
            sql = f"SELECT count(*) FROM {table} WHERE id = %s"
            assert conn.execute(sql, (row_id,)).fetchone()[0] == 0, table


def test_ai_call_needs_a_document_to_classify_or_extract(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _ai_call(conn, a, None, job="classify")
        _ai_call(conn, a, None, job="explain")


def test_ai_extraction_names_its_ai_call(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        doc = _document(conn, a)

        def extraction(version, **row):
            return _insert(
                conn,
                "document_extraction",
                {
                    "workspace_id": a,
                    "document_id": doc,
                    "version": version,
                    "fields": Jsonb({}),
                }
                | row,
            )

        with pytest.raises(errors.CheckViolation), conn.transaction():
            extraction(1, source="ai", kind="invoice")
        with pytest.raises(errors.CheckViolation), conn.transaction():
            extraction(1, source="manual", ai_call_id=_ai_call(conn, a, doc))
        extraction(1, source="manual")  # phase 1 rows: kind defaults to manual
        assert conn.execute(
            "SELECT kind FROM document_extraction WHERE document_id = %s", (doc,)
        ).fetchone() == ("manual",)


def test_proposal_kind_is_an_extraction_kind_but_never_manual(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        doc = _document(conn, a)
        for kind in sorted(set(ExtractionKind) - {ExtractionKind.MANUAL}):
            _insert(conn, "proposal", {"workspace_id": a, "document_id": doc, "kind": kind.value})
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "proposal", {"workspace_id": a, "document_id": doc, "kind": "manual"})


# --- proposal: decided once, decisions only on apply, the D19 scrub ------------------


def test_proposal_decisions_exist_exactly_for_an_applied_proposal(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        doc = _document(conn, a)
        user = _user(conn, a)

        def proposal(**row):
            return _insert(conn, "proposal", {"workspace_id": a, "document_id": doc} | row)

        applied = {
            "status": "applied",
            "decided_at": "2026-11-06T10:00:00Z",
            "decisions": Jsonb({"property": {"address.city": "accepted"}}),
            "applied_entities": Jsonb([{"entity_type": "property", "id": str(uuid.uuid4())}]),
            "fields": Jsonb([]),
            "decided_by": user,
        }
        proposal(**applied)
        bad = [
            applied | {"decisions": None},  # applied without decisions
            applied | {"decided_at": None},  # applied without a decision time
            applied | {"applied_entities": None},
            {"status": "ready", "decisions": Jsonb({})},  # decisions before apply
            {"status": "rejected"},  # rejected without a decision time
            {"status": "ready", "decided_at": "2026-11-06T10:00:00Z"},
            {"status": "failed"},  # failed without an error code
            {"status": "ready", "error_code": "x"},
            {"targets": Jsonb([])},  # targets only for target_mode patch
            {"decisions": Jsonb([]), "status": "applied"},  # decisions are an object
        ]
        for row in bad:
            with pytest.raises(errors.CheckViolation), conn.transaction():
                proposal(**row)
        proposal(status="rejected", decided_at="2026-11-06T10:00:00Z")
        proposal(status="failed", error_code="ai.timeout")
        proposal(status="blocked_tenant_data", error_code="ai.tenant_data")
        proposal(target_mode="patch", targets=Jsonb([{"entity_type": "loan"}]))


def test_the_d19_scrub_keeps_an_applied_proposal_valid(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        doc = _document(conn, a)
        p = _insert(
            conn,
            "proposal",
            {
                "workspace_id": a,
                "document_id": doc,
                "status": "applied",
                "decided_at": "2026-08-01T10:00:00Z",
                "fields": Jsonb([{"key": "tenant:1"}]),
                "decisions": Jsonb({"tenant:1": {"full_name": "accepted"}}),
                "applied_entities": Jsonb([]),
            },
        )
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE proposal SET decisions = NULL WHERE id = %s", (p,))
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE proposal SET scrubbed_at = now() WHERE id = %s", (p,))
        conn.execute(
            "UPDATE proposal SET fields = NULL, decisions = NULL, scrubbed_at = now()"
            " WHERE id = %s",
            (p,),
        )


# --- 0006: accounts, imports, transactions ------------------------------------------

HMAC_A, HMAC_B = hashlib.sha256(b"iban-a").digest(), hashlib.sha256(b"iban-b").digest()


def _account(conn, ws, iban_hmac=HMAC_A, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "label": "Mietkonto",
        "bank": "Sparkasse",
        "iban_masked": "DE89 **** 3000",
        "iban_hmac": iban_hmac,
        "opening_balance": "1250.00",
        "opening_balance_on": "2026-09-30",
    } | overrides
    return _insert(conn, "account", row)


def _import(conn, ws, account_id, **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "account_id": account_id,
        "document_id": _document(conn, ws),
        "format": "csv_camt",
    } | overrides
    return _insert(conn, "statement_import", row)


def _transaction(conn, ws, account_id, import_id, line=b"line-1", **overrides) -> uuid.UUID:
    row = {
        "workspace_id": ws,
        "account_id": account_id,
        "import_id": import_id,
        "line_hash": hashlib.sha256(line).digest(),
        "booking_date": "2026-10-01",
        "amount": "1400.00",
        "purpose": "Miete Oktober WE 3",
    } | overrides
    return _insert(conn, "transaction", row)


def test_account_iban_is_unique_per_workspace(connect, workspaces):
    a, b = workspaces
    with connect(APP, a) as conn:
        _account(conn, a)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _account(conn, a, label="Zweitkonto")
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _account(conn, a, HMAC_B, iban_masked="DE89370400440532013000")  # not masked
    with connect(APP, b) as conn:
        _account(conn, b)  # the same account in another workspace is another row


def test_a_line_is_stored_once_per_account(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        acc, other = _account(conn, a), _account(conn, a, HMAC_B)
        imp = _import(conn, a, acc)
        _transaction(conn, a, acc, imp)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            _transaction(conn, a, acc, _import(conn, a, acc))  # a re-upload
        # The same line hash on another account is another line; but a transaction's import
        # must belong to its own account.
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            _transaction(conn, a, other, imp)
        _transaction(conn, a, other, _import(conn, a, other))


def test_transactions_are_immutable_except_one_exclusion(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        acc = _account(conn, a)
        imp = _import(conn, a, acc)
        first = _transaction(conn, a, acc, imp, b"csv-line")
        later = _transaction(conn, a, acc, _import(conn, a, acc, format="camt053"), b"camt-line")
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE transaction SET amount = 1 WHERE id = %s", (later,))
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("DELETE FROM transaction WHERE id = %s", (later,))
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE transaction SET excluded_as_duplicate_of = id WHERE id = %s", (later,)
            )
        set_once = "UPDATE transaction SET excluded_as_duplicate_of = %s WHERE id = %s"
        conn.execute(set_once, (first, later))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute(set_once, (None, later))  # never back to null
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute(set_once, (_transaction(conn, a, acc, imp, b"x"), later))  # nor moved


def test_transactions_cannot_be_changed_deleted_or_truncated_by_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        acc = _account(conn, a)
        tx = _transaction(conn, a, acc, _import(conn, a, acc))
        for statement in (
            "UPDATE transaction SET purpose = 'x' WHERE id = %s",
            "DELETE FROM transaction WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation), conn.transaction():
                conn.execute(statement, (tx,))
        with pytest.raises(errors.RestrictViolation), conn.transaction():
            conn.execute("TRUNCATE transaction CASCADE")


def test_a_duplicate_can_only_point_at_a_line_of_the_same_account(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        acc, other = _account(conn, a), _account(conn, a, HMAC_B)
        tx = _transaction(conn, a, acc, _import(conn, a, acc))
        foreign = _transaction(conn, a, other, _import(conn, a, other), b"y")
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
            conn.execute(
                "UPDATE transaction SET excluded_as_duplicate_of = %s WHERE id = %s", (foreign, tx)
            )


def test_statement_import_balance_checks(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        acc = _account(conn, a)
        bad = [
            {"balance_source": "user"},  # a source without a declared closing
            {"declared_closing": "100.00"},  # a declared closing with source none
            {"declared_opening": "1.00", "balance_source": "none"},
            {  # reconciled although the computed closing differs
                "status": "reconciled",
                "balance_source": "user",
                "declared_closing": "100.00",
                "computed_closing": "99.99",
            },
            {"status": "unreconciled", "balance_source": "user", "declared_closing": "1.00"},
            {"status": "failed"},  # failed without an error code
            {"error_code": "x"},  # an error code on an imported file
            {"lines_total": 2, "lines_new": 2, "lines_skipped": 1},
            {"first_booking_on": "2026-10-01"},  # first without last
            {"first_booking_on": "2026-10-31", "last_booking_on": "2026-10-01"},
            {"ai_call_id": _ai_call(conn, a, _document(conn, a))},  # only a PDF has an AI call
        ]
        for row in bad:
            with pytest.raises(errors.CheckViolation), conn.transaction():
                _import(conn, a, acc, **row)
        _import(
            conn,
            a,
            acc,
            status="reconciled",
            balance_source="user",
            declared_closing="2650.00",
            computed_closing="2650.00",
            lines_total=3,
            lines_new=1,
            lines_skipped=2,
            first_booking_on="2026-10-01",
            last_booking_on="2026-10-31",
        )
        _import(
            conn,
            a,
            acc,
            status="unreconciled",
            error_code="statement.unreconciled",
            balance_source="file",
            declared_opening="1250.00",
            declared_closing="2650.00",
            computed_closing="2640.00",
        )
        doc = _document(conn, a)
        _import(conn, a, acc, format="pdf", ai_call_id=_ai_call(conn, a, doc), document_id=doc)


def test_possible_duplicate_is_decided_once_per_pair(connect, workspaces):
    a, _ = workspaces
    with connect(APP, a) as conn:
        acc = _account(conn, a)
        t1 = _transaction(conn, a, acc, _import(conn, a, acc), b"one")
        t2 = _transaction(conn, a, acc, _import(conn, a, acc, format="camt053"), b"two")

        def pair(first, second, **row):
            return _insert(
                conn,
                "possible_duplicate",
                {"workspace_id": a, "transaction_id": first, "other_transaction_id": second} | row,
            )

        for row in (
            {"status": "merged"},  # decided without a time
            {"decided_at": "2026-11-06T10:00:00Z"},  # open with a decision time
        ):
            with pytest.raises(errors.CheckViolation), conn.transaction():
                pair(t1, t2, **row)
        with pytest.raises(errors.CheckViolation), conn.transaction():
            pair(t1, t1)
        dup = pair(t1, t2)
        with pytest.raises(errors.UniqueViolation), conn.transaction():
            pair(t1, t2)
        conn.execute(
            "UPDATE possible_duplicate SET status = 'merged', decided_at = now() WHERE id = %s",
            (dup,),
        )


# --- event.entity_type: all of phase 2 at once ---------------------------------------


@pytest.mark.parametrize(
    "event_type",
    [
        "user.signed_in",
        "proposal.applied",
        "account.created",
        "statement.imported",
        "transaction.allocated",
        "rule.created",
        "workspace.settings_changed",
    ],
)
def test_phase_2_events_are_accepted(connect, workspaces, event_type):
    from db.db_support import insert_event

    a, _ = workspaces
    with connect(APP, a) as conn:
        insert_event(conn, a, type=event_type, entity_type=event_type.split(".")[0])


# --- Procrastinate (D20) ------------------------------------------------------------


def test_vendored_procrastinate_schema_is_the_installed_packages():
    installed = importlib.resources.files("procrastinate.sql") / "schema.sql"
    assert VENDORED.read_text(encoding="utf-8") == installed.read_text(encoding="utf-8"), (
        "procrastinate was upgraded: add a migration with its upgrade SQL and a new vendored "
        "copy; never edit 0005"
    )


def test_procrastinate_tables_are_the_only_ones_without_rls(connect):
    with connect(OWNER) as conn:
        rows = conn.execute("""
            SELECT c.relname FROM pg_class c
            WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
              AND NOT c.relrowsecurity AND c.relname <> 'alembic_version'
        """).fetchall()
    assert {r[0] for r in rows} == PROCRASTINATE_TABLES


def test_no_function_in_the_schema_is_security_definer(connect):
    """EXECUTE on procrastinate's functions must not reach past RLS or grants."""
    with connect(OWNER) as conn:
        rows = conn.execute("""
            SELECT p.proname FROM pg_proc p
            WHERE p.pronamespace = 'public'::regnamespace AND p.prosecdef
        """).fetchall()
        functions = conn.execute("""
            SELECT count(*) FROM pg_proc p
            WHERE p.pronamespace = 'public'::regnamespace AND p.proname LIKE 'procrastinate\\_%'
        """).fetchone()[0]
    assert rows == []
    assert functions > 10


def test_app_role_may_use_the_queue_and_nothing_more_on_it(connect):
    with connect(OWNER) as conn:
        for table in sorted(PROCRASTINATE_TABLES):
            assert _granted(conn, table) == {"SELECT", "INSERT", "UPDATE", "DELETE"}, table
        not_executable = conn.execute(
            """
            SELECT p.proname FROM pg_proc p
            WHERE p.pronamespace = 'public'::regnamespace AND p.proname LIKE 'procrastinate\\_%%'
              AND NOT has_function_privilege(%s, p.oid, 'EXECUTE')
            """,
            (APP,),
        ).fetchall()
    assert not_executable == []


def test_app_role_defers_fetches_and_finishes_a_job(connect):
    """The worker's own calls, as hausbuch_app (no workspace needed: the queue is not RLS'd)."""
    with connect(APP) as conn:
        (job_ids,) = conn.execute("""
            SELECT procrastinate_defer_jobs_v1(ARRAY[ROW(
                'default', 'document_read', 0, NULL, 'doc:1',
                '{"workspace_id": "w"}'::jsonb, NULL)::procrastinate_job_to_defer_v1])
        """).fetchone()
        (worker,) = conn.execute(
            "SELECT worker_id FROM procrastinate_register_worker_v1()"
        ).fetchone()
        job = conn.execute(
            "SELECT id, status FROM procrastinate_fetch_job_v2(ARRAY['default'], %s)", (worker,)
        ).fetchone()
        assert job == (job_ids[0], "doing")
        conn.execute("SELECT procrastinate_finish_job_v1(%s, 'succeeded', false)", (job[0],))
        conn.execute("SELECT procrastinate_unregister_worker_v1(%s)", (worker,))
