"""InstaDescribe cloud API — G3 scope: liveness, readiness, and the token-gated
/api/v1 surface (create/list/get + presigned upload).

Liveness (`/healthz` + `/api/healthz`) is dependency-free and public.
Readiness (`/readyz` + `/api/readyz`) is public and verifies essential
configuration (DSN, token digest shape, pipeline revision, bucket, limits)
plus `SELECT 1` — it makes no paid or external call. Responses and logs never
carry the DSN, credentials, SQL, headers, or raw exception text; readiness
failures log stable category names only. FastAPI docs/OpenAPI routes are
enabled locally and switched off via `INSTADESCRIBE_ENABLE_DOCS=0` for the
public portfolio environment before G9.
"""

import logging
import math
from http import HTTPStatus

import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.browser.investigations import router as browser_investigations_router
from app.api.browser.invitations import router as browser_invitations_router
from app.api.browser.v1 import router as browser_v1_router
from app.api.integrations.lifecycle import router as integrations_lifecycle_router
from app.api.integrations.problems import IntegrationProblem, problem_response
from app.api.integrations.v1 import router as integrations_v1_router
from app.api.v1 import router as v1_router
from app.core.config import Settings, get_settings
from app.db.session import get_engine

logger = logging.getLogger("app.readiness")

_settings = get_settings()

app = FastAPI(
    title="InstaDescribe Cloud API",
    version="1.0.0-beta.1",
    docs_url="/docs" if _settings.enable_docs else None,
    redoc_url="/redoc" if _settings.enable_docs else None,
    openapi_url="/openapi.json" if _settings.enable_docs else None,
)

# Narrow CORS: the configured local Vite origin only; credentials disabled.
# PATCH added for G6 scene overrides — origins deliberately NOT broadened.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "If-Match",
        "X-Portfolio-Token",
    ],
    expose_headers=["ETag", "Idempotent-Replayed"],
)

app.include_router(v1_router)
app.include_router(integrations_v1_router)
app.include_router(integrations_lifecycle_router)
app.include_router(browser_v1_router)
app.include_router(browser_invitations_router)
app.include_router(browser_investigations_router)


def _json_safe(value):
    """Validation-error payloads may embed the client's raw input; a NaN or
    Infinity float there would crash response serialization (G6 finding —
    a hostile `{"speed": NaN}` body must yield a clean 422, never a 500)."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, str | int | bool) or value is None:
        return value
    return str(value)  # exotic input objects become their safe repr


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {key: _json_safe(val) for key, val in error.items() if key != "url"}
        for error in exc.errors()
    ]
    if request.url.path.startswith(("/api/integrations/v1", "/api/app/v1")):
        public_errors = [
            {key: error[key] for key in ("loc", "msg", "type") if key in error} for error in errors
        ]
        return problem_response(
            request,
            IntegrationProblem(
                422,
                "invalid_request",
                "Invalid request",
                "One or more request fields are invalid.",
                extensions={"errors": public_errors},
            ),
        )
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(IntegrationProblem)
async def _integration_problem_handler(request: Request, exc: IntegrationProblem) -> JSONResponse:
    return problem_response(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(request: Request, exc: StarletteHTTPException):
    if not request.url.path.startswith(("/api/integrations/v1", "/api/app/v1")):
        return await http_exception_handler(request, exc)
    detail = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    code = {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        503: "service_unavailable",
    }.get(exc.status_code, "http_error")
    return problem_response(
        request,
        IntegrationProblem(
            exc.status_code,
            code,
            HTTPStatus(exc.status_code).phrase,
            detail,
            headers=exc.headers,
        ),
    )


HEALTH_BODY = {"status": "ok"}


@app.get("/healthz")
def healthz() -> dict:
    return HEALTH_BODY


@app.get("/api/healthz")
def healthz_alias() -> dict:
    return healthz()


def _config_problems(settings: Settings) -> bool:
    # Local shape only — readiness makes no paid or external call.
    return not (
        settings.database_url
        and settings.token_digest_valid()
        and settings.pipeline_revision
        and settings.media_bucket
        and settings.max_upload_bytes > 0
        and settings.max_duration_secs > 0
        and settings.provider in settings.provider_allowlist
        and settings.work_queue_name.strip()
    )


def _readiness() -> JSONResponse:
    checks: list[str] = []
    settings = get_settings()
    if _config_problems(settings):
        checks.append("configuration")
    if settings.database_url:
        try:
            with get_engine().connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except Exception:
            # Deliberately swallowed: neither the body nor the log may carry
            # the DSN, SQL, or driver exception text.
            checks.append("database")
        else:
            try:
                from app.db.schema_check import database_matches_packaged_head

                if not database_matches_packaged_head(get_engine()):
                    checks.append("schema")
            except Exception:
                checks.append("schema")
    if checks:
        logger.warning("readiness_unavailable categories=%s", ",".join(checks))
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    return JSONResponse(status_code=200, content={"status": "ready"})


@app.get("/readyz")
def readyz() -> JSONResponse:
    return _readiness()


@app.get("/api/readyz")
def readyz_alias() -> JSONResponse:
    return _readiness()
