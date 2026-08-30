"""G6.2: deterministic regressions for the live smoke's fail-closed S3
cleanup verifier — absence is proven ONLY by a genuine 404 shape; outages,
denials, throttling and service errors are verification FAILURES."""

import importlib.util
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

_SMOKE = Path(__file__).resolve().parents[1] / "scripts" / "g6_smoke.py"
_spec = importlib.util.spec_from_file_location("g6_smoke", _SMOKE)
g6_smoke = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("g6_smoke", g6_smoke)
_spec.loader.exec_module(g6_smoke)


class _RaisingClient:
    def __init__(self, exc):
        self._exc = exc

    def get_object(self, **kwargs):
        raise self._exc


class _RetrievableClient:
    def get_object(self, **kwargs):
        return {"Body": b"still here"}


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "secret-detail http://internal:4566"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "GetObject",
    )


@pytest.mark.parametrize("code", ["NoSuchVersion", "NoSuchKey", "NotFound", "404"])
def test_exact_not_found_shapes_prove_absence(code):
    failure, observed = g6_smoke.classify_version_absence(
        _RaisingClient(_client_error(code, 404)), "bucket", "jobs/x/a.json", "v1"
    )
    assert failure is None
    assert observed == code


def test_access_denied_is_a_verification_failure_not_absence():
    failure, observed = g6_smoke.classify_version_absence(
        _RaisingClient(_client_error("AccessDenied", 403)), "bucket", "jobs/x/a.json", "v1", 3
    )
    assert failure == "verify-denied:3"
    assert observed is None


def test_not_found_code_without_a_404_status_is_not_absence():
    # Fail closed: the CODE alone is not enough — require the actual 404.
    failure, _ = g6_smoke.classify_version_absence(
        _RaisingClient(_client_error("NoSuchKey", 200)), "bucket", "jobs/x/a.json", "v1", 2
    )
    assert failure == "verify-client-error:2"


def test_service_500_is_a_verification_failure():
    failure, _ = g6_smoke.classify_version_absence(
        _RaisingClient(_client_error("InternalError", 500)), "bucket", "jobs/x/a.json", "v1", 4
    )
    assert failure == "verify-server-error:4"


def test_transport_failure_is_a_verification_failure_without_leakage():
    exc = EndpointConnectionError(endpoint_url="http://secret-host:4566")
    failure, _ = g6_smoke.classify_version_absence(
        _RaisingClient(exc), "bucket", "jobs/x/a.json", "v1", 5
    )
    assert failure == "verify-transport:5"
    assert "secret-host" not in failure and "EndpointConnectionError" not in failure


def test_retrievable_version_is_residue():
    """The token names an ordinal only — never a key basename or VersionId."""
    failure, _ = g6_smoke.classify_version_absence(
        _RetrievableClient(), "bucket", "jobs/x/scenes.json", "v123456789", 6
    )
    assert failure == "verify-retrievable:6"
    assert "scenes.json" not in failure and "v1234567" not in failure


def test_failure_tokens_never_carry_raw_error_text():
    failure, _ = g6_smoke.classify_version_absence(
        _RaisingClient(_client_error("SlowDown", 503)), "bucket", "jobs/x/a.json", "v1", 7
    )
    assert failure == "verify-server-error:7"
    assert "secret-detail" not in failure and "internal:4566" not in failure


def test_hostile_error_code_cannot_escape_into_tokens():
    """G6.3: Error.Code itself is attacker-influenced — secret/endpoint-like
    text placed IN THE CODE (not merely the message) must never surface."""
    hostile = _client_error("AKIA-SECRET http://internal-host:4566/?sig=abc", 400)
    failure, observed = g6_smoke.classify_version_absence(
        _RaisingClient(hostile), "bucket", "jobs/x/a.json", "v1", 8
    )
    assert failure == "verify-client-error:8"
    assert observed is None
    for leak in ("AKIA-SECRET", "internal-host", "sig=abc"):
        assert leak not in failure
    # A hostile code can never be classified as absence either.
    hostile_404 = _client_error("EvilNotFound http://x", 404)
    failure, observed = g6_smoke.classify_version_absence(
        _RaisingClient(hostile_404), "bucket", "jobs/x/a.json", "v1", 9
    )
    assert failure == "verify-client-error:9" and observed is None
