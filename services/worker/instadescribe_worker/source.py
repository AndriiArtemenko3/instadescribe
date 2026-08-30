"""Exact source download (B6, hardened by G5.1 C1/C2): the persisted
bucket/key pinned to the EXACT persisted VersionId — the reusable presigned
POST can write a newer object at the same key, so "latest" is never the
processed source. A missing pinned version, 412, or any identity drift is a
deterministic non-retryable failure.

C2: the response StreamingBody is closed in `finally` on every path
(mismatch, overflow, stream exception). When a trustworthy checksum was
persisted at verification time, checksum response mode is requested and the
returned value must be PRESENT and equal — absence is an identity failure,
never silently skipped.
"""

import hashlib
import hmac
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from instadescribe_worker.failures import FailureCode, JobFailure

_CHUNK = 1024 * 1024


def download_source(s3, bucket: str, job, dest: Path) -> str:
    """Download the verified source to `dest`; returns the local SHA-256."""
    if not job.source_version_id:
        raise JobFailure(
            FailureCode.SOURCE_IDENTITY_MISMATCH,
            "job has no pinned source version; cannot prove source identity",
        )
    kwargs = {
        "Bucket": bucket,
        "Key": job.input_object_key,
        "VersionId": job.source_version_id,
    }
    if job.source_checksum_sha256:
        kwargs["ChecksumMode"] = "ENABLED"
    try:
        response = s3.get_object(**kwargs)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code == "PreconditionFailed" or status == 412 or code == "NoSuchVersion":
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "source object no longer matches its verified identity",
            ) from None
        raise JobFailure(FailureCode.SOURCE_DOWNLOAD_FAILED, "source download failed") from None
    except BotoCoreError:
        raise JobFailure(
            FailureCode.SOURCE_DOWNLOAD_FAILED, "source download transport failure"
        ) from None

    body = response["Body"]
    try:
        etag = (response.get("ETag") or "").strip('"')
        if etag != job.source_etag:
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH, "source ETag changed after verification"
            )
        if response.get("VersionId") != job.source_version_id:
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH, "source version changed after verification"
            )
        if job.source_checksum_sha256:
            remote_checksum = response.get("ChecksumSHA256")
            if not remote_checksum:
                raise JobFailure(
                    FailureCode.SOURCE_IDENTITY_MISMATCH,
                    "persisted checksum evidence is missing from the source response",
                )
            if remote_checksum != job.source_checksum_sha256:
                raise JobFailure(
                    FailureCode.SOURCE_IDENTITY_MISMATCH,
                    "source checksum changed after verification",
                )

        expected = job.input_size_bytes
        digest = hashlib.sha256()
        written = 0
        try:
            with open(dest, "wb") as out:
                for chunk in body.iter_chunks(_CHUNK):
                    written += len(chunk)
                    if written > expected:  # hard byte bound while streaming
                        raise JobFailure(
                            FailureCode.SOURCE_IDENTITY_MISMATCH,
                            "source larger than its verified size",
                        )
                    digest.update(chunk)
                    out.write(chunk)
        except JobFailure:
            raise
        except Exception:
            raise JobFailure(
                FailureCode.SOURCE_DOWNLOAD_FAILED, "source stream interrupted"
            ) from None
        if written != expected:
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH, "source smaller than its verified size"
            )
        return digest.hexdigest()
    finally:
        try:
            body.close()
        except Exception:
            pass


def download_verified_asset(s3, bucket: str, asset, dest: Path) -> str:
    """Stream one validated auxiliary Asset by exact S3 version.

    Used for the optional timed transcript. The local SHA-256 is compared with
    persisted evidence when available; signed URLs and raw object bodies never
    enter logs or PostgreSQL.
    """

    if (
        asset.status != "validated"
        or not asset.version_id
        or not asset.etag
        or asset.size_bytes <= 0
    ):
        raise JobFailure(
            FailureCode.SOURCE_IDENTITY_MISMATCH,
            "auxiliary input is not a validated version-pinned asset",
        )
    try:
        response = s3.get_object(
            Bucket=bucket,
            Key=asset.object_key,
            VersionId=asset.version_id,
        )
    except (BotoCoreError, ClientError):
        raise JobFailure(
            FailureCode.SOURCE_DOWNLOAD_FAILED, "auxiliary input download failed"
        ) from None

    body = response["Body"]
    try:
        if (response.get("ETag") or "").strip('"') != asset.etag:
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "auxiliary input ETag changed after verification",
            )
        if response.get("VersionId") != asset.version_id:
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "auxiliary input version changed after verification",
            )
        digest = hashlib.sha256()
        written = 0
        try:
            with open(dest, "wb") as out:
                for chunk in body.iter_chunks(_CHUNK):
                    written += len(chunk)
                    if written > asset.size_bytes:
                        raise JobFailure(
                            FailureCode.SOURCE_IDENTITY_MISMATCH,
                            "auxiliary input exceeds its verified size",
                        )
                    digest.update(chunk)
                    out.write(chunk)
        except JobFailure:
            raise
        except Exception:
            raise JobFailure(
                FailureCode.SOURCE_DOWNLOAD_FAILED,
                "auxiliary input stream interrupted",
            ) from None
        if written != asset.size_bytes:
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "auxiliary input is smaller than its verified size",
            )
        actual = digest.hexdigest()
        if asset.checksum_sha256 and not hmac.compare_digest(actual, asset.checksum_sha256):
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "auxiliary input checksum changed after verification",
            )
        return actual
    finally:
        try:
            body.close()
        except Exception:
            pass


def download_versioned_artifact(
    s3,
    bucket: str,
    artifact,
    dest: Path,
    *,
    max_bytes: int,
) -> str:
    """Download an immutable generated Artifact by its persisted S3 version."""

    meta = artifact.meta if isinstance(artifact.meta, dict) else {}
    version_id = meta.get("version_id")
    if (
        not isinstance(version_id, str)
        or not version_id.strip()
        or isinstance(artifact.size_bytes, bool)
        or not isinstance(artifact.size_bytes, int)
        or not 0 < artifact.size_bytes <= max_bytes
        or not isinstance(artifact.checksum_sha256, str)
        or len(artifact.checksum_sha256) != 64
    ):
        raise JobFailure(
            FailureCode.ARTIFACTS_INVALID,
            "generated artifact is missing a bounded version-pinned identity",
        )
    try:
        response = s3.get_object(
            Bucket=bucket,
            Key=artifact.object_key,
            VersionId=version_id,
        )
    except (BotoCoreError, ClientError):
        raise JobFailure(
            FailureCode.SOURCE_DOWNLOAD_FAILED,
            "generated artifact download failed",
        ) from None

    body = response["Body"]
    try:
        if response.get("VersionId") != version_id:
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "generated artifact version changed after publication",
            )
        persisted_etag = meta.get("etag")
        if persisted_etag is not None and (
            not isinstance(persisted_etag, str)
            or (response.get("ETag") or "").strip('"') != persisted_etag
        ):
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "generated artifact ETag changed after publication",
            )

        digest = hashlib.sha256()
        written = 0
        try:
            with open(dest, "wb") as out:
                for chunk in body.iter_chunks(_CHUNK):
                    written += len(chunk)
                    if written > artifact.size_bytes or written > max_bytes:
                        raise JobFailure(
                            FailureCode.SOURCE_IDENTITY_MISMATCH,
                            "generated artifact exceeds its persisted size",
                        )
                    digest.update(chunk)
                    out.write(chunk)
        except JobFailure:
            raise
        except Exception:
            raise JobFailure(
                FailureCode.SOURCE_DOWNLOAD_FAILED,
                "generated artifact stream interrupted",
            ) from None
        actual = digest.hexdigest()
        if written != artifact.size_bytes or not hmac.compare_digest(
            actual, artifact.checksum_sha256
        ):
            raise JobFailure(
                FailureCode.SOURCE_IDENTITY_MISMATCH,
                "generated artifact bytes changed after publication",
            )
        return actual
    finally:
        try:
            body.close()
        except Exception:
            pass
