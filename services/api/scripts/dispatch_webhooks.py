#!/usr/bin/env python3
"""Run the bounded webhook outbox dispatcher as a dedicated process."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3

API_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_ROOT = API_ROOT.parents[1] / "packages" / "contracts"
for import_root in (CONTRACTS_ROOT, API_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.core.config import get_settings  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.services.maintenance import MaintenanceResult, run_maintenance_cycle  # noqa: E402
from app.services.s3 import delete_versioned_object  # noqa: E402
from app.services.webhook_dispatcher import (  # noqa: E402
    OperationalMetrics,
    collect_operational_metrics,
    dispatch_one,
    materialize_public_deliveries,
    secrets_manager_resolver,
)
from instadescribe_contracts.environment import getenv_compat  # noqa: E402

LOGGER = logging.getLogger("instadescribe.webhook_dispatcher")
HEARTBEAT_CYCLES = 12
METRICS_INTERVAL_SECONDS = 60
MAINTENANCE_INTERVAL_SECONDS = 60 * 60
_METRICS_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9._/#-]{1,255}$")


def run_cycle() -> int:
    settings = get_settings()
    resolver = secrets_manager_resolver(
        boto3.client("secretsmanager", region_name=settings.aws_region)
    )
    handled = 0
    with get_sessionmaker()() as session:
        materialize_public_deliveries(session)
    while handled < 100:
        with get_sessionmaker()() as session:
            result = dispatch_one(
                session,
                allowed_hosts=settings.webhook_allowed_hosts,
                secret_resolver=resolver,
            )
        if result is None:
            break
        handled += 1
    return handled


def publish_operational_metrics(
    *,
    quota_window_start: datetime,
    now: datetime,
) -> OperationalMetrics | None:
    """Publish one complete durable metrics heartbeat.

    The namespace is deployment-owned and contains no tenant identifier. A
    missing namespace keeps local/legacy execution dependency-free; beta
    execution fails closed because Terraform must inject and IAM-pin an exact
    value.  No metric is sent until every database aggregate succeeds.

    ``RenderBacklog`` intentionally remains dimensionless because the worker
    autoscaling alarm consumes that historical series. The four beta
    operational metrics use only the low-cardinality deployment tier expected
    by their Terraform alarms.
    """

    settings = get_settings()
    namespace = getenv_compat("INSTADESCRIBE_METRICS_NAMESPACE")
    if namespace is None:
        if settings.deployment_tier == "beta":
            raise RuntimeError("beta metrics namespace is missing")
        return None
    if not _METRICS_NAMESPACE_RE.fullmatch(namespace):
        raise RuntimeError("metrics namespace is invalid")
    with get_sessionmaker()() as session:
        metrics = collect_operational_metrics(
            session,
            now=now,
            quota_window_start=quota_window_start,
        )
    environment_dimension = [{"Name": "Environment", "Value": settings.deployment_tier}]
    boto3.client("cloudwatch", region_name=settings.aws_region).put_metric_data(
        Namespace=namespace,
        MetricData=[
            {
                "MetricName": "RenderBacklog",
                "Value": metrics.render_backlog,
                "Unit": "Count",
                "StorageResolution": 60,
            },
            {
                "MetricName": "OutboxOldestSeconds",
                "Value": metrics.outbox_oldest_seconds,
                "Unit": "Seconds",
                "StorageResolution": 60,
                "Dimensions": environment_dimension,
            },
            {
                "MetricName": "WebhookDeliveryExhausted",
                "Value": metrics.webhook_delivery_exhausted,
                "Unit": "Count",
                "StorageResolution": 60,
                "Dimensions": environment_dimension,
            },
            {
                "MetricName": "ExpiredProcessingLeases",
                "Value": metrics.expired_processing_leases,
                "Unit": "Count",
                "StorageResolution": 60,
                "Dimensions": environment_dimension,
            },
            {
                "MetricName": "QuotaRejected",
                "Value": metrics.quota_rejected,
                "Unit": "Count",
                "StorageResolution": 60,
                "Dimensions": environment_dimension,
            },
        ],
    )
    return metrics


def run_maintenance() -> MaintenanceResult:
    """Run one bounded lifecycle/retention cycle with exact-version deletes."""

    with get_sessionmaker()() as session:
        return run_maintenance_cycle(session, delete_versioned_object)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.poll_seconds < 1 or args.poll_seconds > 60:
        parser.error("--poll-seconds must be between 1 and 60")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    idle_cycles = 0
    next_metrics_at = 0.0
    quota_window_start = datetime.now(UTC) - timedelta(seconds=METRICS_INTERVAL_SECONDS)
    next_maintenance_at = 0.0
    while True:
        try:
            handled = run_cycle()
            monotonic_now = time.monotonic()
            metrics = None
            metric_heartbeat = "not_due"
            if monotonic_now >= next_metrics_at:
                metric_now = datetime.now(UTC)
                metrics = publish_operational_metrics(
                    quota_window_start=quota_window_start,
                    now=metric_now,
                )
                metric_heartbeat = "published" if metrics is not None else "disabled"
                quota_window_start = metric_now
                next_metrics_at = monotonic_now + METRICS_INTERVAL_SECONDS
            maintenance = None
            if monotonic_now >= next_maintenance_at:
                maintenance = run_maintenance()
                next_maintenance_at = monotonic_now + MAINTENANCE_INTERVAL_SECONDS
        except Exception as exc:
            # Never interpolate the exception message: provider/HTTP errors may
            # contain an endpoint, secret reference, or other customer data.
            LOGGER.error(
                "webhook_dispatch_cycle_failed error_class=%s",
                type(exc).__name__,
            )
            return 1
        if maintenance is not None:
            LOGGER.info(
                "maintenance_cycle "
                "expired_uploads=%d warned_reviews=%d expired_reviews=%d "
                "reaped_idempotency=%d purged_job_events=%d purged_audit_events=%d "
                "purged_assets=%d asset_purge_failures=%d unsafe_assets=%d "
                "purged_deliverables=%d deliverable_purge_failures=%d "
                "unsafe_deliverables=%d deleted_asset_metadata=%d "
                "deleted_deliverable_metadata=%d purged_legacy_artifacts=%d "
                "legacy_artifact_purge_failures=%d unsafe_legacy_artifacts=%d "
                "unrecoverable_legacy_artifacts=%d "
                "deleted_legacy_artifact_metadata=%d deleted_terminal_jobs=%d "
                "deleted_empty_projects=%d blocked_terminal_jobs_object_refs=%d "
                "blocked_terminal_jobs_pending_deliveries=%d",
                maintenance.expired_uploads,
                maintenance.warned_reviews,
                maintenance.expired_reviews,
                maintenance.reaped_idempotency,
                maintenance.purged_job_events,
                maintenance.purged_audit_events,
                maintenance.purged_assets,
                maintenance.asset_purge_failures,
                maintenance.unsafe_assets,
                maintenance.purged_deliverables,
                maintenance.deliverable_purge_failures,
                maintenance.unsafe_deliverables,
                maintenance.deleted_asset_metadata,
                maintenance.deleted_deliverable_metadata,
                maintenance.purged_legacy_artifacts,
                maintenance.legacy_artifact_purge_failures,
                maintenance.unsafe_legacy_artifacts,
                maintenance.unrecoverable_legacy_artifacts,
                maintenance.deleted_legacy_artifact_metadata,
                maintenance.deleted_terminal_jobs,
                maintenance.deleted_empty_projects,
                maintenance.blocked_terminal_jobs_object_refs,
                maintenance.blocked_terminal_jobs_pending_deliveries,
            )
        idle_cycles = 0 if handled else idle_cycles + 1
        if handled or args.once or idle_cycles >= HEARTBEAT_CYCLES:
            if metrics is None:
                LOGGER.info(
                    "webhook_dispatch_cycle handled=%d metrics=%s",
                    handled,
                    metric_heartbeat,
                )
            else:
                LOGGER.info(
                    "webhook_dispatch_cycle handled=%d render_backlog=%d "
                    "outbox_oldest_seconds=%.3f webhook_delivery_exhausted=%d "
                    "expired_processing_leases=%d quota_rejected=%d",
                    handled,
                    metrics.render_backlog,
                    metrics.outbox_oldest_seconds,
                    metrics.webhook_delivery_exhausted,
                    metrics.expired_processing_leases,
                    metrics.quota_rejected,
                )
            idle_cycles = 0
        if args.once:
            return 0
        if handled == 0:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
