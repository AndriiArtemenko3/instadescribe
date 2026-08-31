"""S3 presigned-POST service (ADR-0003).

Presigning uses the browser-visible endpoint so returned URLs contain
`localhost:4566` locally — never the container-internal `localstack:4566`.
Signature V4 with path-style addressing when configured. PostgreSQL stores
only object keys and metadata; signed URLs/fields are never persisted.
"""

from datetime import UTC, datetime, timedelta
from functools import lru_cache

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import get_settings

INVESTIGATION_RETENTION_TAG_KEY = "instadescribe-retention-days"
_MIN_INVESTIGATION_RETENTION_DAYS = 1
_MAX_INVESTIGATION_RETENTION_DAYS = 30


def investigation_retention_tag(retention_days: int) -> str:
    """Return canonical S3 POST Object ``tagging`` XML for one retention tier."""

    if isinstance(retention_days, bool) or not (
        _MIN_INVESTIGATION_RETENTION_DAYS <= retention_days <= _MAX_INVESTIGATION_RETENTION_DAYS
    ):
        raise ValueError("investigation retention days must be between 1 and 30")
    return (
        "<Tagging><TagSet><Tag>"
        f"<Key>{INVESTIGATION_RETENTION_TAG_KEY}</Key>"
        f"<Value>{retention_days}</Value>"
        "</Tag></TagSet></Tagging>"
    )


def _validate_investigation_retention_tag(retention_tag: str) -> None:
    """Fail closed unless ``retention_tag`` is the canonical 1..30 day tier."""

    canonical_tags = {
        investigation_retention_tag(days)
        for days in range(
            _MIN_INVESTIGATION_RETENTION_DAYS,
            _MAX_INVESTIGATION_RETENTION_DAYS + 1,
        )
    }
    if retention_tag not in canonical_tags:
        raise ValueError("invalid or noncanonical investigation retention tag")


def canonical_source_key(job_id: str, sanitized_filename: str) -> str:
    return f"uploads/{job_id}/source/{sanitized_filename}"


@lru_cache
def _presign_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.s3_endpoint_public,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
        ),
    )


@lru_cache
def _internal_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.s3_endpoint_internal,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
        ),
    )


def head_source(object_key: str) -> dict:
    """HeadObject on the private media bucket via the internal endpoint."""
    return _internal_client().head_object(Bucket=get_settings().media_bucket, Key=object_key)


def delete_versioned_object(object_key: str, version_id: str) -> None:
    """Delete one exact S3 version; key-only retention deletes are forbidden."""

    if not object_key or not version_id:
        raise ValueError("an object key and version id are required")
    _internal_client().delete_object(
        Bucket=get_settings().media_bucket,
        Key=object_key,
        VersionId=version_id,
    )


def reset_s3_caches() -> None:
    """Test hook: drop the cached clients after env changes."""
    _presign_client.cache_clear()
    _internal_client.cache_clear()


def generate_download_url(object_key: str, *, version_id: str | None, expires_in: int) -> str:
    """Short-lived signed GetObject URL via the BROWSER-visible endpoint
    (locally `localhost:4566`, never container-only `localstack:4566`).

    When `version_id` is given the URL pins that exact S3 version — the G6
    manifest signs the processed source's pinned version, never latest. Every
    signed response instructs `Cache-Control: private, no-store` (G6.1) so
    private media cannot be retained beyond the URL's life; real-S3
    confirmation remains G11. The returned URL is handed to the client once;
    it is never persisted or logged."""
    params: dict = {
        "Bucket": get_settings().media_bucket,
        "Key": object_key,
        "ResponseCacheControl": "private, no-store",
    }
    if version_id:
        params["VersionId"] = version_id
    return _presign_client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=expires_in
    )


def generate_upload_post(
    object_key: str,
    content_type: str,
    *,
    max_bytes: int | None = None,
    retention_tag: str | None = None,
) -> dict:
    """Presigned POST with exact bucket/key/type, 1..max size, SSE, short expiry.

    ``retention_tag`` is optional so the stable audio-description upload
    contract remains byte-for-byte untagged.  When supplied by the Browser
    investigation route it is both a form field and an exact policy
    condition.  Reusing the same presigned form can therefore create another
    S3 version, but it cannot create an untagged or differently-tiered one.
    """
    settings = get_settings()
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("upload limit must be positive")
    if retention_tag is not None:
        _validate_investigation_retention_tag(retention_tag)
    upload_limit = (
        settings.max_upload_bytes
        if max_bytes is None
        else min(settings.max_upload_bytes, max_bytes)
    )
    fields = {
        "Content-Type": content_type,
        "x-amz-server-side-encryption": "AES256",
    }
    conditions = [
        {"bucket": settings.media_bucket},
        ["eq", "$key", object_key],
        ["eq", "$Content-Type", content_type],
        ["eq", "$x-amz-server-side-encryption", "AES256"],
        ["content-length-range", 1, upload_limit],
    ]
    if retention_tag is not None:
        # POST Object tagging is an XML multipart form field (unlike the
        # x-amz-tagging header used by PUT Object).
        fields["tagging"] = retention_tag
        conditions.append(["eq", "$tagging", retention_tag])
    post = _presign_client().generate_presigned_post(
        Bucket=settings.media_bucket,
        Key=object_key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=settings.presign_expiry_secs,
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.presign_expiry_secs)
    return {"url": post["url"], "fields": post["fields"], "expires_at": expires_at}
