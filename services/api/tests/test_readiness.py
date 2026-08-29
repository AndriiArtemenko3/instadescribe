"""Readiness routes: 200 when ready, safe 503 when not, liveness unaffected.

The readiness handler reads settings/engine lazily per request, so these tests
swap DATABASE_URL via env + cache clears without rebuilding the app.
"""

import os

import pytest
from app.core.config import get_settings
from app.db.session import reset_engine_caches
from app.main import app
from fastapi.testclient import TestClient

TEST_DB_URL = os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL")
requires_db = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)

client = TestClient(app)

# A closed local port: connection refused immediately (connect_timeout=2 caps
# any slower failure mode). The fake password must never leak into responses.
DEAD_DB_URL = "postgresql+psycopg://probe:not-a-real-secret@127.0.0.1:59999/nowhere"


@pytest.fixture(autouse=True)
def _fresh_caches():
    yield
    get_settings.cache_clear()
    reset_engine_caches()


def _point_db_at(monkeypatch, url: str | None) -> None:
    if url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    reset_engine_caches()


def test_missing_configuration_is_safe_503_and_liveness_stays_200(monkeypatch):
    _point_db_at(monkeypatch, None)
    assert client.get("/healthz").status_code == 200
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "checks": ["configuration"]}
    assert client.get("/api/readyz").json() == r.json()


def test_unreachable_database_is_safe_503_without_leaking_dsn(monkeypatch):
    _point_db_at(monkeypatch, DEAD_DB_URL)
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/healthz").status_code == 200
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable", "checks": ["database"]}
    for leak in ("not-a-real-secret", "59999", "psycopg", "Traceback", "SELECT"):
        assert leak not in r.text
    r_alias = client.get("/api/readyz")
    assert r_alias.status_code == 503
    assert r_alias.json() == r.json()


@requires_db
def test_ready_when_database_available_and_aliases_identical(monkeypatch, migrated_db):
    _point_db_at(monkeypatch, migrated_db)  # the run-scoped migrated database
    a = client.get("/readyz")
    b = client.get("/api/readyz")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json() == {"status": "ready"}


@requires_db
def test_recovery_after_outage(monkeypatch, migrated_db):
    _point_db_at(monkeypatch, DEAD_DB_URL)
    assert client.get("/readyz").status_code == 503
    _point_db_at(monkeypatch, migrated_db)
    assert client.get("/readyz").status_code == 200


def test_missing_g3_configuration_is_safe_503_with_sanitized_logging(monkeypatch, caplog):
    import logging

    monkeypatch.delenv("PORTFOLIO_TOKEN_SHA256", raising=False)
    get_settings.cache_clear()
    reset_engine_caches()
    with caplog.at_level(logging.WARNING, logger="app.readiness"):
        r = client.get("/readyz")
    assert r.status_code == 503
    assert "configuration" in r.json()["checks"]
    assert "readiness_unavailable" in caplog.text
    assert "configuration" in caplog.text
    # Stable categories only — never DSN/credentials/SQL/headers/tracebacks.
    for leak in ("postgresql", "placeholder", "SELECT", "Traceback", "X-Portfolio-Token"):
        assert leak not in caplog.text


def test_missing_pipeline_revision_fails_readiness(monkeypatch):
    monkeypatch.delenv("INSTADESCRIBE_PIPELINE_REVISION", raising=False)
    get_settings.cache_clear()
    reset_engine_caches()
    r = client.get("/readyz")
    assert r.status_code == 503
    assert "configuration" in r.json()["checks"]
