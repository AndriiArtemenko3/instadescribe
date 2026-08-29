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
) -> dict:
    """Presigned POST with exact bucket/key/type, 1..max size, SSE, short expiry."""
    settings = get_settings()
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("upload limit must be positive")
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
    post = _presign_client().generate_presigned_post(
        Bucket=settings.media_bucket,
        Key=object_key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=settings.presign_expiry_secs,
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.presign_expiry_secs)
    return {"url": post["url"], "fields": post["fields"], "expires_at": expires_at}
