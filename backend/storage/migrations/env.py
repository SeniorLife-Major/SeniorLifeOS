"""Alembic environment: runs migrations with psycopg against DATABASE_URL (or -x url=...)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend.storage.models import Base, vault_tables_only
from backend.storage.settings import database_url, with_driver

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def url() -> str:
    given = context.get_x_argument(as_dictionary=True).get("url")
    return with_driver(given or database_url(), "psycopg")


def run_migrations_offline() -> None:
    context.configure(url=url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, include_name=vault_tables_only)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")  # tests may pass one in
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=target_metadata, include_name=vault_tables_only)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url(), poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_name=vault_tables_only)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
