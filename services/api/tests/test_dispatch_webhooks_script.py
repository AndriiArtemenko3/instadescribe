"""Sanitized process telemetry for the dedicated webhook dispatcher."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.services.maintenance import MaintenanceResult
from app.services.webhook_dispatcher import OperationalMetrics

from scripts import dispatch_webhooks


def test_once_logs_handled_count_without_customer_data(monkeypatch, caplog):
    monkeypatch.setattr(dispatch_webhooks, "run_cycle", lambda: 3)
    monkeypatch.setattr(dispatch_webhooks, "run_maintenance", MaintenanceResult)
    monkeypatch.setattr("sys.argv", ["dispatch_webhooks.py", "--once"])

    with caplog.at_level(logging.INFO):
        assert dispatch_webhooks.main() == 0

    assert "webhook_dispatch_cycle handled=3" in caplog.text


def test_failure_logs_only_error_class(monkeypatch, caplog):
    sensitive = "https://customer.invalid secret-reference"

    def fail() -> int:
        raise RuntimeError(sensitive)

    monkeypatch.setattr(dispatch_webhooks, "run_cycle", fail)
    monkeypatch.setattr(dispatch_webhooks, "run_maintenance", MaintenanceResult)
    monkeypatch.setattr("sys.argv", ["dispatch_webhooks.py", "--once"])

    with caplog.at_level(logging.ERROR):
        assert dispatch_webhooks.main() == 1

    assert "error_class=RuntimeError" in caplog.text
    assert sensitive not in caplog.text


@pytest.mark.parametrize("poll_seconds", ["0", "61"])
def test_poll_interval_remains_bounded(monkeypatch, poll_seconds):
    monkeypatch.setattr(
        "sys.argv", ["dispatch_webhooks.py", "--once", "--poll-seconds", poll_seconds]
    )

    with pytest.raises(SystemExit) as exc_info:
        dispatch_webhooks.main()

    assert exc_info.value.code == 2


def test_maintenance_logs_aggregate_counters_only(monkeypatch, caplog):
    sensitive = "customer@example.invalid/job-secret"
    monkeypatch.setattr(dispatch_webhooks, "run_cycle", lambda: 0)
    monkeypatch.setattr(
        dispatch_webhooks,
        "run_maintenance",
        lambda: MaintenanceResult(
            expired_uploads=2,
            purged_deliverables=5,
            deliverable_purge_failures=1,
            unsafe_legacy_artifacts=3,
            unrecoverable_legacy_artifacts=7,
            blocked_terminal_jobs_object_refs=2,
        ),
    )
    monkeypatch.setattr("sys.argv", ["dispatch_webhooks.py", "--once"])
    with caplog.at_level(logging.INFO):
        assert dispatch_webhooks.main() == 0
    assert "maintenance_cycle expired_uploads=2" in caplog.text
    assert "purged_deliverables=5" in caplog.text
    assert "deliverable_purge_failures=1" in caplog.text
    assert "unsafe_legacy_artifacts=3" in caplog.text
    assert "unrecoverable_legacy_artifacts=7" in caplog.text
    assert "blocked_terminal_jobs_object_refs=2" in caplog.text
    assert sensitive not in caplog.text


def test_maintenance_failure_logs_only_error_class(monkeypatch, caplog):
    sensitive = "customer@example.invalid/job-secret"

    def fail_maintenance() -> MaintenanceResult:
        raise RuntimeError(sensitive)

    monkeypatch.setattr(dispatch_webhooks, "run_cycle", lambda: 0)
    monkeypatch.setattr(dispatch_webhooks, "run_maintenance", fail_maintenance)
    monkeypatch.setattr("sys.argv", ["dispatch_webhooks.py", "--once"])

    with caplog.at_level(logging.ERROR):
        assert dispatch_webhooks.main() == 1
    assert "error_class=RuntimeError" in caplog.text
    assert sensitive not in caplog.text


def test_metric_heartbeat_publishes_exact_sanitized_series(monkeypatch):
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    metrics = OperationalMetrics(
        render_backlog=7,
        outbox_oldest_seconds=125.5,
        webhook_delivery_exhausted=2,
        expired_processing_leases=1,
        quota_rejected=3,
    )
    calls: list[dict] = []
    session = object()

    class CloudWatch:
        def put_metric_data(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setenv("INSTADESCRIBE_METRICS_NAMESPACE", "InstaDescribe/Beta")
    monkeypatch.setattr(
        dispatch_webhooks,
        "get_settings",
        lambda: SimpleNamespace(deployment_tier="beta", aws_region="eu-west-2"),
    )
    monkeypatch.setattr(
        dispatch_webhooks,
        "get_sessionmaker",
        lambda: lambda: nullcontext(session),
    )
    monkeypatch.setattr(
        dispatch_webhooks,
        "collect_operational_metrics",
        lambda actual_session, **kwargs: (
            metrics
            if actual_session is session
            and kwargs
            == {
                "now": now,
                "quota_window_start": now - timedelta(minutes=1),
            }
            else pytest.fail("unexpected metric collection arguments")
        ),
    )
    monkeypatch.setattr(
        dispatch_webhooks.boto3,
        "client",
        lambda service, **kwargs: (
            CloudWatch()
            if service == "cloudwatch" and kwargs == {"region_name": "eu-west-2"}
            else pytest.fail("unexpected AWS client")
        ),
    )

    assert (
        dispatch_webhooks.publish_operational_metrics(
            quota_window_start=now - timedelta(minutes=1),
            now=now,
        )
        == metrics
    )
    assert len(calls) == 1
    assert calls[0]["Namespace"] == "InstaDescribe/Beta"
    by_name = {row["MetricName"]: row for row in calls[0]["MetricData"]}
    assert set(by_name) == {
        "RenderBacklog",
        "OutboxOldestSeconds",
        "WebhookDeliveryExhausted",
        "ExpiredProcessingLeases",
        "QuotaRejected",
    }
    assert by_name["RenderBacklog"] == {
        "MetricName": "RenderBacklog",
        "Value": 7,
        "Unit": "Count",
        "StorageResolution": 60,
    }
    assert by_name["OutboxOldestSeconds"]["Value"] == 125.5
    assert by_name["OutboxOldestSeconds"]["Unit"] == "Seconds"
    for name in (
        "OutboxOldestSeconds",
        "WebhookDeliveryExhausted",
        "ExpiredProcessingLeases",
        "QuotaRejected",
    ):
        assert by_name[name]["Dimensions"] == [{"Name": "Environment", "Value": "beta"}]
    assert "organization" not in repr(calls).lower()
    assert "endpoint" not in repr(calls).lower()


def test_beta_metric_namespace_is_mandatory(monkeypatch):
    monkeypatch.delenv("INSTADESCRIBE_METRICS_NAMESPACE", raising=False)
    monkeypatch.setattr(
        dispatch_webhooks,
        "get_settings",
        lambda: SimpleNamespace(deployment_tier="beta", aws_region="eu-west-2"),
    )

    with pytest.raises(RuntimeError, match="beta metrics namespace is missing"):
        dispatch_webhooks.publish_operational_metrics(
            quota_window_start=datetime.now(UTC) - timedelta(minutes=1),
            now=datetime.now(UTC),
        )


def test_metric_query_failure_prevents_false_zero_publish(monkeypatch):
    now = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    cloudwatch_called = False

    def fail_collection(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    def forbidden_cloudwatch(*_args, **_kwargs):
        nonlocal cloudwatch_called
        cloudwatch_called = True
        raise AssertionError("CloudWatch must not receive a partial heartbeat")

    monkeypatch.setenv("INSTADESCRIBE_METRICS_NAMESPACE", "InstaDescribe/Beta")
    monkeypatch.setattr(
        dispatch_webhooks,
        "get_settings",
        lambda: SimpleNamespace(deployment_tier="beta", aws_region="eu-west-2"),
    )
    monkeypatch.setattr(
        dispatch_webhooks,
        "get_sessionmaker",
        lambda: lambda: nullcontext(object()),
    )
    monkeypatch.setattr(dispatch_webhooks, "collect_operational_metrics", fail_collection)
    monkeypatch.setattr(dispatch_webhooks.boto3, "client", forbidden_cloudwatch)

    with pytest.raises(RuntimeError, match="database unavailable"):
        dispatch_webhooks.publish_operational_metrics(
            quota_window_start=now - timedelta(minutes=1),
            now=now,
        )
    assert cloudwatch_called is False


def test_metric_failure_fails_dispatcher_cycle_without_sensitive_text(monkeypatch, caplog):
    sensitive = "https://customer.invalid/tenant-secret"

    def fail_metrics(**_kwargs):
        raise RuntimeError(sensitive)

    monkeypatch.setattr(dispatch_webhooks, "run_cycle", lambda: 0)
    monkeypatch.setattr(dispatch_webhooks, "publish_operational_metrics", fail_metrics)
    monkeypatch.setattr(dispatch_webhooks, "run_maintenance", MaintenanceResult)
    monkeypatch.setattr("sys.argv", ["dispatch_webhooks.py", "--once"])

    with caplog.at_level(logging.ERROR):
        assert dispatch_webhooks.main() == 1
    assert "error_class=RuntimeError" in caplog.text
    assert sensitive not in caplog.text
