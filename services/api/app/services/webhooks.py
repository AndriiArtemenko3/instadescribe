"""Small, deterministic primitives for signed terminal webhooks.

Database claiming and network I/O deliberately live outside this module.  The
dispatcher passes the exact body bytes returned here to its HTTP client and
must disable redirects.  Keeping signing and retry classification pure gives
SDK consumers a stable golden-vector contract before any customer endpoint is
enabled.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import random
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

MAX_WEBHOOK_BODY_BYTES = 64 * 1024
MAX_ATTEMPTS = 10
MAX_RETRY_SECONDS = 6 * 60 * 60
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, *range(500, 600)})


class WebhookContractError(ValueError):
    """A webhook endpoint or envelope violates the bounded beta contract."""


@dataclass(frozen=True, slots=True)
class SignedWebhook:
    body: bytes
    headers: dict[str, str]


def validate_endpoint_url(url: str, *, allowed_hosts: Iterable[str]) -> str:
    """Validate an operator-approved exact HTTPS destination.

    Beta does not expose self-service endpoint registration.  The hostname
    must be on an explicit operator allowlist; URL credentials, fragments,
    non-default ports and literal IPs are rejected before persistence.
    """

    if not isinstance(url, str) or len(url) > 2048:
        raise WebhookContractError("webhook URL is invalid")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise WebhookContractError("webhook URL is invalid") from None
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise WebhookContractError("webhook URL must be an exact HTTPS endpoint")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise WebhookContractError("literal IP webhook destinations are forbidden")
    approved = {host.rstrip(".").lower() for host in allowed_hosts}
    if hostname not in approved:
        raise WebhookContractError("webhook hostname is not operator-approved")
    return url


def resolve_public_addresses(
    hostname: str,
    *,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> tuple[str, ...]:
    """Resolve a destination and reject loopback/private/link-local answers.

    The dispatcher calls this immediately before each connection as a second
    guard in addition to the manual hostname allowlist.  It must still disable
    redirects and repeat validation for every retry.
    """

    try:
        answers = resolver(hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise WebhookContractError("webhook hostname could not be resolved") from None
    addresses = sorted({answer[4][0] for answer in answers})
    if not addresses:
        raise WebhookContractError("webhook hostname has no addresses")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            raise WebhookContractError("webhook hostname returned an invalid address") from None
        if not address.is_global:
            raise WebhookContractError("webhook hostname resolved to a non-public address")
    return tuple(addresses)


def sign_event(
    *,
    event_id: str,
    attempt: int,
    payload: dict,
    secret: bytes,
    timestamp: datetime | None = None,
) -> SignedWebhook:
    if not event_id or len(event_id) > 128:
        raise WebhookContractError("event ID is invalid")
    if attempt < 1 or attempt > MAX_ATTEMPTS:
        raise WebhookContractError("webhook attempt is outside the retry bound")
    if len(secret) < 32:
        raise WebhookContractError("webhook signing secret is too short")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        raise WebhookContractError("webhook body exceeds the bounded size")
    instant = timestamp or datetime.now(UTC)
    if instant.tzinfo is None:
        raise WebhookContractError("webhook timestamp must be timezone-aware")
    epoch = str(int(instant.timestamp()))
    signed = event_id.encode("utf-8") + b"." + epoch.encode("ascii") + b"." + body
    digest = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    return SignedWebhook(
        body=body,
        headers={
            "Content-Type": "application/json",
            "Webhook-Id": event_id,
            "Webhook-Timestamp": epoch,
            "Webhook-Attempt": str(attempt),
            "Webhook-Signature": f"v1={digest}",
        },
    )


def delivery_action(status_code: int | None) -> str:
    """Return success, retry, disable or stop for a completed attempt."""

    if status_code is None:
        return "retry"
    if 200 <= status_code < 300:
        return "success"
    if status_code == 410:
        return "disable"
    if status_code in RETRYABLE_STATUS_CODES:
        return "retry"
    return "stop"


def retry_delay_seconds(attempt: int, *, random_value: float | None = None) -> int:
    """Bounded exponential backoff with full jitter for the next delivery."""

    if attempt < 1 or attempt >= MAX_ATTEMPTS:
        raise WebhookContractError("no retry is allowed after this attempt")
    jitter = random.random() if random_value is None else random_value
    if not 0 <= jitter <= 1:
        raise WebhookContractError("retry jitter must be between zero and one")
    ceiling = min(MAX_RETRY_SECONDS, 30 * (2 ** (attempt - 1)))
    return max(1, round(ceiling * jitter))
