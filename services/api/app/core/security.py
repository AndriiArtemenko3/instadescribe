"""Central portfolio-token boundary (ADR-0006, decision D6).

Mounted once on the /api/v1 router: every current and future route beneath it
inherits this dependency automatically. Only a SHA-256 hex digest of the token
lives in configuration; comparison is constant-time. Missing and wrong tokens
produce the SAME generic 401. Missing/malformed server configuration produces
a safe 503 — never silently-disabled protection. The token, header value, and
digest are never logged.
"""

import hashlib
import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import get_settings
from app.core.tenancy import PORTFOLIO_PRINCIPAL, PrincipalContext

api_key_header = APIKeyHeader(
    name="X-Portfolio-Token",
    auto_error=False,
    description="Portfolio access token (portfolio access control, not multi-tenant auth)",
)


def verify_portfolio_token(token: str | None = Security(api_key_header)) -> PrincipalContext:
    settings = get_settings()
    if not settings.token_digest_valid():
        raise HTTPException(status_code=503, detail="service unavailable")
    if not token:
        raise HTTPException(status_code=401, detail="unauthorized")
    supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expected = settings.portfolio_token_sha256.lower()  # type: ignore[union-attr]
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    return PORTFOLIO_PRINCIPAL
