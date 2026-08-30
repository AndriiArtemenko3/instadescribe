"""Liveness routes (dependency-free) and the docs-route policy toggle."""

import importlib

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_healthz_returns_200_without_dependencies():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_healthz_is_an_exact_alias():
    a = client.get("/healthz")
    b = client.get("/api/healthz")
    assert b.status_code == 200
    assert a.json() == b.json()


def test_docs_enabled_by_default():
    assert client.get("/openapi.json").status_code == 200


def test_docs_can_be_disabled_for_public_env(monkeypatch):
    # The toggle is read at app construction, so rebuild the module under the
    # flag, then restore the default module state for later tests.
    import app.main as main_module
    from app.core.config import get_settings

    monkeypatch.setenv("INSTADESCRIBE_ENABLE_DOCS", "0")
    get_settings.cache_clear()
    try:
        reloaded = importlib.reload(main_module)
        c = TestClient(reloaded.app)
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/docs").status_code == 404
        assert c.get("/healthz").status_code == 200
    finally:
        monkeypatch.delenv("INSTADESCRIBE_ENABLE_DOCS", raising=False)
        get_settings.cache_clear()
        importlib.reload(main_module)
