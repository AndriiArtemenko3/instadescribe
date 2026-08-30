"""Verify the private BFF-to-Browser-API identity attestation.

The Cognito access token remains the user authentication credential and is
verified independently.  This short-lived HMAC envelope carries only the
authoritative email/MFA context resolved by the trusted Next BFF and binds it
to the exact bearer token, so neither a browser claim nor an envelope copied
from another token can influence the local membership principal.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass

from app.core.config import get_settings

_HEADER_VERSION = "v1"
_MAX_CLOCK_SKEW_SECONDS = 60
_MAX_ASSERTION_BYTES = 1024
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_ASCII_TRIM = " \t\r\n\f\v"


class BrowserAssertionConfigurationUnavailable(Exception):
    """The shared BFF/API trust key is absent or malformed."""


class BrowserAssertionInvalid(Exception):
    """The private assertion is missing, malformed, stale, or unauthentic."""


@dataclass(frozen=True, slots=True)
class VerifiedBrowserAssertion:
    email: str
    mfa_verified: bool


def _decode_base64url(value: str, *, expected_bytes: int | None = None) -> bytes:
    if not value or _B64URL_RE.fullmatch(value) is None:
        raise BrowserAssertionInvalid
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise BrowserAssertionInvalid from exc
    # Reject non-canonical encodings as well as padding/alternate alphabets.
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise BrowserAssertionInvalid
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise BrowserAssertionInvalid
    return decoded


def _shared_key() -> bytes:
    encoded = get_settings().browser_assertion_secret
    if encoded is None or _SECRET_RE.fullmatch(encoded) is None:
        raise BrowserAssertionConfigurationUnavailable
    try:
        key = _decode_base64url(encoded, expected_bytes=32)
    except BrowserAssertionInvalid as exc:
        raise BrowserAssertionConfigurationUnavailable from exc
    return key


def _canonical_email(raw: bytes) -> str:
    try:
        email = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BrowserAssertionInvalid from exc
    canonical = email.strip(_ASCII_TRIM).lower()
    encoded = canonical.encode("utf-8")
    if (
        email != canonical
        or not 3 <= len(encoded) <= 254
        or any(byte < 0x21 or byte > 0x7E for byte in encoded)
        or canonical.count("@") != 1
    ):
        raise BrowserAssertionInvalid
    local, domain = canonical.split("@", 1)
    if not local or not domain:
        raise BrowserAssertionInvalid
    return canonical


def _token_digest(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_browser_assertion(
    token: str,
    value: str | None,
    *,
    now: int | None = None,
) -> VerifiedBrowserAssertion:
    """Verify one exact five-component assertion and return trusted context."""

    key = _shared_key()
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= _MAX_ASSERTION_BYTES:
        raise BrowserAssertionInvalid
    parts = value.split(".")
    if len(parts) != 5:
        raise BrowserAssertionInvalid
    version, timestamp_text, mfa_bit, email_encoded, signature_encoded = parts
    if version != _HEADER_VERSION or mfa_bit not in {"0", "1"}:
        raise BrowserAssertionInvalid
    if (
        not timestamp_text.isascii()
        or not timestamp_text.isdigit()
        or timestamp_text.startswith("0")
    ):
        raise BrowserAssertionInvalid
    try:
        issued_at = int(timestamp_text)
    except ValueError as exc:
        raise BrowserAssertionInvalid from exc
    current = int(time.time()) if now is None else now
    if abs(current - issued_at) > _MAX_CLOCK_SKEW_SECONDS:
        raise BrowserAssertionInvalid

    email = _canonical_email(_decode_base64url(email_encoded))
    signature = _decode_base64url(signature_encoded, expected_bytes=32)
    message = (
        f"{_HEADER_VERSION}\n{timestamp_text}\n{_token_digest(token)}\n{email}\n{mfa_bit}"
    ).encode()
    expected = hmac.digest(key, message, "sha256")
    if not hmac.compare_digest(expected, signature):
        raise BrowserAssertionInvalid
    return VerifiedBrowserAssertion(email=email, mfa_verified=mfa_bit == "1")
