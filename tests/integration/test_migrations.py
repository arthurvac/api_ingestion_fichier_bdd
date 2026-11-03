import io
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
TABLES = {
    "person",
    "organization",
    "job",
    "import_batch",
    "person_history",
    "alembic_version",
}


def test_v1_schema_matches_models_and_contains_only_active_tables(pg_engine):
    config = Config(str(ROOT / "alembic.ini"), stdout=io.StringIO())
    with pg_engine.connect() as connection:
        assert set(inspect(connection).get_table_names()) == TABLES
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0001_v1"
        )
        connection.rollback()
        config.attributes["connection"] = connection
        command.check(config)


def test_initial_upgrade_is_idempotent_and_downgrade_recreates_empty_database(
    pg_engine,
):
    schema = "migration_" + uuid4().hex
    with pg_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        pg_engine.url,
        connect_args={"options": f"-csearch_path={schema}"},
        hide_parameters=True,
    )
    config = Config(str(ROOT / "alembic.ini"), stdout=io.StringIO())
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with engine.begin() as connection:
            assert set(inspect(connection).get_table_names()) == TABLES
            connection.execute(
                text(
                    "INSERT INTO person(first_name,last_name) VALUES('Synthetic','Example')"
                )
            )
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.check(config)
            assert connection.scalar(text("SELECT count(*) FROM person")) == 1
            connection.rollback()
            command.downgrade(config, "base")
            assert set(inspect(connection).get_table_names()) == {"alembic_version"}
            assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
            connection.rollback()
            command.upgrade(config, "head")
            assert set(inspect(connection).get_table_names()) == TABLES
            assert connection.scalar(text("SELECT count(*) FROM person")) == 0
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0001_v1"
            )
    finally:
        engine.dispose()
        with pg_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
