"""Transactional outbox delivery for the bounded B2B webhook beta.

Only the four documented public terminal/review events may cross this
boundary.  ``render.requested`` is an internal queue intent and is excluded by
an exact allowlist.  Claims and attempt results are committed separately from
network I/O, giving at-least-once delivery without holding a database lock
while a customer endpoint is slow.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
import urllib3
from sqlalchemy.orm import Session
from urllib3.util import Timeout

from app.domain.states import JobState
from app.models import (
    Job,
    JobEvent,
    Render,
    RenderAttemptArtifact,
    TtsPreview,
    TtsPreviewArtifact,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.services.lifecycle import orphaned_render_attempt_condition
from app.services.tts_previews import orphaned_preview_artifact_condition
from app.services.webhooks import (
    MAX_ATTEMPTS,
    SignedWebhook,
    WebhookContractError,
    delivery_action,
    resolve_public_addresses,
    retry_delay_seconds,
    sign_event,
    validate_endpoint_url,
)

PUBLIC_WEBHOOK_EVENTS = frozenset(
    {"job.needs_review", "job.completed", "job.failed", "job.cancelled"}
)
DELIVERY_LEASE = timedelta(minutes=2)
DELIVERY_HORIZON = timedelta(hours=24)


class WebhookDispatchError(RuntimeError):
    """A stable dispatcher failure that never embeds response bodies/secrets."""


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    delivery_id: uuid.UUID
    endpoint_id: uuid.UUID
    event_id: uuid.UUID
    endpoint_url: str
    signing_secret_ref: str
    attempt: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DispatchResult:
    delivery_id: uuid.UUID
    attempt: int
    action: str
    status_code: int | None


@dataclass(frozen=True, slots=True)
class OperationalMetrics:
    """Low-cardinality dispatcher telemetry derived from durable state.

    ``quota_rejected`` is the number of jobs whose measured-duration quota
    reconciliation reached the durable ``quota_exceeded`` terminal state in
    the caller-supplied, non-overlapping heartbeat interval.  The other
    values are point-in-time gauges.
    """

    render_backlog: int
    outbox_oldest_seconds: float
    webhook_delivery_exhausted: int
    expired_processing_leases: int
    quota_rejected: int


SecretResolver = Callable[[str], bytes]
WebhookSender = Callable[[str, SignedWebhook, tuple[str, ...]], int]


def render_backlog_count(session: Session) -> int:
    """Return all DB-polled media work that needs a zero-scaled worker.

    The CloudWatch metric retains its historical ``RenderBacklog`` name for
    deployment compatibility, but now covers five-format renders, per-scene
    TTS previews and both exact-version cleanup journals.
    """

    try:
        render_count = int(
            session.scalar(
                sa.select(sa.func.count(Render.id)).where(Render.state.in_(("queued", "rendering")))
            )
            or 0
        )
        cleanup_count = int(
            session.scalar(
                sa.select(sa.func.count(RenderAttemptArtifact.id))
                .join(
                    Render,
                    sa.and_(
                        Render.organization_id == RenderAttemptArtifact.organization_id,
                        Render.id == RenderAttemptArtifact.render_id,
                    ),
                )
                .where(orphaned_render_attempt_condition())
            )
            or 0
        )
        preview_count = int(
            session.scalar(
                sa.select(sa.func.count(TtsPreview.id)).where(
                    TtsPreview.state.in_(("queued", "rendering"))
                )
            )
            or 0
        )
        preview_cleanup_count = int(
            session.scalar(
                sa.select(sa.func.count(TtsPreviewArtifact.id))
                .join(
                    TtsPreview,
                    sa.and_(
                        TtsPreview.organization_id == TtsPreviewArtifact.organization_id,
                        TtsPreview.id == TtsPreviewArtifact.preview_id,
                    ),
                )
                .where(orphaned_preview_artifact_condition())
            )
            or 0
        )
        preview_expiry_count = int(
            session.scalar(
                sa.select(sa.func.count(TtsPreview.id)).where(
                    TtsPreview.state.in_(("completed", "failed", "cancelled")),
                    TtsPreview.expires_at <= sa.func.now(),
                    sa.not_(
                        sa.exists().where(
                            TtsPreviewArtifact.organization_id == TtsPreview.organization_id,
                            TtsPreviewArtifact.preview_id == TtsPreview.id,
                        )
                    ),
                )
            )
            or 0
        )
        return (
            render_count
            + cleanup_count
            + preview_count
            + preview_cleanup_count
            + preview_expiry_count
        )
    except Exception:
        session.rollback()
        raise


def collect_operational_metrics(
    session: Session,
    *,
    now: datetime,
    quota_window_start: datetime,
) -> OperationalMetrics:
    """Read one sanitized aggregate snapshot without masking query failures.

    Outbox age includes only due public events for an active organization
    endpoint.  Organizations without an enabled webhook are not delivery
    backlog.  A PROCESSING row with no lease is counted as expired because
    the worker's reclaim path treats that legacy/corrupt state identically.

    Creation-time HTTP quota rejections have no durable row and therefore do
    not appear in ``quota_rejected``.  The metric covers the independently
    auditable worker-side measured-duration rejection only.
    """

    if now.tzinfo is None or quota_window_start.tzinfo is None:
        raise ValueError("metric interval timestamps must be timezone-aware")
    if quota_window_start >= now:
        raise ValueError("quota metric interval must have positive duration")

    try:
        oldest_outbox_at = session.scalar(
            sa.select(sa.func.min(JobEvent.available_at))
            .join(
                WebhookEndpoint,
                WebhookEndpoint.organization_id == JobEvent.organization_id,
            )
            .join(
                Job,
                sa.and_(
                    Job.organization_id == JobEvent.organization_id,
                    Job.id == JobEvent.job_id,
                ),
            )
            .where(
                JobEvent.event_type.in_(PUBLIC_WEBHOOK_EVENTS),
                Job.workflow_kind == "audio_description",
                JobEvent.dispatched_at.is_(None),
                JobEvent.available_at <= now,
                WebhookEndpoint.is_active.is_(True),
            )
        )
        exhausted = int(
            session.scalar(
                sa.select(sa.func.count(WebhookDelivery.id)).where(
                    WebhookDelivery.state == "exhausted"
                )
            )
            or 0
        )
        expired_leases = int(
            session.scalar(
                sa.select(sa.func.count(Job.id)).where(
                    Job.status == JobState.PROCESSING.value,
                    sa.or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
                )
            )
            or 0
        )
        quota_rejected = int(
            session.scalar(
                sa.select(sa.func.count(Job.id)).where(
                    Job.status == JobState.FAILED.value,
                    Job.error_code == "quota_exceeded",
                    Job.completed_at > quota_window_start,
                    Job.completed_at <= now,
                )
            )
            or 0
        )
        oldest_seconds = (
            max(0.0, (now - oldest_outbox_at).total_seconds())
            if oldest_outbox_at is not None
            else 0.0
        )
        return OperationalMetrics(
            render_backlog=render_backlog_count(session),
            outbox_oldest_seconds=oldest_seconds,
            webhook_delivery_exhausted=exhausted,
            expired_processing_leases=expired_leases,
            quota_rejected=quota_rejected,
        )
    except Exception:
        session.rollback()
        raise


def materialize_public_deliveries(
    session: Session,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Atomically fan public outbox events into one delivery per active org endpoint."""

    if not 1 <= limit <= 1_000:
        raise ValueError("limit must be between 1 and 1000")
    instant = now or datetime.now(UTC)
    try:
        rows = session.execute(
            sa.select(JobEvent, WebhookEndpoint)
            .join(
                WebhookEndpoint,
                WebhookEndpoint.organization_id == JobEvent.organization_id,
            )
            .join(
                Job,
                sa.and_(
                    Job.organization_id == JobEvent.organization_id,
                    Job.id == JobEvent.job_id,
                ),
            )
            .where(
                JobEvent.event_type.in_(PUBLIC_WEBHOOK_EVENTS),
                Job.workflow_kind == "audio_description",
                JobEvent.dispatched_at.is_(None),
                JobEvent.available_at <= instant,
                WebhookEndpoint.is_active.is_(True),
            )
            .order_by(JobEvent.available_at, JobEvent.id)
            .limit(limit)
            .with_for_update(of=JobEvent, skip_locked=True)
        ).all()
        for event, endpoint in rows:
            session.add(
                WebhookDelivery(
                    organization_id=event.organization_id,
                    endpoint_id=endpoint.id,
                    event_id=event.id,
                    next_attempt_at=instant,
                )
            )
            event.dispatched_at = instant
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        raise


def claim_due_delivery(
    session: Session,
    *,
    now: datetime | None = None,
) -> DeliveryClaim | None:
    """Claim one due/expired attempt with ``SKIP LOCKED`` fencing."""

    instant = now or datetime.now(UTC)
    while True:
        try:
            row = session.execute(
                sa.select(WebhookDelivery, WebhookEndpoint, JobEvent)
                .join(
                    WebhookEndpoint,
                    sa.and_(
                        WebhookEndpoint.organization_id == WebhookDelivery.organization_id,
                        WebhookEndpoint.id == WebhookDelivery.endpoint_id,
                    ),
                )
                .join(
                    JobEvent,
                    sa.and_(
                        JobEvent.organization_id == WebhookDelivery.organization_id,
                        JobEvent.id == WebhookDelivery.event_id,
                    ),
                )
                .join(
                    Job,
                    sa.and_(
                        Job.organization_id == JobEvent.organization_id,
                        Job.id == JobEvent.job_id,
                    ),
                )
                .where(
                    WebhookEndpoint.is_active.is_(True),
                    JobEvent.event_type.in_(PUBLIC_WEBHOOK_EVENTS),
                    Job.workflow_kind == "audio_description",
                    sa.or_(
                        sa.and_(
                            WebhookDelivery.state.in_(("pending", "retry_scheduled")),
                            WebhookDelivery.next_attempt_at <= instant,
                        ),
                        sa.and_(
                            WebhookDelivery.state == "in_flight",
                            WebhookDelivery.lease_expires_at <= instant,
                        ),
                    ),
                )
                .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
                .limit(1)
                .with_for_update(of=WebhookDelivery, skip_locked=True)
            ).one_or_none()
            if row is None:
                session.rollback()
                return None
            delivery, endpoint, event = row
            if (
                delivery.attempt_count >= MAX_ATTEMPTS
                or delivery.created_at + DELIVERY_HORIZON <= instant
            ):
                delivery.state = "exhausted"
                delivery.lease_expires_at = None
                delivery.last_error_code = "delivery_exhausted"
                delivery.updated_at = instant
                session.commit()
                continue
            if not isinstance(event.payload, dict):
                delivery.state = "exhausted"
                delivery.lease_expires_at = None
                delivery.last_error_code = "invalid_event_payload"
                delivery.updated_at = instant
                session.commit()
                continue
            delivery.state = "in_flight"
            delivery.attempt_count += 1
            delivery.last_attempt_at = instant
            delivery.lease_expires_at = instant + DELIVERY_LEASE
            delivery.updated_at = instant
            claim = DeliveryClaim(
                delivery_id=delivery.id,
                endpoint_id=endpoint.id,
                event_id=event.id,
                endpoint_url=endpoint.endpoint_url,
                signing_secret_ref=endpoint.signing_secret_ref,
                attempt=delivery.attempt_count,
                payload=dict(event.payload),
            )
            session.commit()
            return claim
        except Exception:
            session.rollback()
            raise


def complete_delivery(
    session: Session,
    claim: DeliveryClaim,
    *,
    status_code: int | None,
    error_code: str | None = None,
    force_action: str | None = None,
    now: datetime | None = None,
    random_value: float | None = None,
) -> DispatchResult:
    """Persist one attempt outcome only if the claim generation still matches."""

    instant = now or datetime.now(UTC)
    action = force_action or delivery_action(status_code)
    if action not in {"success", "retry", "disable", "stop"}:
        raise ValueError("invalid webhook delivery action")
    if status_code is not None and not 100 <= status_code <= 599:
        raise ValueError("status_code must be a valid HTTP status")
    if error_code is not None and (not error_code or len(error_code) > 80):
        raise ValueError("error_code must contain 1-80 characters")
    try:
        delivery = session.execute(
            sa.select(WebhookDelivery)
            .where(WebhookDelivery.id == claim.delivery_id)
            .with_for_update()
        ).scalar_one_or_none()
        if (
            delivery is None
            or delivery.state != "in_flight"
            or delivery.attempt_count != claim.attempt
        ):
            raise WebhookDispatchError("webhook delivery claim is stale")
        delivery.last_status_code = status_code
        delivery.last_error_code = error_code
        delivery.lease_expires_at = None
        delivery.updated_at = instant

        if action == "success":
            delivery.state = "succeeded"
            delivery.delivered_at = instant
        elif action == "disable":
            delivery.state = "exhausted"
            endpoint = session.execute(
                sa.select(WebhookEndpoint)
                .where(
                    WebhookEndpoint.organization_id == delivery.organization_id,
                    WebhookEndpoint.id == delivery.endpoint_id,
                )
                .with_for_update()
            ).scalar_one()
            endpoint.is_active = False
            endpoint.disabled_at = instant
            endpoint.updated_at = instant
        elif action == "stop":
            delivery.state = "exhausted"
        elif (
            delivery.attempt_count >= MAX_ATTEMPTS
            or delivery.created_at + DELIVERY_HORIZON <= instant
        ):
            action = "stop"
            delivery.state = "exhausted"
            delivery.last_error_code = error_code or "delivery_exhausted"
        else:
            delivery.state = "retry_scheduled"
            delivery.next_attempt_at = instant + timedelta(
                seconds=retry_delay_seconds(delivery.attempt_count, random_value=random_value)
            )
        session.commit()
        return DispatchResult(delivery.id, claim.attempt, action, status_code)
    except Exception:
        session.rollback()
        raise


def post_signed_webhook(
    url: str,
    signed: SignedWebhook,
    addresses: tuple[str, ...],
) -> int:
    """POST to a pre-resolved public address with SNI/hostname verification.

    Redirects and retries are disabled.  The response body is never loaded or
    logged, and each connection pool is discarded after the one bounded call.
    """

    parsed = urlsplit(url)
    hostname = parsed.hostname
    if hostname is None or not addresses:
        raise WebhookDispatchError("webhook destination is unavailable")
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    headers = {**signed.headers, "Host": hostname}
    for address in addresses:
        pool = urllib3.HTTPSConnectionPool(
            address,
            port=443,
            maxsize=1,
            block=True,
            timeout=Timeout(connect=5.0, read=10.0),
            retries=False,
            cert_reqs="CERT_REQUIRED",
            assert_hostname=hostname,
            server_hostname=hostname,
        )
        try:
            response = pool.urlopen(
                "POST",
                target,
                body=signed.body,
                headers=headers,
                redirect=False,
                retries=False,
                preload_content=False,
            )
            status = int(response.status)
            response.close()
            return status
        except (OSError, urllib3.exceptions.HTTPError):
            pass
        finally:
            pool.close()
    raise WebhookDispatchError("webhook endpoint could not be reached")


def secrets_manager_resolver(client: Any) -> SecretResolver:
    """Create a resolver that returns raw signing bytes from Secrets Manager."""

    def resolve(reference: str) -> bytes:
        response = client.get_secret_value(SecretId=reference)
        if isinstance(response.get("SecretString"), str):
            return response["SecretString"].encode("utf-8")
        raw = response.get("SecretBinary")
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, str):
            try:
                return base64.b64decode(raw, validate=True)
            except ValueError:
                pass
        raise WebhookDispatchError("webhook signing secret is unavailable")

    return resolve


def dispatch_one(
    session: Session,
    *,
    allowed_hosts: Iterable[str],
    secret_resolver: SecretResolver,
    sender: WebhookSender = post_signed_webhook,
    now: datetime | None = None,
) -> DispatchResult | None:
    """Claim, sign, send and persist one attempt without leaking raw errors."""

    instant = now or datetime.now(UTC)
    claim = claim_due_delivery(session, now=instant)
    if claim is None:
        return None
    try:
        endpoint = validate_endpoint_url(claim.endpoint_url, allowed_hosts=allowed_hosts)
        hostname = urlsplit(endpoint).hostname
        if hostname is None:
            raise WebhookContractError("webhook hostname is missing")
        addresses = resolve_public_addresses(hostname)
        secret = secret_resolver(claim.signing_secret_ref)
        signed = sign_event(
            event_id=str(claim.event_id),
            attempt=claim.attempt,
            payload=claim.payload,
            secret=secret,
            timestamp=instant,
        )
    except WebhookContractError:
        return complete_delivery(
            session,
            claim,
            status_code=None,
            error_code="unsafe_endpoint_or_contract",
            force_action="disable",
            now=instant,
        )
    except Exception:
        return complete_delivery(
            session,
            claim,
            status_code=None,
            error_code="secret_unavailable",
            now=instant,
        )
    try:
        status = sender(endpoint, signed, addresses)
    except Exception:
        return complete_delivery(
            session,
            claim,
            status_code=None,
            error_code="network_error",
            now=instant,
        )
    return complete_delivery(
        session,
        claim,
        status_code=status,
        error_code=None if 200 <= status < 300 else "http_error",
        now=instant,
    )
