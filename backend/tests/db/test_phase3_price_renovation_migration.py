"""Migration 0054 (docs/phases/phase-3.md D287): the tax input kind `price_renovation`, per
property (no tax_year) and with its document (the Kaufvertrag) required, and the downgrade that
refuses while such a row exists. The kind CHECK against TaxInputKind is
test_schema_consistency.py's. Invented values only."""

import pytest
from db.db_support import _admin, _insert, alembic, create_database, drop_database, insert_property
from db.test_phase5_migration import (  # noqa: F401 - scope is a fixture
    _as_owner,
    _document,
    _new_workspace,
    scope,
)
from db.test_phase6_migration import _forced
from psycopg import errors
from psycopg.types.json import Jsonb

from hausbuch.schemas.tax import TaxInputCreate

HEAD = "0054_price_renovation"
PREVIOUS = "0053_spot_price"
KIND = "price_renovation"

VALUE = {
    "status": "claimed",
    "amount": "11900.00",
    "amount_net": "10000.00",
    "period_start": "2026-07-01",
    "period_end": "2026-09-30",
    "paid_on": "2026-06-15",
    "clause": "Der Verkäufer renoviert die Wohnung nach Übergabe auf eigene Kosten.",
}


def _row(ws, prop, document, **overrides) -> dict:
    return {
        "workspace_id": ws,
        "property_id": prop,
        "kind": KIND,
        "document_id": document,
        "value": Jsonb(VALUE),
    } | overrides


def test_a_price_renovation_is_per_property_and_needs_the_kaufvertrag(scope):  # noqa: F811
    conn, a, _ = scope
    prop = insert_property(conn, a)
    document = _document(conn, a)
    row = _row(a, prop, document)
    _insert(conn, "tax_input", row)
    TaxInputCreate.model_validate(
        {k: v.obj if isinstance(v, Jsonb) else v for k, v in row.items() if k != "workspace_id"}
    )
    for bad in (_row(a, prop, document, tax_year=2026), _row(a, prop, None)):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            _insert(conn, "tax_input", bad)


def _checks(conn) -> str:
    rows = conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conrelid = 'tax_input'::regclass AND contype = 'c' ORDER BY conname"
    )
    return " ".join(r[0] for r in rows)


def test_0054_runs_up_down_and_up_on_an_empty_database():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        with _admin(name) as conn:
            assert KIND in _checks(conn)
        alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            checks = _checks(conn)
            assert KIND not in checks and "grundsteuer_schedule" in checks
        alembic(name, "upgrade", HEAD)
    finally:
        drop_database(name)


def test_0054_downgrade_refuses_while_a_price_renovation_exists_and_keeps_rls_forced():
    name = create_database()
    try:
        alembic(name, "upgrade", HEAD)
        ws = _new_workspace(name)
        with _admin(name) as conn:
            _as_owner(conn, ws)
            _insert(conn, "tax_input", _row(ws, insert_property(conn, ws), _document(conn, ws)))
            forced = _forced(conn)
        with pytest.raises(Exception, match=f"downgrade of {HEAD} refused"):
            alembic(name, "downgrade", PREVIOUS)
        with _admin(name) as conn:
            assert _forced(conn) == forced and "tax_input" in forced
            assert KIND in _checks(conn)
    finally:
        drop_database(name)
