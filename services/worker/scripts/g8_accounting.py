"""Fail-closed S3 and PostgreSQL accounting for run-owned G8 resources."""

import sqlalchemy as sa


def _paginate_keys(s3, bucket: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    seen: set[str] = set()
    while True:
        kwargs = {"Bucket": bucket}
        if token is not None:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        next_token = page.get("NextContinuationToken")
        if not isinstance(next_token, str) or not next_token or next_token in seen:
            raise ValueError("truncated object listing has no advancing continuation token")
        seen.add(next_token)
        token = next_token


def _paginate_versions(s3, bucket: str) -> tuple[dict[str, int], list[str]]:
    versions: dict[str, int] = {}
    delete_markers: list[str] = []
    key_marker: str | None = None
    version_marker: str | None = None
    seen: set[tuple[str, str | None]] = set()
    while True:
        kwargs = {"Bucket": bucket}
        if key_marker is not None:
            kwargs["KeyMarker"] = key_marker
        if version_marker is not None:
            kwargs["VersionIdMarker"] = version_marker
        page = s3.list_object_versions(**kwargs)
        for version in page.get("Versions", []):
            key = version["Key"]
            versions[key] = versions.get(key, 0) + 1
        delete_markers.extend(marker["Key"] for marker in page.get("DeleteMarkers", []))
        if not page.get("IsTruncated"):
            return versions, delete_markers
        next_key = page.get("NextKeyMarker")
        next_version = page.get("NextVersionIdMarker")
        if not isinstance(next_key, str) or not next_key:
            raise ValueError("truncated version listing has no usable key marker")
        pair = (next_key, next_version)
        if pair in seen or pair == (key_marker, version_marker):
            raise ValueError("truncated version listing markers do not advance")
        seen.add(pair)
        key_marker, version_marker = pair


def reconcile_bucket(s3, bucket: str, expected_keys: set[str]) -> dict:
    keys = _paginate_keys(s3, bucket)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate keys in current-object listing")
    current = set(keys)
    unknown = sorted(current - expected_keys)
    missing = sorted(expected_keys - current)
    if unknown:
        raise ValueError(f"unknown objects in the run-owned bucket: {unknown[:5]}")
    if missing:
        raise ValueError(f"expected objects missing: {missing[:5]}")

    versions, markers = _paginate_versions(s3, bucket)
    if markers:
        raise ValueError(f"unexpected delete markers: {sorted(set(markers))[:5]}")
    version_keys = set(versions)
    unknown_versions = sorted(version_keys - expected_keys)
    missing_versions = sorted(expected_keys - version_keys)
    if unknown_versions:
        raise ValueError(f"versions exist for unknown keys: {unknown_versions[:5]}")
    if missing_versions:
        raise ValueError(f"expected objects have no version record: {missing_versions[:5]}")
    if current != version_keys:
        raise ValueError("current object and version listings disagree")
    multi = {key: count for key, count in versions.items() if count != 1}
    if multi:
        raise ValueError(f"unexpected extra object versions: {multi}")
    return {
        "objects": len(current),
        "versioned_objects": len(version_keys),
        "delete_markers": 0,
        "keys_verified": len(expected_keys),
    }


def reconcile_database(
    engine,
    *,
    project_id: str,
    job_id: str,
    expected_artifacts: set[tuple[str, str]],
    override_scene_id: str,
) -> dict:
    """Require exactly the run-owned identities, not merely matching totals."""
    with engine.connect() as conn:
        projects = {str(row[0]) for row in conn.execute(sa.text("SELECT id FROM projects"))}
        jobs = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(sa.text("SELECT id, project_id FROM jobs"))
        }
        artifacts = {
            (str(row[0]), str(row[1]), str(row[2]))
            for row in conn.execute(
                sa.text("SELECT job_id, artifact_type, object_key FROM artifacts")
            )
        }
        overrides = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(sa.text("SELECT job_id, scene_id FROM scene_overrides"))
        }

    expected_projects = {project_id}
    expected_jobs = {(job_id, project_id)}
    expected_artifact_rows = {(job_id, kind, key) for kind, key in expected_artifacts}
    expected_overrides = {(job_id, override_scene_id)}
    mismatches = {
        "projects": (projects, expected_projects),
        "jobs": (jobs, expected_jobs),
        "artifacts": (artifacts, expected_artifact_rows),
        "overrides": (overrides, expected_overrides),
    }
    bad = [name for name, (actual, expected) in mismatches.items() if actual != expected]
    if bad:
        raise ValueError(f"database identity reconciliation failed: {','.join(bad)}")
    return {
        "projects": len(projects),
        "jobs": len(jobs),
        "artifacts": len(artifacts),
        "overrides": len(overrides),
    }
