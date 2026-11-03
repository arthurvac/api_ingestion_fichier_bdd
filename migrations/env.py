from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        apply_migrations(supplied_connection)
        return
    engine = create_engine(
        get_settings().database_url, poolclass=NullPool, hide_parameters=True
    )
    with engine.connect() as connection:
        apply_migrations(connection)
    engine.dispose()


def apply_migrations(connection):
    # Verrou de session distinct de celui des imports, commun à tous les lanceurs Alembic.
    connection.execute(text("SELECT pg_advisory_lock(728194022)"))
    connection.commit()
    try:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if connection.in_transaction():
            connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(728194022)"))
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
