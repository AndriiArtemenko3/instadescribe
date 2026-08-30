"""Worker database lifecycle — canonical models/state come from the single
repository source (`app.models` / `app.domain`); no divergent schema copy."""

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from instadescribe_worker.config import get_worker_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_worker_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def reset_db_caches() -> None:
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
