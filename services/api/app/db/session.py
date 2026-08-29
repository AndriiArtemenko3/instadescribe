"""Synchronous engine + session lifecycle for FastAPI.

The engine is created lazily (never at import) so the process boots — and
liveness stays green — with the database absent or misconfigured. Pool
pre-ping keeps long-lived pool connections honest; the short connect timeout
keeps readiness probes fast when PostgreSQL is down.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 2},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def reset_engine_caches() -> None:
    """Test hook: drop cached engine/sessionmaker after env changes."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
