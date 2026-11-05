"""hausbuch.db.tables mirrors the migrated schema: same tables, columns, types, nullability,
server defaults and primary keys. The migration is the source of truth."""

import pytest
from conftest import OWNER, make_engine
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from hausbuch.db.tables import metadata

DIALECT = postgresql.dialect()


def _sql_type(type_) -> str:
    return type_.compile(dialect=DIALECT)


@pytest.fixture(scope="module")
def reflected(dbname):
    engine = make_engine(dbname, OWNER)
    try:
        with engine.connect() as conn:
            insp = inspect(conn)
            names = set(insp.get_table_names()) - {"alembic_version"}
            yield {
                name: (
                    {c["name"]: c for c in insp.get_columns(name)},
                    insp.get_pk_constraint(name)["constrained_columns"],
                )
                for name in names
            }
    finally:
        engine.dispose()


def test_same_tables(reflected):
    assert set(metadata.tables) == set(reflected)
    assert len(metadata.tables) == 18


@pytest.mark.parametrize("table", sorted(metadata.tables))
def test_columns_match(reflected, table):
    db_columns, db_pk = reflected[table]
    defined = metadata.tables[table]

    assert [c.name for c in defined.columns] == list(db_columns), "column set or order differs"
    for column in defined.columns:
        db = db_columns[column.name]
        where = f"{table}.{column.name}"
        assert _sql_type(column.type) == _sql_type(db["type"]), where
        assert column.nullable == db["nullable"], where
        has_default = column.server_default is not None or column.identity is not None
        db_has_default = db["default"] is not None or bool(db.get("identity"))
        assert has_default == db_has_default, where
    assert [c.name for c in defined.primary_key.columns] == db_pk
