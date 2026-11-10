"""Migration 0018 (docs/phases/phase-4b.md §6): return_input and questionnaire_answer (append-only,
scope per kind and per key), the two pay document types, tax_input private_cost with its
carried_from_id, tax_export per owner, the grants and the downgrade guard.

RLS, workspace_id and the table list are covered for every table by test_migration.py; the
enum CHECKs (return_input.kind, questionnaire_answer.answer, document.type, the extraction and
proposal kinds, tax_input.kind, tax_export.kind) by test_schema_consistency.py."""

import hashlib
import uuid

import pytest
from db.db_support import (
    APP,
    OWNER,
    _admin,
    _insert,
    alembic,
    create_database,
    drop_database,
    insert_property,
)
from psycopg import errors
from psycopg.types.json import Jsonb
from sqlalchemy.exc import DBAPIError

from hausbuch.schemas.employment import QuestionnaireAnswerCreate, ReturnInputCreate
from hausbuch.schemas.tax import TaxInputCreate

PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
EXPECTED_GRANTS = {
    "return_input": {"SELECT", "INSERT"},  # append-only
    "questionnaire_answer": {"SELECT", "INSERT"},  # append-only
}
HEAD = "0018_income_tax_return"
PREVIOUS = "0017_electricity_category"


@pytest.mark.parametrize("table", sorted(EXPECTED_GRANTS))
def test_app_role_grants(connect, table):
    with connect(OWNER) as conn:
        granted = {
            p
            for p in PRIVILEGES
            if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (APP, table, p)).fetchone()[0]
        }
        assert granted == EXPECTED_GRANTS[table]


# --- Seed helpers ----------------------------------------------------------------------


def _document(conn, ws, **overrides) -> uuid.UUID:
    digest = hashlib.sha256(uuid.uuid4().bytes).digest()
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 11, 'application/pdf', %s)",
        (ws, digest, f"{ws}/sha256/{digest.hex()}"),
    )
    row = {"workspace_id": ws, "file_sha256": digest, "source": "upload"} | overrides
    return _insert(conn, "document", row)


def _owner(conn, ws) -> uuid.UUID:
    return _insert(conn, "owner", {"workspace_id": ws, "full_name": "Eigentümer A"})


@pytest.fixture
def scope(connect, workspaces):
    """(conn, workspace, ids) as the app role, rolled back after the test."""
    a, _ = workspaces
    with connect(APP, a) as conn:
        ids = {
            "owner": _owner(conn, a),
            "document": _document(conn, a, type="lohnsteuerbescheinigung"),
            "property": insert_property(conn, a),
        }
        yield conn, a, ids


# One valid row per tested kind: its scope ids and its value (synthetic figures).
VALID = {
    "wage_certificate": (
        {"owner", "document"},
        {
            "period_from": "2026-01-01",
            "period_to": "2026-12-31",
            "employer": "Arbeitgeber A",
            "tax_class": 4,
            "gross": "48000.00",
            "income_tax": "6100.00",
        },
    ),
    "commute": (
        {"owner"},
        {
            "kind": "first_workplace",
            "workplace_label": "Büro A",
            "period_from": "2026-01-01",
            "period_to": "2026-12-31",
            "days_per_week": 3,
            "days": 130,
            "km": 25,
            "km_car": 25,
        },
    ),
    "telecom": ({"owner"}, {"bills_year": "480.00"}),
    "child": (set(), {"born_on": "2026-06-14"}),
    "special_expense": (
        set(),
        {"expense_class": "donation", "amount": "300.00", "recipient_label": "Verein X"},
    ),
}


def _valid(ws, ids, kind, **overrides) -> dict:
    keys, value = VALID[kind]
    row = {"workspace_id": ws, "kind": kind, "tax_year": 2026, "value": Jsonb(value)}
    return row | {f"{k}_id": ids[k] for k in keys} | overrides


def _as_schema(row: dict) -> dict:
    return {k: v.obj if isinstance(v, Jsonb) else v for k, v in row.items() if k != "workspace_id"}


@pytest.mark.parametrize("kind", sorted(VALID))
def test_each_kind_with_its_scope_is_accepted_by_the_table_and_the_schema(scope, kind):
    conn, a, ids = scope
    row = _valid(a, ids, kind)
    _insert(conn, "return_input", row)
    ReturnInputCreate.model_validate(_as_schema(row))


@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("telecom", {"owner_id": None}),  # an owner kind without its owner
        ("commute", {"owner_id": None}),
        ("child", {"owner": True}),  # a household kind with an owner
        ("special_expense", {"owner": True}),
        ("wage_certificate", {"document_id": None}),  # the certificate is required
        ("telecom", {"tax_year": 1999}),
        ("telecom", {"withdrawn": True}),  # withdrawn without supersedes_id
        ("telecom", {"kind": "lottery_win"}),
    ],
)
def test_a_row_outside_its_kinds_scope_is_refused_by_the_table_and_the_schema(scope, kind, change):
    conn, a, ids = scope
    row = _valid(a, ids, kind)
    if change.pop("owner", False):
        row["owner_id"] = ids["owner"]
    row |= change
    with pytest.raises((errors.CheckViolation, errors.NotNullViolation)), conn.transaction():
        _insert(conn, "return_input", row)
    with pytest.raises(ValueError):
        ReturnInputCreate.model_validate(_as_schema(row))


def test_value_must_be_a_json_object(scope):
    conn, a, ids = scope
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "return_input", _valid(a, ids, "telecom", value=Jsonb(["480.00"])))


def test_return_input_is_append_only_even_for_the_owner(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        first = _insert(conn, "return_input", _valid(a, {}, "child"))
        for statement in (
            "UPDATE return_input SET note = 'x' WHERE id = %s",
            "UPDATE return_input SET withdrawn = true WHERE id = %s",
            "DELETE FROM return_input WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
                conn.execute(statement, (first,))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("TRUNCATE return_input CASCADE")


def test_a_correction_supersedes_one_row_of_the_same_kind_and_year(scope):
    conn, a, ids = scope
    first = _insert(conn, "return_input", _valid(a, ids, "telecom"))
    _insert(conn, "return_input", _valid(a, ids, "telecom", supersedes_id=first, withdrawn=True))
    with pytest.raises(errors.UniqueViolation), conn.transaction():  # superseded once only
        _insert(conn, "return_input", _valid(a, ids, "telecom", supersedes_id=first))
    other = _insert(conn, "return_input", _valid(a, ids, "commute"))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another kind
        _insert(conn, "return_input", _valid(a, ids, "telecom", supersedes_id=other))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another year
        _insert(conn, "return_input", _valid(a, ids, "commute", tax_year=2027, supersedes_id=other))
    with pytest.raises(errors.CheckViolation), conn.transaction():  # not itself
        own = uuid.uuid4()
        _insert(conn, "return_input", _valid(a, ids, "telecom", id=own, supersedes_id=own))


def test_carried_from_names_a_row_of_the_same_kind_and_never_a_one_off_kind(scope):
    conn, a, ids = scope
    last_year = _insert(conn, "return_input", _valid(a, ids, "telecom", tax_year=2025))
    _insert(conn, "return_input", _valid(a, ids, "telecom", carried_from_id=last_year))
    commute = _insert(conn, "return_input", _valid(a, ids, "commute", tax_year=2025))
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another kind
        _insert(conn, "return_input", _valid(a, ids, "telecom", carried_from_id=commute))
    child = _insert(conn, "return_input", _valid(a, ids, "child", tax_year=2025))
    row = _valid(a, ids, "child", carried_from_id=child)
    with pytest.raises(errors.CheckViolation), conn.transaction():  # a child is never carried
        _insert(conn, "return_input", row)
    with pytest.raises(ValueError):
        ReturnInputCreate.model_validate(_as_schema(row))
    cert = _insert(conn, "return_input", _valid(a, ids, "wage_certificate", tax_year=2025))
    with pytest.raises(errors.CheckViolation), conn.transaction():  # nor a certificate
        _insert(conn, "return_input", _valid(a, ids, "wage_certificate", carried_from_id=cert))


# --- questionnaire_answer --------------------------------------------------------------


def _answer(ws, ids, key="owner.commute_changed", **overrides) -> dict:
    row = {"workspace_id": ws, "question_key": key, "tax_year": 2026, "answer": "yes"}
    if key.startswith("owner."):
        row["owner_id"] = ids["owner"]
    elif key.startswith("property."):
        row["property_id"] = ids["property"]
    return row | overrides


@pytest.mark.parametrize(
    "key", ["owner.commute_changed", "household.new_child", "property.private_costs"]
)
def test_an_answer_with_its_scope_is_accepted_by_the_table_and_the_schema(scope, key):
    conn, a, ids = scope
    row = _answer(a, ids, key, choice="changed")
    _insert(conn, "questionnaire_answer", row)
    QuestionnaireAnswerCreate.model_validate(_as_schema(row))


@pytest.mark.parametrize(
    ("key", "change"),
    [
        ("owner.commute_changed", {"owner_id": None}),  # the owner is required
        ("household.new_child", {"owner": True}),  # a household key has neither id
        ("household.new_child", {"property": True}),
        ("property.private_costs", {"property_id": None}),
        ("owner.commute_changed", {"property": True}),  # never both
        ("owner.commute_changed", {"answer": "maybe"}),
        ("owner.commute_changed", {"choice": "Freitext mit Leerzeichen"}),  # coded only
        ("owner.commute_changed", {"question_key": "owner.Commute"}),
        ("household.new_child", {"question_key": "tenant.new_child"}),
        ("owner.commute_changed", {"withdrawn": True}),
    ],
)
def test_an_answer_outside_its_scope_is_refused_by_the_table_and_the_schema(scope, key, change):
    conn, a, ids = scope
    row = _answer(a, ids, key)
    if change.pop("owner", False):
        row["owner_id"] = ids["owner"]
    if change.pop("property", False):
        row["property_id"] = ids["property"]
    row |= change
    with pytest.raises(errors.CheckViolation), conn.transaction():
        _insert(conn, "questionnaire_answer", row)
    with pytest.raises(ValueError):
        QuestionnaireAnswerCreate.model_validate(_as_schema(row))


def test_an_answer_is_corrected_by_a_new_row_for_the_same_key_and_year(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        ids = {"owner": _owner(conn, a)}
        first = _insert(conn, "questionnaire_answer", _answer(a, ids))
        _insert(conn, "questionnaire_answer", _answer(a, ids, answer="no", supersedes_id=first))
        other = _insert(conn, "questionnaire_answer", _answer(a, ids, "household.new_child"))
        with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another key
            _insert(conn, "questionnaire_answer", _answer(a, ids, supersedes_id=other))
        last = _insert(conn, "questionnaire_answer", _answer(a, ids, tax_year=2025))
        _insert(conn, "questionnaire_answer", _answer(a, ids, tax_year=2027, carried_from_id=last))
        for statement in (
            "UPDATE questionnaire_answer SET answer = 'no' WHERE id = %s",
            "DELETE FROM questionnaire_answer WHERE id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
                conn.execute(statement, (first,))
        with pytest.raises(errors.RestrictViolation, match="append-only"), conn.transaction():
            conn.execute("TRUNCATE questionnaire_answer CASCADE")


# --- tax_input private_cost, tax_export per owner, the pay documents ---------------------

PRIVATE_COST = {"cost_class": "postage", "label": "Porto Mieterpost", "amount": "12.00"}


def _private_cost(ws, prop, **overrides) -> dict:
    row = {
        "workspace_id": ws,
        "property_id": prop,
        "kind": "private_cost",
        "tax_year": 2026,
        "value": Jsonb(PRIVATE_COST),
    }
    return row | overrides


def test_a_private_cost_is_year_scoped_and_may_be_carried_from_last_year(scope):
    conn, a, ids = scope
    row = _private_cost(a, ids["property"], tax_year=2025)
    last = _insert(conn, "tax_input", row)
    TaxInputCreate.model_validate(_as_schema(row))
    _insert(conn, "tax_input", _private_cost(a, ids["property"], carried_from_id=last))
    with pytest.raises(errors.CheckViolation), conn.transaction():  # every private cost has a year
        _insert(conn, "tax_input", _private_cost(a, ids["property"], tax_year=None))
    other = insert_property(conn, a, label="Zweite Wohnung")
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():  # another property
        _insert(conn, "tax_input", _private_cost(a, other, carried_from_id=last))
    market = _insert(
        conn,
        "tax_input",
        {
            "workspace_id": a,
            "property_id": ids["property"],
            "kind": "market_rent",
            "value": Jsonb({"rent_per_m2": "10.00"}),
        },
    )
    with pytest.raises(errors.CheckViolation), conn.transaction():  # only private_cost carries
        _insert(
            conn,
            "tax_input",
            {
                "workspace_id": a,
                "property_id": ids["property"],
                "kind": "market_rent",
                "value": Jsonb({"rent_per_m2": "11.00"}),
                "carried_from_id": market,
            },
        )


def _export(ws, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "tax_year": 2026,
        "kind": "anlage_n_sheet",
        "rule_set_version": "2026.1",
        "form_year": 2025,
        "form_status": "provisional",
        "draft": Jsonb({}),
        "file_sha256": hashlib.sha256(b"x").digest(),
        "size_bytes": 1,
    } | overrides


def _export_file(conn, ws) -> None:
    conn.execute(
        "INSERT INTO document_file (workspace_id, sha256, size_bytes, media_type, storage_key)"
        " VALUES (%s, %s, 1, 'application/pdf', 'k')",
        (ws, hashlib.sha256(b"x").digest()),
    )


def test_a_tax_export_belongs_to_a_property_or_an_owner_never_both(connect, workspaces):
    a, _ = workspaces
    with connect(OWNER, a) as conn:
        owner, prop = _owner(conn, a), insert_property(conn, a)
        _export_file(conn, a)
        _insert(conn, "tax_export", _export(a, owner_id=owner))
        _insert(conn, "tax_export", _export(a, kind="field_sheet", property_id=prop))
        for bad in (
            _export(a),  # neither
            _export(a, owner_id=owner, property_id=prop),  # both
            _export(a, kind="field_sheet", owner_id=owner),  # an owner's export is Anlage N
            _export(a, property_id=prop),  # an Anlage N sheet is an owner's
        ):
            with pytest.raises(errors.CheckViolation), conn.transaction():
                _insert(conn, "tax_export", bad)


@pytest.mark.parametrize("doc_type", ["lohnsteuerbescheinigung", "gehaltsabrechnung"])
def test_the_pay_documents_are_document_types(scope, doc_type):
    conn, a, _ = scope
    _document(conn, a, type=doc_type)


# --- The downgrade guard ----------------------------------------------------------------


def _seed(conn, ws, data):
    owner = _owner(conn, ws)
    if data == "return_input":
        _insert(conn, "return_input", _valid(ws, {}, "child"))
    elif data == "questionnaire_answer":
        _insert(conn, "questionnaire_answer", _answer(ws, {"owner": owner}))
    elif data == "private_cost":
        _insert(conn, "tax_input", _private_cost(ws, insert_property(conn, ws)))
    elif data == "anlage_n_export":
        _export_file(conn, ws)
        _insert(conn, "tax_export", _export(ws, owner_id=owner))
    elif data == "document":
        return _document(conn, ws, type="gehaltsabrechnung")
    return None


@pytest.mark.parametrize(
    "data", ["return_input", "questionnaire_answer", "private_cost", "anlage_n_export", "document"]
)
def test_0018_downgrade_refuses_while_it_would_drop_data(data):
    """A downgrade never drops data; the guard must see rows under forced RLS. A pay document
    is not append-only, so retyping it lets the downgrade through, which then restores every
    earlier CHECK and the NOT NULL on tax_export.property_id."""
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = uuid.uuid4()
        with _admin(name) as conn:
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'A')", (ws,))
            conn.execute(f"SET ROLE {OWNER}")
            conn.execute("SELECT set_config('app.workspace_id', %s, false)", (str(ws),))
            document = _seed(conn, ws, data)
        with pytest.raises(DBAPIError, match=f"downgrade of {HEAD} refused"):
            alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
            for table in (
                "return_input",
                "questionnaire_answer",
                "tax_input",
                "tax_export",
                "document",
            ):
                forced = conn.execute(
                    "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)
                ).fetchone()
                assert forced == (True,)  # the refused downgrade's NO FORCE was rolled back
            if document is not None:
                conn.execute("UPDATE document SET type = 'other' WHERE id = %s", (document,))
        if document is not None:
            alembic(name, "downgrade", PREVIOUS)
            with _admin(name) as conn:
                tables = conn.execute(
                    "SELECT count(*) FROM pg_tables"
                    " WHERE tablename IN ('return_input', 'questionnaire_answer')"
                ).fetchone()
                assert tables == (0,)
                nullable = conn.execute(
                    "SELECT is_nullable FROM information_schema.columns"
                    " WHERE table_name = 'tax_export' AND column_name = 'property_id'"
                ).fetchone()
                assert nullable == ("NO",)
                with pytest.raises(errors.CheckViolation):
                    conn.execute(
                        "UPDATE document SET type = 'lohnsteuerbescheinigung' WHERE id = %s",
                        (document,),
                    )
            alembic(name, "upgrade", "head")
    finally:
        drop_database(name)
