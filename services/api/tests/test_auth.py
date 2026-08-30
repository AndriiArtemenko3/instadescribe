"""Central token boundary: generic 401s, safe 503, structural inheritance."""

from app.core.config import get_settings
from app.core.security import verify_portfolio_token
from app.main import app
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

AUTH = {"X-Portfolio-Token": "test-token"}

client = TestClient(app)


def test_missing_and_wrong_token_are_identical_generic_401s():
    missing = client.get("/api/v1/jobs")
    wrong = client.get("/api/v1/jobs", headers={"X-Portfolio-Token": "not-the-token"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json() == {"detail": "unauthorized"}


def test_missing_server_digest_is_safe_503_not_disabled_protection(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_TOKEN_SHA256", raising=False)
    get_settings.cache_clear()
    try:
        r = client.get("/api/v1/jobs", headers=AUTH)
        assert r.status_code == 503
        assert r.json() == {"detail": "service unavailable"}
    finally:
        get_settings.cache_clear()


def test_malformed_server_digest_is_safe_503(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_TOKEN_SHA256", "not-hex")
    get_settings.cache_clear()
    try:
        r = client.get("/api/v1/jobs", headers=AUTH)
        assert r.status_code == 503
    finally:
        get_settings.cache_clear()


def test_every_v1_route_inherits_the_central_token_dependency():
    # This Starlette version mounts included routers (no flattening), so
    # inspect EVERY leaf router for the complete route/method inventory and
    # the v1 router for the single centrally mounted dependency; then prove
    # inheritance behaviorally: every discovered route + actual HTTP method
    # rejects tokenless requests first (G6 Gate 4 expansion).
    from app.api.jobs import router as jobs_router
    from app.api.manifest import router as manifest_router
    from app.api.projects import router as projects_router
    from app.api.scenes import router as scenes_router
    from app.api.v1 import router as v1_router

    leaf_routers = {
        "jobs": jobs_router,
        "manifest": manifest_router,
        "scenes": scenes_router,
        "projects": projects_router,
    }
    assert verify_portfolio_token in [d.dependency for d in v1_router.dependencies]
    dummy = "00000000-0000-0000-0000-000000000000"
    inventory: list[tuple[str, str]] = []
    for name, leaf in leaf_routers.items():
        api_routes = [r for r in leaf.routes if isinstance(r, APIRoute)]
        assert api_routes, f"no routes registered on the {name} router"
        for route in api_routes:
            unprefixed = route.path.replace("{job_id}", dummy).replace("{scene_id}", "scene_1")
            unprefixed = unprefixed.replace("{project_id}", dummy)
            full_path = "/api/v1" + unprefixed
            for method in route.methods - {"HEAD", "OPTIONS"}:
                inventory.append((method, full_path))
                resp = client.request(method, full_path)
                assert resp.status_code == 401, (method, full_path, resp.status_code)
                # No unguarded duplicate exists outside /api/v1 for ANY
                # method: strictly 404 (405 would mean the path exists with
                # other verbs).
                dup = client.request(method, unprefixed)
                assert dup.status_code == 404, (method, unprefixed, dup.status_code)
    # The complete G6 surface is present in the guarded inventory.
    assert ("GET", f"/api/v1/jobs/{dummy}/manifest") in inventory
    assert ("PATCH", f"/api/v1/jobs/{dummy}/scenes/scene_1") in inventory
    assert ("GET", f"/api/v1/jobs/{dummy}/overrides") in inventory
    assert ("PATCH", f"/api/v1/projects/{dummy}") in inventory


def test_health_and_readiness_remain_public():
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/healthz").status_code == 200
    # Readiness answers without a token (status depends on dependencies).
    assert client.get("/readyz").status_code in (200, 503)
    assert client.get("/api/readyz").status_code in (200, 503)


def test_no_g4_routes_exist():
    fake_id = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/v1/jobs/{fake_id}/upload-complete", headers=AUTH).status_code == 404
    assert client.patch(f"/api/v1/jobs/{fake_id}", headers=AUTH).status_code == 405
    assert client.delete(f"/api/v1/jobs/{fake_id}", headers=AUTH).status_code == 405
