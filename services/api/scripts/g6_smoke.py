#!/usr/bin/env python3
"""G6 live proof (FABLE5 G6/G6.1 verification gate).

Against the REAL compose stack (rebuilt API image, PostgreSQL, LocalStack):
signed manifest GETs for the video AND every required JSON artifact (exact
bytes, actual content type, checksum, private cache directive), Range 206,
source-overwrite version pinning, atomic PATCH overrides, and
`docker compose restart api` persistence.

Fail-closed cleanup (G6.1): ALL database and exact-VersionId S3 deletions
are attempted even if one fails; cleanup failures are recorded without
masking a test failure; absence of run-owned rows and created versions is
machine-verified; the script exits nonzero on residue and prints
`G6 SMOKE PASSED` only after successful cleanup verification. Run-owned
resources only — shared dev data is never drained. LocalStack behavior
only; real S3 remains G11.
"""

import hashlib
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import boto3
import httpx
import sqlalchemy as sa

REPO = Path(__file__).resolve().parents[3]
API = "http://localhost:8000"
TOKEN = {"X-Portfolio-Token": "local-dev-token"}  # local placeholder only
APP_DB = "postgresql+psycopg://instascribe:local-dev-only@127.0.0.1:5432/instascribe"
BUCKET = "instascribe-media"

GENERATED = {
    "scenes_json": ("analysis/scenes.json", "application/json", b'[{"scene_id": "scene_1"}]'),
    "entities_json": ("analysis/entities.json", "application/json", b"[]"),
    "audio_events_json": ("analysis/audio_events.json", "application/json", b"[]"),
    "ad_placement_gaps_json": ("analysis/ad_placement_gaps.json", "application/json", b"[]"),
    "transcript_json": ("analysis/transcript.json", "application/json", b"[]"),
}
WIRE_TO_TYPE = {
    "scenes": "scenes_json",
    "entities": "entities_json",
    "audioEvents": "audio_events_json",
    "placementGaps": "ad_placement_gaps_json",
    "transcript": "transcript_json",
}

evidence: dict = {"steps": []}


class SmokeFailure(Exception):
    pass


def step(name: str) -> None:
    print(f"\n=== {name}", flush=True)
    evidence["steps"].append(name)


def fail(msg: str) -> None:
    raise SmokeFailure(msg)


def run(cmd: list[str], timeout: int = 900) -> str:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        fail(f"{cmd[0]} rc={proc.returncode}: {proc.stderr[-1500:]}")
    return proc.stdout


def wait_healthy() -> None:
    for _ in range(45):
        try:
            if httpx.get(f"{API}/healthz", timeout=2).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(2)
    fail("API did not become healthy")


def exercise(s3, engine, created_versions, project_id, job_id) -> None:
    step("1. rebuild + start the API image from G6 source; migrate to head")
    run(["docker", "compose", "up", "--build", "-d", "--wait", "postgres", "localstack", "api"])
    run(["docker", "compose", "run", "--rm", "migrate"], timeout=300)
    run(["docker", "compose", "restart", "api"])
    wait_healthy()
    inventory = run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            # This Starlette wraps included routers (no flattening), so the
            # inventory walks the LEAF routers — proving the image imports
            # the G6 code.
            "import json\n"
            "from fastapi.routing import APIRoute\n"
            "from app.api.jobs import router as jobs\n"
            "from app.api.manifest import router as manifest\n"
            "from app.api.projects import router as projects\n"
            "from app.api.scenes import router as scenes\n"
            "out = []\n"
            "for leaf in (jobs, manifest, scenes, projects):\n"
            "    for r in leaf.routes:\n"
            "        if isinstance(r, APIRoute):\n"
            "            out += [[m, '/api/v1' + r.path] "
            "for m in sorted(r.methods - {'HEAD', 'OPTIONS'})]\n"
            "print(json.dumps(sorted(out)))",
        ],
    )
    routes = json.loads(inventory.strip().splitlines()[-1])
    evidence["route_inventory"] = routes
    for expected in (
        ["GET", "/api/v1/jobs/{job_id}/manifest"],
        ["PATCH", "/api/v1/jobs/{job_id}/scenes/{scene_id}"],
        ["GET", "/api/v1/jobs/{job_id}/overrides"],
        ["PATCH", "/api/v1/projects/{project_id}"],
    ):
        if expected not in routes:
            fail(f"missing route in image: {expected}")

    step("2. seed a run-owned READY_FOR_REVIEW job with real S3 objects")
    source_bytes = b"\x00g6-fake-mp4\x01" * 128
    source_key = f"uploads/{job_id}/source/clip.mp4"
    put = s3.put_object(
        Bucket=BUCKET,
        Key=source_key,
        Body=source_bytes,
        ContentType="video/mp4",
        ServerSideEncryption="AES256",
    )
    created_versions.append((source_key, put["VersionId"]))
    seeded_bytes = {"video": source_bytes}
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'g6-smoke')"),
            {"pid": str(project_id)},
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings, "
                "provider, model, input_object_key, input_content_type, input_size_bytes, "
                "source_etag, source_version_id, attempt_count, progress, stage) "
                "VALUES (:jid, :pid, 'dev', 'READY_FOR_REVIEW', '{}'::jsonb, 'fake', "
                "'gpt-4.1', :key, 'video/mp4', :size, :etag, :version, 1, 100, 'complete')"
            ),
            {
                "jid": str(job_id),
                "pid": str(project_id),
                "key": source_key,
                "size": len(source_bytes),
                "etag": put["ETag"].strip('"'),
                "version": put["VersionId"],
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO artifacts (id, job_id, artifact_type, object_key, content_type, "
                "size_bytes, checksum_sha256, metadata) VALUES (:aid, :jid, 'source_video', "
                ":key, 'video/mp4', :size, :sha, CAST(:meta AS jsonb))"
            ),
            {
                "aid": str(uuid.uuid4()),
                "jid": str(job_id),
                "key": source_key,
                "size": len(source_bytes),
                "sha": hashlib.sha256(source_bytes).hexdigest(),
                "meta": json.dumps(
                    {"etag": put["ETag"].strip('"'), "version_id": put["VersionId"]}
                ),
            },
        )
        for artifact_type, (suffix, content_type, body) in GENERATED.items():
            key = f"jobs/{job_id}/attempts/1/{suffix}"
            put_art = s3.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=body,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
            created_versions.append((key, put_art["VersionId"]))
            conn.execute(
                sa.text(
                    "INSERT INTO artifacts (id, job_id, artifact_type, object_key, "
                    "content_type, size_bytes, checksum_sha256, metadata) VALUES "
                    "(:aid, :jid, :atype, :key, :ctype, :size, :sha, '{}'::jsonb)"
                ),
                {
                    "aid": str(uuid.uuid4()),
                    "jid": str(job_id),
                    "atype": artifact_type,
                    "key": key,
                    "ctype": content_type,
                    "size": len(body),
                    "sha": hashlib.sha256(body).hexdigest(),
                },
            )

    step("3. live manifest: EVERY required artifact — bytes/type/checksum/cache")
    with httpx.Client(timeout=30) as web:
        r = web.get(f"{API}/api/v1/jobs/{job_id}/manifest", headers=TOKEN)
        if r.status_code != 200:
            fail(f"manifest: {r.status_code} {r.text[:300]}")
        if r.headers.get("cache-control") != "private, no-store":
            fail("manifest missing Cache-Control: private, no-store")
        manifest = r.json()
        if "localstack" in json.dumps(manifest):
            fail("container-internal hostname leaked")
        for wire, atype in WIRE_TO_TYPE.items():
            seeded_bytes[wire] = GENERATED[atype][2]
        expected_types = {"video": "video/mp4"} | {w: "application/json" for w in WIRE_TO_TYPE}
        fetched = {}
        for wire, expected in seeded_bytes.items():
            ref = manifest["artifacts"][wire]
            # G6.2: positively assert the browser-visible host on EVERY
            # required URL; the container-internal host must never appear.
            if "localhost:4566" not in ref["url"]:
                fail(f"{wire}: URL lacks the browser-visible host")
            if "localstack" in ref["url"]:
                fail(f"{wire}: container-internal hostname leaked")
            got = web.get(ref["url"])
            if got.status_code != 200:
                fail(f"{wire}: HTTP {got.status_code}")
            if got.content != expected:
                fail(f"{wire}: bytes differ from the seeded artifact")
            if got.headers.get("content-type") != expected_types[wire]:
                fail(f"{wire}: content type {got.headers.get('content-type')}")
            if hashlib.sha256(got.content).hexdigest() != ref["checksumSha256"]:
                fail(f"{wire}: checksum mismatch against the manifest")
            if got.headers.get("cache-control") != "private, no-store":
                fail(f"{wire}: signed response lacks the private cache directive")
            fetched[wire] = len(got.content)
        evidence["fetched_required_artifacts"] = fetched
        video_url = manifest["artifacts"]["video"]["url"]
        ranged = web.get(video_url, headers={"Range": "bytes=8-31"})
        if ranged.status_code != 206:
            fail(f"Range not honored: {ranged.status_code}")
        expected_cr = f"bytes 8-31/{len(source_bytes)}"
        if ranged.headers.get("content-range") != expected_cr:
            fail(f"bad Content-Range {ranged.headers.get('content-range')}")
        evidence["manifest_expiry"] = manifest["expiresAt"]

        step("4. overwrite the source key; pinned version still served")
        overwrite = s3.put_object(
            Bucket=BUCKET,
            Key=source_key,
            Body=b"DIFFERENT G6 BYTES",
            ContentType="video/mp4",
            ServerSideEncryption="AES256",
        )
        created_versions.append((source_key, overwrite["VersionId"]))
        if web.get(video_url).content != source_bytes:
            fail("previously issued URL stopped serving the pinned version")
        fresh = web.get(f"{API}/api/v1/jobs/{job_id}/manifest", headers=TOKEN).json()
        if web.get(fresh["artifacts"]["video"]["url"]).content != source_bytes:
            fail("fresh manifest does not pin the processed version")

        step("5. atomic PATCH overrides against the live API")
        p1 = web.patch(
            f"{API}/api/v1/jobs/{job_id}/scenes/scene_1",
            json={"ad": "Live edited AD", "active": False},
            headers=TOKEN,
        )
        if p1.status_code != 200 or p1.json()["version"] != 1:
            fail(f"patch1: {p1.status_code} {p1.text[:200]}")
        p2 = web.patch(
            f"{API}/api/v1/jobs/{job_id}/scenes/scene_1",
            json={"speed": 1.5, "expectedVersion": 1},
            headers=TOKEN,
        )
        if p2.status_code != 200 or p2.json()["version"] != 2:
            fail(f"patch2: {p2.status_code} {p2.text[:200]}")
        before_resp = web.get(f"{API}/api/v1/jobs/{job_id}/overrides", headers=TOKEN)
        if before_resp.headers.get("cache-control") != "private, no-store":
            fail("GET overrides lacks the private cache directive")
        before = before_resp.json()
        expected_value = {
            "ad": "Live edited AD",
            "active": False,
            "locked": False,
            "speed": 1.5,
            "version": 2,
            "reviewStatus": "edited",
            "reviewedAt": None,
        }
        actual_value = before.get("scene_1", {})
        updated_at = actual_value.pop("updatedAt", None)
        if not isinstance(updated_at, str) or not updated_at.endswith("Z"):
            fail(f"override map lacks a canonical updatedAt: {before}")
        if actual_value != expected_value:
            fail(f"override map mismatch before restart: {before}")
        # Preserve the exact response for the restart persistence comparison.
        before["scene_1"]["updatedAt"] = updated_at

    step("6. docker compose restart api — override map and manifest survive")
    run(["docker", "compose", "restart", "api"])
    wait_healthy()
    with httpx.Client(timeout=30) as web:
        after = web.get(f"{API}/api/v1/jobs/{job_id}/overrides", headers=TOKEN).json()
        if after != before:
            fail(f"override map changed across restart: {after}")
        again = web.get(f"{API}/api/v1/jobs/{job_id}/manifest", headers=TOKEN)
        if again.status_code != 200:
            fail(f"manifest after restart: {again.status_code}")
        # G6.2: two independently generated manifests are NOT expected to be
        # byte-identical (fresh signatures/expiry) — instead prove the fresh
        # manifest is USABLE: fetch the newly signed pinned video and verify
        # bytes, checksum and the private cache header again.
        fresh_video = again.json()["artifacts"]["video"]
        got = web.get(fresh_video["url"])
        if got.status_code != 200 or got.content != source_bytes:
            fail("post-restart manifest video is not the pinned source")
        if hashlib.sha256(got.content).hexdigest() != fresh_video["checksumSha256"]:
            fail("post-restart video checksum mismatch")
        if got.headers.get("cache-control") != "private, no-store":
            fail("post-restart video lacks the private cache directive")
    evidence["restart_preserved_overrides"] = True
    evidence["restart_manifest_revalidated"] = True
    evidence["created_version_count"] = len(created_versions)


def clean_up(s3, engine, created_versions, project_id, job_id) -> list[str]:
    """Attempt EVERY deletion; return recorded failures (never raises)."""
    failures: list[str] = []
    for table, column, value in (
        ("scene_overrides", "job_id", str(job_id)),
        ("artifacts", "job_id", str(job_id)),
        ("jobs", "id", str(job_id)),
        ("projects", "id", str(project_id)),
    ):
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(f"DELETE FROM {table} WHERE {column} = :v"),  # noqa: S608
                    {"v": value},
                )
        except Exception:
            failures.append(f"cleanup-db:{table}")
    for ordinal, (key, version_id) in enumerate(created_versions, start=1):
        try:
            s3.delete_object(Bucket=BUCKET, Key=key, VersionId=version_id)
        except Exception:
            # Fixed category + run-owned ordinal only — no key/version/text.
            failures.append(f"cleanup-s3:{ordinal}")
    return failures


# Exact S3 not-found shapes (G6.2): absence is proven ONLY by a genuine 404
# response with one of these codes — AccessDenied/throttling/500/transport
# failures are VERIFICATION FAILURES, never treated as absence.
NOT_FOUND_CODES = {"NoSuchVersion", "NoSuchKey", "NotFound", "404"}


def classify_version_absence(
    s3, bucket: str, key: str, version_id: str, ordinal: int = 0
) -> tuple[str | None, str | None]:
    """(failure_token, observed_not_found_code).

    failure_token None => the exact version is PROVEN absent (a genuine 404
    with an ALLOWLISTED not-found code — the only success classification;
    that allowlisted code may be recorded as evidence). Any other outcome is
    a FIXED-CATEGORY failure token carrying only the ordinal of the
    run-owned resource: no object key, filename, VersionId fragment,
    endpoint, credential, raw exception text or externally supplied error
    code can appear in a token (G6.3 — Error.Code is attacker-influenced
    text and must never be echoed)."""
    from botocore.exceptions import ClientError

    try:
        s3.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        return f"verify-retrievable:{ordinal}", None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in NOT_FOUND_CODES and status == 404:
            return None, code  # proven absent; allowlisted code is safe evidence
        if status == 403:
            return f"verify-denied:{ordinal}", None
        if isinstance(status, int) and status >= 500:
            return f"verify-server-error:{ordinal}", None
        return f"verify-client-error:{ordinal}", None
    except Exception:
        return f"verify-transport:{ordinal}", None


def verify_clean(s3, engine, created_versions, project_id, job_id) -> list[str]:
    """Machine-verify absence of every run-owned row and created version.
    Keeps checking every version after a failure — nothing short-circuits."""
    residue: list[str] = []
    try:
        with engine.begin() as conn:
            for table, column, value in (
                ("scene_overrides", "job_id", str(job_id)),
                ("artifacts", "job_id", str(job_id)),
                ("jobs", "id", str(job_id)),
                ("projects", "id", str(project_id)),
            ):
                count = conn.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE {column} = :v"),  # noqa: S608
                    {"v": value},
                ).scalar_one()
                if count != 0:
                    residue.append(f"db:{table}:{count}")
    except Exception:
        residue.append("db-verify-failed")
    observed_codes: set[str] = set()
    for ordinal, (key, version_id) in enumerate(created_versions, start=1):
        failure, code = classify_version_absence(s3, BUCKET, key, version_id, ordinal)
        if failure is not None:
            residue.append(failure)
        elif code:
            observed_codes.add(code)
    evidence["s3_not_found_observed"] = sorted(observed_codes)
    return residue


def main() -> int:
    s3 = boto3.client(
        "s3",
        region_name="eu-west-2",
        endpoint_url="http://localhost:4566",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    engine = sa.create_engine(APP_DB)
    created_versions: list[tuple[str, str]] = []
    project_id, job_id = uuid.uuid4(), uuid.uuid4()

    test_error: str | None = None
    try:
        exercise(s3, engine, created_versions, project_id, job_id)
    except SmokeFailure as exc:
        test_error = str(exc)
    except Exception as exc:  # unexpected: still recorded, cleanup still runs
        test_error = f"{type(exc).__name__}: {exc}"

    cleanup_failures = clean_up(s3, engine, created_versions, project_id, job_id)
    residue = verify_clean(s3, engine, created_versions, project_id, job_id)
    engine.dispose()

    evidence["cleanup_failures"] = cleanup_failures
    evidence["cleanup_residue"] = residue
    if test_error:
        print(f"G6 SMOKE FAILED: {test_error}", file=sys.stderr, flush=True)
        if cleanup_failures or residue:
            print(f"cleanup problems: {cleanup_failures + residue}", file=sys.stderr, flush=True)
        return 1
    if cleanup_failures or residue:
        print(
            f"G6 SMOKE FAILED: cleanup problems: {cleanup_failures + residue}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    evidence["completed_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    print("\n=== G6 SMOKE PASSED ===")  # only after successful cleanup verification
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
