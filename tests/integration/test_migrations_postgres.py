import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from ads_growth_agent.persistence.schema import CORE_TABLES

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_alembic_upgrade_and_downgrade_against_live_postgres(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        config = Config("alembic.ini")

        command.upgrade(config, "head")
        _assert_schema_created(test_url)

        command.downgrade(config, "base")
        _assert_schema_downgraded(test_url)
    finally:
        _drop_temporary_database(test_url)


def _integration_database_url() -> URL:
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("Set RUN_POSTGRES_INTEGRATION=1 to run live PostgreSQL tests.")
    return make_url(os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))


def _create_temporary_database(base_url: URL) -> URL:
    database_name = f"ads_growth_test_{uuid4().hex[:12]}"
    admin_url = base_url.set(database="postgres")
    test_url = base_url.set(database=database_name)

    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()

    return test_url


def _drop_temporary_database(test_url: URL) -> None:
    database_name = test_url.database
    admin_url = test_url.set(database="postgres")
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        engine.dispose()


def _assert_schema_created(test_url: URL) -> None:
    engine = sa.create_engine(test_url)
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            table_names = set(inspector.get_table_names())
            expected_tables = {table.name for table in CORE_TABLES}

            assert expected_tables.issubset(table_names)
            assert _extensions(connection) >= {"pgcrypto", "vector"}
            assert _column_udt(connection, "knowledge_chunks", "embedding") == "vector"
            assert _column_udt(connection, "advertiser_memories", "embedding") == "vector"
            assert "ix_knowledge_chunks_embedding_cosine" in _index_names(
                connection, "knowledge_chunks"
            )
            assert "ix_advertiser_memories_embedding_cosine" in _index_names(
                connection, "advertiser_memories"
            )
            assert "ix_knowledge_chunks_content_fts" in _index_names(
                connection, "knowledge_chunks"
            )
    finally:
        engine.dispose()


def _assert_schema_downgraded(test_url: URL) -> None:
    engine = sa.create_engine(test_url)
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            table_names = set(inspector.get_table_names())

            assert not ({table.name for table in CORE_TABLES} & table_names)
    finally:
        engine.dispose()


def _extensions(connection: sa.Connection) -> set[str]:
    return set(
        connection.execute(
            sa.text("SELECT extname FROM pg_extension WHERE extname in ('pgcrypto', 'vector')")
        ).scalars()
    )


def _column_udt(connection: sa.Connection, table_name: str, column_name: str) -> str:
    return connection.execute(
        sa.text(
            "SELECT udt_name "
            "FROM information_schema.columns "
            "WHERE table_name = :table_name AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar_one()


def _index_names(connection: sa.Connection, table_name: str) -> set[str]:
    return set(
        connection.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename = :table_name"),
            {"table_name": table_name},
        ).scalars()
    )
