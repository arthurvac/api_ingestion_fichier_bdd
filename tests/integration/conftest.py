import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import get_session
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def pg_engine():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "TEST_DATABASE_URL absent : lancer compose.test.yaml pour PostgreSQL isolé."
        )
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+psycopg"
        or url.database != "ingestion_test"
        or url.username != "ingestion_test"
    ):
        pytest.fail(
            "Tests refusés : base et utilisateur ingestion_test obligatoires.",
            pytrace=False,
        )
    admin = create_engine(url, hide_parameters=True)
    schema = "test_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}"}, hide_parameters=True
    )
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def session_factory(pg_engine):
    from app.models import Base

    tables = ", ".join('"' + t.name + '"' for t in Base.metadata.sorted_tables)
    with pg_engine.begin() as connection:
        connection.execute(text("TRUNCATE " + tables + " RESTART IDENTITY CASCADE"))
    return sessionmaker(pg_engine, expire_on_commit=False)


@pytest.fixture
def api_client(session_factory):
    app = create_app()

    def session_dependency():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_dependency
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
