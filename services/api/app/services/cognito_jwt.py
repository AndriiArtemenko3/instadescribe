"""Fail-closed, local verification of Cognito user-pool access tokens.

Bearer tokens never leave this process. The unverified JOSE header is used
only to select a bounded RSA key from the configured issuer's JWKS; every
identity attribute consumed by the browser API comes from the verified subject,
the separately authenticated BFF assertion, or the local membership database.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import jwt

from app.core.config import CognitoConfiguration, get_settings

_MAX_TOKEN_BYTES = 32_768
_MAX_JWKS_BYTES = 1_048_576
_MAX_JWKS_KEYS = 20
_JWKS_TTL_SECONDS = 300.0
_UNKNOWN_KID_REFRESH_INTERVAL_SECONDS = 30.0


class CognitoConfigurationUnavailable(Exception):
    """The browser identity trust boundary is absent or malformed."""


class CognitoJwksUnavailable(Exception):
    """The configured issuer's signing keys cannot currently be obtained."""


class CognitoTokenInvalid(Exception):
    """The presented bearer token did not pass strict local verification."""


@dataclass(frozen=True, slots=True)
class VerifiedCognitoClaims:
    subject: str


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        return None


def _download_jwks(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with build_opener(_RejectRedirects).open(request, timeout=5.0) as response:
            if response.getcode() != 200:
                raise CognitoJwksUnavailable
            raw = response.read(_MAX_JWKS_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise CognitoJwksUnavailable from exc
    if len(raw) > _MAX_JWKS_BYTES:
        raise CognitoJwksUnavailable
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CognitoJwksUnavailable from exc
    if not isinstance(document, dict):
        raise CognitoJwksUnavailable
    return document


def _parse_jwks(document: dict[str, Any]) -> dict[str, jwt.PyJWK]:
    raw_keys = document.get("keys")
    if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= _MAX_JWKS_KEYS:
        raise CognitoJwksUnavailable
    parsed: dict[str, jwt.PyJWK] = {}
    for raw_key in raw_keys:
        if not isinstance(raw_key, dict):
            raise CognitoJwksUnavailable
        kid = raw_key.get("kid")
        if (
            not isinstance(kid, str)
            or not 1 <= len(kid) <= 128
            or any(char.isspace() for char in kid)
            or kid in parsed
            or raw_key.get("kty") != "RSA"
            or raw_key.get("use", "sig") != "sig"
            or raw_key.get("alg", "RS256") != "RS256"
        ):
            raise CognitoJwksUnavailable
        try:
            parsed[kid] = jwt.PyJWK.from_dict(raw_key, algorithm="RS256")
        except (jwt.PyJWKError, ValueError, TypeError) as exc:
            raise CognitoJwksUnavailable from exc
    return parsed


class _CachedJwks:
    def __init__(self, url: str) -> None:
        self._url = url
        self._keys: dict[str, jwt.PyJWK] = {}
        self._loaded_at = 0.0
        self._last_unknown_refresh_at = 0.0
        self._lock = threading.Lock()

    def key_for(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            now = time.monotonic()
            if not self._keys or now - self._loaded_at >= _JWKS_TTL_SECONDS:
                self._refresh(now)
            key = self._keys.get(kid)
            if key is not None:
                return key

            # Refresh an otherwise-fresh set once for key rotation, with a
            # short global throttle so attacker-controlled kids cannot turn
            # verification into an unbounded issuer request loop.
            if (
                self._last_unknown_refresh_at == 0.0
                or now - self._last_unknown_refresh_at >= _UNKNOWN_KID_REFRESH_INTERVAL_SECONDS
            ):
                self._last_unknown_refresh_at = now
                self._refresh(now)
                key = self._keys.get(kid)
                if key is not None:
                    return key
        raise CognitoTokenInvalid

    def _refresh(self, now: float) -> None:
        self._keys = _parse_jwks(_download_jwks(self._url))
        self._loaded_at = now


@lru_cache(maxsize=16)
def _cached_jwks(issuer: str, jwks_url: str) -> _CachedJwks:
    # Including the issuer in the key prevents accidental cache sharing when
    # configuration changes between tenants/tests but the URL is reused.
    del issuer
    return _CachedJwks(jwks_url)


def reset_cognito_jwt_cache() -> None:
    """Test/deployment-rotation hook; never stores token material."""

    _cached_jwks.cache_clear()


def _configured_client_matches(claims: dict[str, Any], expected: str) -> bool:
    client_id = claims.get("client_id")
    audience = claims.get("aud")
    if isinstance(client_id, str) and client_id == expected:
        return True
    if isinstance(audience, str):
        return audience == expected
    if isinstance(audience, list):
        return expected in audience and all(isinstance(item, str) for item in audience)
    return False


def verify_cognito_access_token(token: str) -> VerifiedCognitoClaims:
    configuration: CognitoConfiguration | None = get_settings().cognito_configuration()
    if configuration is None:
        raise CognitoConfigurationUnavailable
    if not isinstance(token, str) or not 1 <= len(token.encode("utf-8")) <= _MAX_TOKEN_BYTES:
        raise CognitoTokenInvalid
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise CognitoTokenInvalid from exc
    kid = header.get("kid") if isinstance(header, dict) else None
    if (
        not isinstance(kid, str)
        or not 1 <= len(kid) <= 128
        or any(char.isspace() for char in kid)
        or header.get("alg") != "RS256"
    ):
        raise CognitoTokenInvalid

    key = _cached_jwks(configuration.issuer, configuration.jwks_url).key_for(kid)
    try:
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=configuration.issuer,
            options={
                "require": ["exp", "iss", "sub", "token_use"],
                "verify_aud": False,
            },
        )
    except jwt.InvalidTokenError as exc:
        raise CognitoTokenInvalid from exc
    if claims.get("token_use") != "access" or not _configured_client_matches(
        claims, configuration.app_client_id
    ):
        raise CognitoTokenInvalid
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255 or subject != subject.strip():
        raise CognitoTokenInvalid
    return VerifiedCognitoClaims(subject=subject)
