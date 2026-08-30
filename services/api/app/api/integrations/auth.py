"""Bearer API-key authentication and scope enforcement."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.integrations.problems import IntegrationProblem
from app.core.tenancy import PrincipalContext
from app.db.session import get_db
from app.services.api_keys import (
    ApiKeyConfigurationError,
    api_key_token_shape_valid,
    verify_api_key,
)

_service_key_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="IntegrationServiceApiKey",
    description="InstaDescribe service API key; never use this credential in a browser.",
)


def _bearer_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_service_key_bearer),
    ],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise IntegrationProblem(
            401,
            "unauthorized",
            "Unauthorized",
            "A valid bearer API key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    if not api_key_token_shape_valid(token):
        raise IntegrationProblem(
            401,
            "unauthorized",
            "Unauthorized",
            "A valid bearer API key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def authenticate_integration_principal(
    token: Annotated[str, Depends(_bearer_token)],
    db: Session = Depends(get_db),
) -> PrincipalContext:
    try:
        principal = verify_api_key(db, token)
    except (ApiKeyConfigurationError, SQLAlchemyError):
        raise IntegrationProblem(
            503,
            "authentication_unavailable",
            "Authentication unavailable",
            "Authentication is temporarily unavailable.",
            retryable=True,
        ) from None
    if principal is None:
        raise IntegrationProblem(
            401,
            "unauthorized",
            "Unauthorized",
            "A valid bearer API key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_scope(principal: PrincipalContext, scope: str) -> None:
    if "*" not in principal.scopes and scope not in principal.scopes:
        raise IntegrationProblem(
            403,
            "insufficient_scope",
            "Forbidden",
            "The API key does not grant the required scope.",
        )
