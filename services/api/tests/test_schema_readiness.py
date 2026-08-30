"""Schema-aware readiness (Part A2): connectivity alone is no longer enough —
the database must sit at the exact packaged Alembic head."""

import os

import pytest
from app.core.config import get_settings
from app.db.schema_check import packaged_heads
from app.db.session import reset_engine_caches
from app.main import app
from fastapi.testclient import TestClient

requires_db = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_caches():
    yield
    get_settings.cache_clear()
    reset_engine_caches()


def _point_at(monkeypatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    reset_engine_caches()


def test_packaged_migration_tree_is_discoverable_and_single_headed():
    heads = packaged_heads()
    assert len(heads) == 1
    # The comparison uses the tree packaged next to the app code.


@requires_db
def test_ready_at_head_then_schema_503_when_behind_then_recovers(
    monkeypatch, migrated_db, alembic_config
):
    from alembic import command

    _point_at(monkeypatch, migrated_db)
    assert client.get("/readyz").status_code == 200

    # Schema behind head: connectivity fine, readiness must fail with the
    # stable 'schema' category while liveness stays 200.
    command.downgrade(alembic_config, "-1")
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "checks": ["schema"]}
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/readyz").json() == r.json()

    # Schema absent entirely.
    command.downgrade(alembic_config, "base")
    r = client.get("/readyz")
    assert r.status_code == 503
    assert "schema" in r.json()["checks"]
    assert client.get("/healthz").status_code == 200

    # Recovery at exact packaged head.
    command.upgrade(alembic_config, "head")
    assert client.get("/readyz").status_code == 200
    # No revision IDs, DSNs, or SQL in any readiness body.
    body = client.get("/readyz").text
    for leak in ("alembic", "version_num", "postgresql", "SELECT"):
        assert leak not in body
