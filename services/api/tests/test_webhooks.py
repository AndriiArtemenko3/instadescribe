"""Golden vectors and SSRF/retry guards for terminal beta webhooks."""

from datetime import UTC, datetime

import pytest
from app.services.webhooks import (
    WebhookContractError,
    delivery_action,
    resolve_public_addresses,
    retry_delay_seconds,
    sign_event,
    validate_endpoint_url,
)


def test_signing_golden_vector_is_over_exact_body_bytes():
    signed = sign_event(
        event_id="evt_123",
        attempt=2,
        payload={"state": "completed", "jobId": "job_123"},
        secret=b"0123456789abcdef0123456789abcdef",
        timestamp=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
    )
    assert signed.body == b'{"jobId":"job_123","state":"completed"}'
    assert signed.headers == {
        "Content-Type": "application/json",
        "Webhook-Id": "evt_123",
        "Webhook-Timestamp": "1787918400",
        "Webhook-Attempt": "2",
        "Webhook-Signature": (
            "v1=bcccca626325ca5fbf2f40b3316c3d2b0fcb7b1814f5aca0e7ff613fd93f57b1"
        ),
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.customer.test/job",
        "https://user:pass@hooks.customer.test/job",
        "https://127.0.0.1/job",
        "https://hooks.customer.test:8443/job",
        "https://hooks.customer.test/job#secret",
        "https://unapproved.customer.test/job",
    ],
)
def test_endpoint_validation_fails_closed(url):
    with pytest.raises(WebhookContractError):
        validate_endpoint_url(url, allowed_hosts={"hooks.customer.test"})


def test_endpoint_validation_accepts_only_operator_approved_host():
    assert (
        validate_endpoint_url(
            "https://hooks.customer.test/instadescribe",
            allowed_hosts={"hooks.customer.test"},
        )
        == "https://hooks.customer.test/instadescribe"
    )


def test_dns_guard_rejects_any_non_public_answer():
    def resolver(*_args, **_kwargs):
        return [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("169.254.169.254", 443)),
        ]

    with pytest.raises(WebhookContractError, match="non-public"):
        resolve_public_addresses("hooks.customer.test", resolver=resolver)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, "success"),
        (204, "success"),
        (410, "disable"),
        (429, "retry"),
        (503, "retry"),
        (400, "stop"),
        (None, "retry"),
    ],
)
def test_delivery_classification(status, expected):
    assert delivery_action(status) == expected


def test_retry_is_bounded_and_deterministic_with_injected_jitter():
    assert retry_delay_seconds(1, random_value=1) == 30
    assert retry_delay_seconds(9, random_value=1) == 7680
    with pytest.raises(WebhookContractError):
        retry_delay_seconds(10, random_value=1)
