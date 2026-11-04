import io
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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
            == "0002_shared_vivo_uri"
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
                == "0002_shared_vivo_uri"
            )
    finally:
        engine.dispose()
        with pg_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_shared_vivo_uri_migration_preserves_data_and_guards_downgrade(pg_engine):
    schema = "migration_" + uuid4().hex
    with pg_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        pg_engine.url,
        connect_args={"options": f"-csearch_path={schema}"},
        hide_parameters=True,
    )
    config = Config(str(ROOT / "alembic.ini"), stdout=io.StringIO())
    uri = "http://vivoweb.org/ontology/core#Department"
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0001_v1")
        with engine.begin() as connection:
            person_id = connection.scalar(
                text(
                    "INSERT INTO person(first_name,last_name) VALUES('Synthetic','Example') RETURNING person_id"
                )
            )
            org_id = connection.scalar(
                text(
                    "INSERT INTO organization(org_name,org_vivo_uri) VALUES('Institute A',:uri) RETURNING org_id"
                ),
                {"uri": uri},
            )
            job_id = connection.scalar(
                text(
                    "INSERT INTO job(person_id,org_id,job_type) VALUES(:person,:org,'Permanent') RETURNING job_id"
                ),
                {"person": person_id, "org": org_id},
            )
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.check(config)
            assert (
                connection.scalar(
                    text("SELECT org_vivo_uri FROM organization WHERE org_id=:id"),
                    {"id": org_id},
                )
                == uri
            )
            assert connection.execute(
                text("SELECT person_id,org_id FROM job WHERE job_id=:id"),
                {"id": job_id},
            ).one() == (person_id, org_id)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organization(org_name,org_vivo_uri) VALUES('Institute B',:uri)"
                ),
                {"uri": uri},
            )
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            with pytest.raises(IntegrityError):
                command.downgrade(config, "0001_v1")
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0002_shared_vivo_uri"
            )
            assert connection.scalar(text("SELECT count(*) FROM organization")) == 2
            connection.rollback()
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM organization WHERE org_name='Institute B'")
            )
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0001_v1")
            assert (
                connection.scalar(
                    text("SELECT org_vivo_uri FROM organization WHERE org_id=:id"),
                    {"id": org_id},
                )
                == uri
            )
            assert connection.scalar(text("SELECT count(*) FROM job")) == 1
            connection.rollback()
            command.upgrade(config, "head")
            command.check(config)
    finally:
        engine.dispose()
        with pg_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
