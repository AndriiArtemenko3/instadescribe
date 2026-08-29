"""Exact-version streaming guards for optional provided transcripts."""

import hashlib
from types import SimpleNamespace

import pytest
from instadescribe_worker.failures import NON_RETRYABLE, FailureCode, JobFailure
from instadescribe_worker.source import download_verified_asset


class Body:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.closed = False

    def iter_chunks(self, _size):
        yield self.payload[:3]
        yield self.payload[3:]

    def close(self):
        self.closed = True


class S3:
    def __init__(self, payload: bytes):
        self.body = Body(payload)
        self.calls = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        return {"Body": self.body, "ETag": '"etag-1"', "VersionId": "version-1"}


def asset(payload: bytes, **overrides):
    values = {
        "status": "validated",
        "object_key": "uploads/orgs/org/jobs/job/transcript/input.vtt",
        "version_id": "version-1",
        "etag": "etag-1",
        "size_bytes": len(payload),
        "checksum_sha256": hashlib.sha256(payload).hexdigest(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_auxiliary_asset_streams_exact_version_and_closes_body(tmp_path):
    payload = b"WEBVTT\n\n00:00.000 --> 00:01.000\nHello\n"
    client = S3(payload)
    destination = tmp_path / "provided.vtt"

    digest = download_verified_asset(client, "media", asset(payload), destination)

    assert destination.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert client.calls == [
        {
            "Bucket": "media",
            "Key": "uploads/orgs/org/jobs/job/transcript/input.vtt",
            "VersionId": "version-1",
        }
    ]
    assert client.body.closed


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "uploaded"},
        {"version_id": None},
        {"etag": None},
        {"size_bytes": 999},
        {"checksum_sha256": "0" * 64},
    ],
)
def test_auxiliary_asset_identity_drift_fails_without_fallback(tmp_path, overrides):
    payload = b"WEBVTT\n"
    with pytest.raises(JobFailure) as raised:
        download_verified_asset(S3(payload), "media", asset(payload, **overrides), tmp_path / "x")
    assert raised.value.code == FailureCode.SOURCE_IDENTITY_MISMATCH


def test_invalid_transcript_is_finite_and_non_retryable():
    assert FailureCode.INVALID_TRANSCRIPT in NON_RETRYABLE
    assert not JobFailure(FailureCode.INVALID_TRANSCRIPT, "invalid transcript").retryable
