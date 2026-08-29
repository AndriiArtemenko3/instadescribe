"""G6 Gate 2: the exact version-pinned artifact manifest.

Run-owned resources only: every S3 object version this module creates is
recorded and deleted (by exact VersionId) in the seeding fixture's finally;
the shared bucket and unrelated objects are never touched.
"""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa
from app.models import Artifact, Job, Project
from sqlalchemy.orm import Session

AUTH = {"X-Portfolio-Token": "test-token"}

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
        reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
    ),
    pytest.mark.skipif(
        not os.environ.get("INSTADESCRIBE_TEST_S3"),
        reason="INSTADESCRIBE_TEST_S3 not set (LocalStack required; use `make cloud-test` or CI)",
    ),
]

GENERATED = {
    "scenes_json": ("analysis/scenes.json", "application/json", b'[{"scene_id": "scene_1"}]'),
    "entities_json": ("analysis/entities.json", "application/json", b"[]"),
    "audio_events_json": ("analysis/audio_events.json", "application/json", b"[]"),
    "ad_placement_gaps_json": ("analysis/ad_placement_gaps.json", "application/json", b"[]"),
    "transcript_json": ("analysis/transcript.json", "application/json", b"[]"),
    "system_info_json": (
        "analysis/system_info.json",
        "application/json",
        b'{"processing":{"provider":"fake","model":"gpt-4.1"},"tokens":{"input_tokens":0,"output_tokens":0,"total_tokens":0}}',
    ),
}


class SeededJob:
    def __init__(self, project_id, job_id, source_bytes, source_version, artifact_bytes):
        self.project_id = project_id
        self.job_id = job_id
        self.source_bytes = source_bytes
        self.source_version = source_version
        self.artifact_bytes = artifact_bytes  # wire suffix -> bytes


@pytest.fixture()
def seeded_ready_job(db_engine, media_bucket):
    """A READY_FOR_REVIEW job with real S3 objects and consistent rows —
    exactly what G5.1 leaves behind (attempt-scoped keys, pinned source)."""
    bucket, s3 = media_bucket
    created_versions: list[tuple[str, str]] = []  # (key, version_id)
    project_id, job_id = uuid.uuid4(), uuid.uuid4()
    source_bytes = b"\x00fake-mp4-bytes\x01" * 64
    source_key = f"uploads/{job_id}/source/clip.mp4"
    try:
        put = s3.put_object(
            Bucket=bucket,
            Key=source_key,
            Body=source_bytes,
            ContentType="video/mp4",
            ServerSideEncryption="AES256",
        )
        created_versions.append((source_key, put["VersionId"]))
        artifact_bytes: dict[str, bytes] = {}
        with Session(db_engine) as session:
            session.add(Project(id=project_id, name="g6-manifest"))
            session.flush()
            job = Job(
                id=job_id,
                project_id=project_id,
                pipeline_revision="test",
                status="READY_FOR_REVIEW",
                provider="fake",
                model="gpt-4.1",
                settings={},
                input_object_key=source_key,
                input_content_type="video/mp4",
                input_size_bytes=len(source_bytes),
                source_etag=put["ETag"].strip('"'),
                source_version_id=put["VersionId"],
                attempt_count=1,
                progress=100,
                stage="complete",
            )
            session.add(job)
            session.flush()  # jobs row must exist before artifact FK rows
            session.add(
                Artifact(
                    job_id=job_id,
                    artifact_type="source_video",
                    object_key=source_key,
                    content_type="video/mp4",
                    size_bytes=len(source_bytes),
                    checksum_sha256=hashlib.sha256(source_bytes).hexdigest(),
                    meta={"etag": put["ETag"].strip('"'), "version_id": put["VersionId"]},
                )
            )
            for artifact_type, (suffix, content_type, body) in GENERATED.items():
                key = f"jobs/{job_id}/attempts/1/{suffix}"
                put_art = s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentType=content_type,
                    ServerSideEncryption="AES256",
                )
                created_versions.append((key, put_art["VersionId"]))
                artifact_bytes[artifact_type] = body
                session.add(
                    Artifact(
                        job_id=job_id,
                        artifact_type=artifact_type,
                        object_key=key,
                        content_type=content_type,
                        size_bytes=len(body),
                        checksum_sha256=hashlib.sha256(body).hexdigest(),
                        meta=(
                            {
                                "provider": "fake",
                                "model": "gpt-4.1",
                                "tokens": {
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "total_tokens": 0,
                                },
                            }
                            if artifact_type == "system_info_json"
                            else {}
                        ),
                    )
                )
            session.commit()
        yield SeededJob(project_id, job_id, source_bytes, put["VersionId"], artifact_bytes)
    finally:
        # Delete ONLY the exact versions this test created.
        for key, version_id in created_versions:
            try:
                s3.delete_object(Bucket=bucket, Key=key, VersionId=version_id)
            except Exception:
                pass


def _get_manifest(client, job_id):
    return client.get(f"/api/v1/jobs/{job_id}/manifest", headers=AUTH)


def test_manifest_contract_shape_and_signing(api_db_client, seeded_ready_job, work_queue):
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    body = r.json()
    assert body["projectId"] == str(seeded_ready_job.project_id)
    assert body["jobId"] == str(seeded_ready_job.job_id)
    assert body["pipelineRevision"] == "test"
    assert body["expiresAt"].endswith("Z")
    expires = datetime.fromisoformat(body["expiresAt"].replace("Z", "+00:00"))
    remaining = (expires - datetime.now(UTC)).total_seconds()
    assert 200 < remaining <= 300  # default INSTADESCRIBE_DOWNLOAD_PRESIGN_EXPIRY_SECS

    artifacts = body["artifacts"]
    assert set(artifacts) == {
        "video",
        "scenes",
        "entities",
        "audioEvents",
        "placementGaps",
        "transcript",
        "systemInfo",
        "posterJpg",
        "posterAvif",
    }
    assert artifacts["posterJpg"] is None and artifacts["posterAvif"] is None
    for wire in (
        "video",
        "scenes",
        "entities",
        "audioEvents",
        "placementGaps",
        "transcript",
        "systemInfo",
    ):
        ref = artifacts[wire]
        assert set(ref) == {"url", "contentType", "sizeBytes", "checksumSha256"}
        assert ref["sizeBytes"] > 0
        assert len(ref["checksumSha256"]) == 64 and ref["checksumSha256"].islower()
        assert "localhost:4566" in ref["url"]
        assert "localstack" not in ref["url"]  # container-only hostname never leaks
    # The video URL pins the exact processed version (opaque query parameter);
    # no separate raw VersionId field exists anywhere in the response.
    assert "versionId=" in artifacts["video"]["url"]
    assert "version_id" not in json.dumps(body)


def test_legacy_fake_manifest_allows_absent_system_info(
    api_db_client, seeded_ready_job, db_engine, work_queue
):
    """API redeploys must not break READY fake jobs produced before G12."""
    with Session(db_engine) as session:
        session.execute(
            sa.delete(Artifact).where(
                Artifact.job_id == seeded_ready_job.job_id,
                Artifact.artifact_type == "system_info_json",
            )
        )
        session.commit()
    response = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert response.status_code == 200
    assert response.json()["artifacts"]["systemInfo"] is None


def test_openai_manifest_requires_system_info(
    api_db_client, seeded_ready_job, db_engine, work_queue
):
    with Session(db_engine) as session:
        session.execute(
            sa.update(Job).where(Job.id == seeded_ready_job.job_id).values(provider="openai")
        )
        session.execute(
            sa.delete(Artifact).where(
                Artifact.job_id == seeded_ready_job.job_id,
                Artifact.artifact_type == "system_info_json",
            )
        )
        session.commit()
    response = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "manifest_unavailable"


_WIRE_TO_TYPE = {
    "scenes": "scenes_json",
    "entities": "entities_json",
    "audioEvents": "audio_events_json",
    "placementGaps": "ad_placement_gaps_json",
    "transcript": "transcript_json",
    "systemInfo": "system_info_json",
}


def test_every_required_signed_url_fetches_exact_bytes(api_db_client, seeded_ready_job, work_queue):
    """G6.1 Gate 3: video PLUS all five required JSON references — 200, exact
    seeded bytes, actual Content-Type, SHA-256 equal to the manifest
    checksum, browser-visible host only, and the private cache directive."""
    body = _get_manifest(api_db_client, seeded_ready_job.job_id).json()
    artifacts = body["artifacts"]
    with httpx.Client(timeout=30) as web:
        expected_bytes = {"video": seeded_ready_job.source_bytes} | {
            wire: seeded_ready_job.artifact_bytes[atype] for wire, atype in _WIRE_TO_TYPE.items()
        }
        expected_types = {"video": "video/mp4"} | {
            wire: "application/json" for wire in _WIRE_TO_TYPE
        }
        for wire, expected in expected_bytes.items():
            ref = artifacts[wire]
            assert "localhost:4566" in ref["url"] and "localstack" not in ref["url"], wire
            got = web.get(ref["url"])
            assert got.status_code == 200, wire
            assert got.content == expected, wire
            assert got.headers["content-type"] == expected_types[wire], wire
            assert hashlib.sha256(got.content).hexdigest() == ref["checksumSha256"], wire
            # G6.1 Gate 4: signed responses carry the private cache directive.
            assert got.headers.get("cache-control") == "private, no-store", wire
        # Range support for the player: 206 + correct Content-Range.
        ranged = web.get(artifacts["video"]["url"], headers={"Range": "bytes=4-15"})
        assert ranged.status_code == 206
        total = len(seeded_ready_job.source_bytes)
        assert ranged.headers["content-range"] == f"bytes 4-15/{total}"
        assert ranged.content == seeded_ready_job.source_bytes[4:16]


@pytest.mark.parametrize(
    "malformed_key",
    [
        "jobs/{jid}/attempts/1/xanalysis/scenes.json",
        "jobs/{jid}/attempts/1/evil/analysis/scenes.json",
        "jobs/{jid}/attempts/1/analysis/scenes.json/other/scenes.json",
        "jobs/{jid}/attempts/1/analysis/scenes.json.bad/scenes.json",
        "jobs/{jid}/attempts/1/analysis/../analysis/scenes.json",
        "jobs/{jid}/attempts/11/analysis/scenes.json",
    ],
)
def test_malformed_keys_refuse_the_manifest_and_never_reach_the_signer(
    api_db_client, seeded_ready_job, db_engine, work_queue, monkeypatch, malformed_key
):
    """G6.1 Gate 1: only the EXACT deterministic key is acceptable; the
    signer must never be called for an inconsistent manifest."""
    import app.api.manifest as manifest_module

    signed: list = []

    def _spy(key, *, version_id, expires_in):
        signed.append(key)
        return "http://localhost:4566/never-used"

    monkeypatch.setattr(manifest_module, "generate_download_url", _spy)
    rendered = malformed_key.format(jid=seeded_ready_job.job_id)
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE artifacts SET object_key = :key "
                "WHERE job_id = :jid AND artifact_type = 'scenes_json'"
            ),
            {"key": rendered, "jid": str(seeded_ready_job.job_id)},
        )
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"
    assert signed == []  # nothing was signed for the inconsistent manifest


@pytest.mark.parametrize("row_type", ["scenes_json", "source_video"])
@pytest.mark.parametrize("suffix", ["\n", "\r\n", " "])
def test_trailing_junk_checksums_refuse_and_never_sign(
    api_db_client, seeded_ready_job, db_engine, work_queue, monkeypatch, row_type, suffix
):
    """G6.2: `.match()` with `$` accepted '64 hex + newline' — checksum
    validation must be exact for BOTH generated and source rows, and the
    signer must never run for the inconsistent manifest."""
    import app.api.manifest as manifest_module

    signed: list = []

    def _spy(key, *, version_id, expires_in):
        signed.append(key)
        return "http://localhost:4566/never-used"

    monkeypatch.setattr(manifest_module, "generate_download_url", _spy)
    with db_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE artifacts SET checksum_sha256 = checksum_sha256 || :junk "
                "WHERE job_id = :jid AND artifact_type = :atype"
            ),
            {"junk": suffix, "jid": str(seeded_ready_job.job_id), "atype": row_type},
        )
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"
    assert signed == []  # no URL was signed for the inconsistent manifest


def test_response_render_failure_is_sanitized_503(
    api_db_client, seeded_ready_job, work_queue, monkeypatch, caplog
):
    """G6.2: Starlette encodes JSON in the JSONResponse constructor — a
    render failure AFTER signed URLs exist must still be a sanitized 503."""
    import app.api.manifest as manifest_module

    class _ExplodingResponse:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("render exploded with X-Amz-Signature=abc http://localstack:4566")

    monkeypatch.setattr(manifest_module, "JSONResponse", _ExplodingResponse)
    with caplog.at_level("WARNING"):
        r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"
    assert "category=contract" in caplog.text
    for leak in ("X-Amz-Signature", "localstack", "uploads/", "render exploded"):
        assert leak not in caplog.text and leak not in r.text


def test_contract_serialization_failure_is_sanitized(
    api_db_client, seeded_ready_job, work_queue, monkeypatch, caplog
):
    """G6.1 Gate 4: an unexpected final contract/serialization failure is a
    clean 503 with no signed URL or row content in logs."""
    import app.api.manifest as manifest_module

    class _Broken:
        @staticmethod
        def model_validate(body):
            raise RuntimeError(f"unexpected: {body['artifacts']['video']['url']}")

    monkeypatch.setattr(manifest_module, "ManifestResponse", _Broken)
    with caplog.at_level("WARNING"):
        r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"
    assert "category=contract" in caplog.text
    for leak in ("X-Amz-Signature", "localhost:4566", "uploads/"):
        assert leak not in caplog.text and leak not in r.text


def test_manifest_still_serves_pinned_version_after_source_overwrite(
    api_db_client, seeded_ready_job, media_bucket, work_queue
):
    bucket, s3 = media_bucket
    body_before = _get_manifest(api_db_client, seeded_ready_job.job_id).json()
    url_before = body_before["artifacts"]["video"]["url"]
    key = f"uploads/{seeded_ready_job.job_id}/source/clip.mp4"
    overwrite = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=b"COMPLETELY DIFFERENT BYTES",
        ContentType="video/mp4",
        ServerSideEncryption="AES256",
    )
    try:
        with httpx.Client(timeout=30) as web:
            # The previously issued URL still serves the processed source.
            assert web.get(url_before).content == seeded_ready_job.source_bytes
            # A NEWLY requested manifest also signs the pinned version.
            fresh = _get_manifest(api_db_client, seeded_ready_job.job_id).json()
            assert web.get(fresh["artifacts"]["video"]["url"]).content == (
                seeded_ready_job.source_bytes
            )
    finally:
        s3.delete_object(Bucket=bucket, Key=key, VersionId=overwrite["VersionId"])


def test_optional_posters_present_are_returned(
    api_db_client, seeded_ready_job, db_engine, media_bucket, work_queue
):
    bucket, s3 = media_bucket
    poster = b"\xff\xd8\xff jpeg-ish"
    key = f"jobs/{seeded_ready_job.job_id}/attempts/1/posters/poster.jpg"
    put = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=poster,
        ContentType="image/jpeg",
        ServerSideEncryption="AES256",
    )
    try:
        with Session(db_engine) as session:
            session.add(
                Artifact(
                    job_id=seeded_ready_job.job_id,
                    artifact_type="poster_jpg",
                    object_key=key,
                    content_type="image/jpeg",
                    size_bytes=len(poster),
                    checksum_sha256=hashlib.sha256(poster).hexdigest(),
                    meta={},
                )
            )
            session.commit()
        body = _get_manifest(api_db_client, seeded_ready_job.job_id).json()
        assert body["artifacts"]["posterJpg"] is not None
        assert body["artifacts"]["posterAvif"] is None
        with httpx.Client(timeout=30) as web:
            assert web.get(body["artifacts"]["posterJpg"]["url"]).content == poster
    finally:
        s3.delete_object(Bucket=bucket, Key=key, VersionId=put["VersionId"])


def test_unknown_artifact_row_is_ignored(api_db_client, seeded_ready_job, db_engine, work_queue):
    with Session(db_engine) as session:
        session.add(
            Artifact(
                job_id=seeded_ready_job.job_id,
                artifact_type="future_artifact_type",
                object_key=f"jobs/{seeded_ready_job.job_id}/attempts/1/analysis/future.bin",
                content_type="application/octet-stream",
                size_bytes=1,
                checksum_sha256="0" * 64,
                meta={},
            )
        )
        session.commit()
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 200
    assert "future" not in r.text


@pytest.mark.parametrize(
    "artifact_type",
    [
        "source_video",
        "scenes_json",
        "entities_json",
        "audio_events_json",
        "ad_placement_gaps_json",
        "transcript_json",
    ],
)
def test_each_required_row_missing_refuses_the_manifest(
    api_db_client, seeded_ready_job, db_engine, work_queue, artifact_type
):
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("DELETE FROM artifacts WHERE job_id = :jid AND artifact_type = :atype"),
            {"jid": str(seeded_ready_job.job_id), "atype": artifact_type},
        )
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"


@pytest.mark.parametrize(
    "mutation",
    [
        # Losing attempt's prefix.
        "UPDATE artifacts SET object_key = 'jobs/' || :jid || '/attempts/2/analysis/scenes.json' "
        "WHERE job_id = :jid AND artifact_type = 'scenes_json'",
        # Wrong suffix under the winning prefix.
        "UPDATE artifacts SET object_key = 'jobs/' || :jid || '/attempts/1/analysis/other.json' "
        "WHERE job_id = :jid AND artifact_type = 'scenes_json'",
        "UPDATE artifacts SET content_type = 'text/plain' "
        "WHERE job_id = :jid AND artifact_type = 'scenes_json'",
        "UPDATE artifacts SET size_bytes = 0 WHERE job_id = :jid AND artifact_type = 'scenes_json'",
        # Not lowercase 64-hex.
        "UPDATE artifacts SET checksum_sha256 = 'ABCDEF' "
        "WHERE job_id = :jid AND artifact_type = 'scenes_json'",
    ],
)
def test_inconsistent_generated_row_refuses_the_manifest(
    api_db_client, seeded_ready_job, db_engine, work_queue, mutation
):
    with db_engine.begin() as conn:
        conn.execute(sa.text(mutation), {"jid": str(seeded_ready_job.job_id)})
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE artifacts SET object_key = 'uploads/other/source/clip.mp4' "
        "WHERE job_id = :jid AND artifact_type = 'source_video'",
        "UPDATE artifacts SET metadata = jsonb_set(metadata, '{version_id}', '\"vX\"') "
        "WHERE job_id = :jid AND artifact_type = 'source_video'",
        "UPDATE artifacts SET metadata = jsonb_set(metadata, '{etag}', '\"changed\"') "
        "WHERE job_id = :jid AND artifact_type = 'source_video'",
        "UPDATE jobs SET source_version_id = NULL WHERE id = :jid",
        # G6.1: content type and size must also agree with the job row.
        "UPDATE artifacts SET content_type = 'video/webm' "
        "WHERE job_id = :jid AND artifact_type = 'source_video'",
        "UPDATE artifacts SET size_bytes = size_bytes + 1 "
        "WHERE job_id = :jid AND artifact_type = 'source_video'",
    ],
)
def test_source_identity_mismatch_refuses_the_manifest(
    api_db_client, seeded_ready_job, db_engine, work_queue, mutation
):
    with db_engine.begin() as conn:
        conn.execute(sa.text(mutation), {"jid": str(seeded_ready_job.job_id)})
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"


def test_identity_policy_404s(api_db_client, seeded_ready_job, db_engine, work_queue):
    # Malformed UUID, absent job, and the PROJECT id supplied as a job id all
    # return the same generic 404.
    for bad in ("not-a-uuid", str(uuid.uuid4()), str(seeded_ready_job.project_id)):
        r = _get_manifest(api_db_client, bad)
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize(
    "status", ["AWAITING_UPLOAD", "UPLOAD_COMPLETE", "QUEUED", "PROCESSING", "FAILED", "COMPLETED"]
)
def test_non_ready_states_are_safe_409(
    api_db_client, seeded_ready_job, db_engine, work_queue, status
):
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE jobs SET status = :status WHERE id = :jid"),
            {"status": status, "jid": str(seeded_ready_job.job_id)},
        )
    r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "artifacts_not_ready"


def test_signer_and_database_failures_are_sanitized(
    api_db_client, seeded_ready_job, work_queue, monkeypatch, caplog
):
    import app.api.manifest as manifest_module

    def _signer_boom(key, *, version_id, expires_in):
        raise RuntimeError(f"signing exploded for {key} at http://localstack:4566")

    monkeypatch.setattr(manifest_module, "generate_download_url", _signer_boom)
    with caplog.at_level("WARNING"):
        r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "manifest_unavailable"
    for leak in ("localstack", "uploads/", "jobs/", "exploded", "instascribe-media"):
        assert leak not in r.text
        assert leak not in caplog.text
    assert "category=signer" in caplog.text
    monkeypatch.undo()

    def _db_boom(session, job):
        raise RuntimeError("dsn=postgresql://user:pw@db/x")

    monkeypatch.setattr(manifest_module, "resolve_manifest", _db_boom)
    with caplog.at_level("WARNING"):
        r = _get_manifest(api_db_client, seeded_ready_job.job_id)
    assert r.status_code == 503
    assert "pw@db" not in r.text and "pw@db" not in caplog.text
    assert "category=database" in caplog.text


def test_manifest_requires_the_token(api_db_client, seeded_ready_job, work_queue):
    r = api_db_client.get(f"/api/v1/jobs/{seeded_ready_job.job_id}/manifest")
    assert r.status_code == 401
    r = api_db_client.get(
        f"/api/v1/jobs/{seeded_ready_job.job_id}/manifest",
        headers={"X-Portfolio-Token": "wrong"},
    )
    assert r.status_code == 401
    # The equivalent unprefixed route stays a strict 404.
    r = api_db_client.get(f"/jobs/{seeded_ready_job.job_id}/manifest", headers=AUTH)
    assert r.status_code == 404
