"""Alembic environment — wired to the API service's declarative metadata.

The database URL comes exclusively from the DATABASE_URL environment variable
(Compose locally, the task definition in AWS); it is never committed and never
logged by this module.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
# Repo checkout: app lives at services/api/app. API image: app lives at /srv/app.
for _candidate in (REPO_ROOT / "services" / "api", REPO_ROOT):
    if (_candidate / "app").is_dir():
        sys.path.insert(0, str(_candidate))
        break

# These imports intentionally follow runtime path selection, which static
# import sorting cannot infer consistently. Keep their side-effect order explicit.
# isort: off
from app.db.base import Base  # noqa: E402
from app import models  # noqa: E402,F401  (imports register the tables)
# isort: on

config = context.config
if config.config_file_name is not None:
    # Never disable application loggers when migrations run in-process
    # (tests, the compose migrate gate): keep existing loggers alive.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    # Explicit config wins (test fixtures pass the guarded disposable-DB URL
    # this way); the environment is the deployment path.
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set for Alembic operations")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
