"""Schema-aware readiness support: compare the database's Alembic revision
state with the head(s) of the migration tree packaged alongside this code
(repo checkout or /srv inside the API image). Never runs migrations and never
exposes revision IDs to callers — the readiness surface reports only the
stable category 'schema'.
"""

from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine


def _migrations_dir() -> Path:
    here = Path(__file__).resolve()
    # here.parents: [app/db, app, <api-root>, ...]; the migration tree sits
    # next to the app package in the image (/srv/migrations) and at the
    # repository root in a checkout.
    candidates = [here.parents[2] / "migrations"]
    if len(here.parents) > 4:
        candidates.append(here.parents[4] / "migrations")
    for candidate in candidates:
        if (candidate / "env.py").exists():
            return candidate
    raise RuntimeError("packaged migration tree not found")


@lru_cache
def packaged_heads() -> frozenset[str]:
    cfg = Config()
    cfg.set_main_option("script_location", str(_migrations_dir()))
    return frozenset(ScriptDirectory.from_config(cfg).get_heads())


def database_matches_packaged_head(engine: Engine) -> bool:
    """True only when alembic_version exists and matches the packaged head(s)
    exactly. Any query failure after a live connection means missing/foreign
    schema and returns False (the caller reports the 'schema' category)."""
    with engine.connect() as conn:
        try:
            rows = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all()
        except sa.exc.DatabaseError:
            return False
    return bool(rows) and set(rows) == set(packaged_heads())
