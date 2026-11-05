"""Alembic environment. Migrations are hand-written SQL; there is no ORM metadata to diff.

Run as the schema-owner role (not a superuser): RLS is FORCEd, and superusers bypass it.
"""

import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("HAUSBUCH_DATABASE_URL")
    if not url:
        raise RuntimeError("set HAUSBUCH_DATABASE_URL, e.g. postgresql+psycopg://user:pw@host/db")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Tests pass an open connection (already SET ROLE to the schema owner).
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
