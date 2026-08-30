"""Narrow CORS: the configured Vite origin is allowed; others are not."""

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

PREFLIGHT_HEADERS = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type,x-portfolio-token",
}


def test_allowed_origin_preflight_succeeds():
    r = client.options(
        "/api/v1/jobs", headers={"Origin": "http://localhost:5173", **PREFLIGHT_HEADERS}
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed_headers = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-portfolio-token" in allowed_headers
    assert "content-type" in allowed_headers
    assert "access-control-allow-credentials" not in r.headers


def test_unlisted_origin_is_not_granted_access():
    r = client.options(
        "/api/v1/jobs", headers={"Origin": "http://evil.example", **PREFLIGHT_HEADERS}
    )
    assert "access-control-allow-origin" not in r.headers
