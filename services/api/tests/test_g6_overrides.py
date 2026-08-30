"""Scene overrides: strict validation, immutable generated artifact,
version-aware compatibility map, identity policy and CORS."""

import os
import threading
import uuid

import pytest
import sqlalchemy as sa

AUTH = {"X-Portfolio-Token": "test-token"}

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
        reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
    ),
]


@pytest.fixture()
def ready_job(db_engine):
    """A READY_FOR_REVIEW job seeded directly (no S3 needed for overrides)."""
    pid, jid = uuid.uuid4(), uuid.uuid4()
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'g6-ovr')"), {"pid": str(pid)}
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                "VALUES (:jid, :pid, 'test', 'READY_FOR_REVIEW', '{}'::jsonb)"
            ),
            {"jid": str(jid), "pid": str(pid)},
        )
    return {"project_id": pid, "job_id": jid}


def _patch(client, job_id, scene_id, payload):
    # Model a reconciled client: first insert omits expectedVersion; every
    # later write echoes the exact version returned by GET.
    if "expectedVersion" not in payload:
        current = _overrides(client, job_id)
        if current.status_code == 200 and scene_id in current.json():
            payload = {**payload, "expectedVersion": current.json()[scene_id]["version"]}
    return client.patch(f"/api/v1/jobs/{job_id}/scenes/{scene_id}", json=payload, headers=AUTH)


def _overrides(client, job_id):
    return client.get(f"/api/v1/jobs/{job_id}/overrides", headers=AUTH)


def test_patch_insert_update_and_get_map_round_trip(api_db_client, ready_job):
    jid = ready_job["job_id"]
    r = _patch(api_db_client, jid, "scene_1", {"ad": "New AD text", "active": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projectId"] == str(ready_job["project_id"])
    assert body["jobId"] == str(jid)
    assert body["sceneId"] == "scene_1"
    assert body["version"] == 1  # insert starts at version 1
    assert body["updatedAt"].endswith("Z")
    assert body["reviewStatus"] == "edited"
    assert body["reviewedAt"] is None
    assert body["override"] == {
        "ad": "New AD text",
        "active": False,
        "locked": False,
        "version": 1,
        "reviewStatus": "edited",
        "reviewedAt": None,
        "updatedAt": body["updatedAt"],
    }

    r2 = _patch(api_db_client, jid, "scene_1", {"voice": "nova", "speed": 1.25, "locked": True})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["version"] == 2  # conflict update increments
    assert body2["override"] == {
        "ad": "New AD text",  # omitted fields NEVER reset
        "active": False,
        "locked": True,
        "voice": "nova",
        "speed": 1.25,
        "version": 2,
        "reviewStatus": "edited",
        "reviewedAt": None,
        "updatedAt": body2["updatedAt"],
    }

    # The compatibility map remains deterministically ordered.
    _patch(api_db_client, jid, "scene_10", {"ad": "ten"})
    _patch(api_db_client, jid, "scene_2", {"ad": "two"})
    listing = _overrides(api_db_client, jid)
    assert listing.status_code == 200
    data = listing.json()
    assert list(data) == ["scene_1", "scene_2", "scene_10"]  # numeric order
    assert data["scene_1"] == {
        "ad": "New AD text",
        "active": False,
        "locked": True,
        "voice": "nova",
        "speed": 1.25,
        "version": 2,
        "reviewStatus": "edited",
        "reviewedAt": None,
        "updatedAt": data["scene_1"]["updatedAt"],
    }
    for entry in data.values():
        assert {"version", "reviewStatus", "reviewedAt", "updatedAt"} <= set(entry)
        assert not {"updated_at", "jobId", "projectId"} & set(entry)


def test_empty_map_when_no_overrides(api_db_client, ready_job):
    r = _overrides(api_db_client, ready_job["job_id"])
    assert r.status_code == 200
    assert r.json() == {}


def test_ad_allows_tabs_newlines_and_empty(api_db_client, ready_job):
    jid = ready_job["job_id"]
    text = "line one\n\tline two\r\n"
    assert _patch(api_db_client, jid, "scene_1", {"ad": text}).status_code == 200
    assert _overrides(api_db_client, jid).json()["scene_1"]["ad"] == text
    r = _patch(api_db_client, jid, "scene_1", {"ad": ""})
    assert r.status_code == 200  # empty is a legal, deliberate value
    assert _overrides(api_db_client, jid).json()["scene_1"]["ad"] == ""


@pytest.mark.parametrize(
    "payload",
    [
        {},  # empty subset
        {"version": 7},  # client-supplied version forbidden
        {"unknown": "x"},
        {"ad": None},  # explicit null is invalid
        {"active": None},
        {"speed": None},
        {"active": 1},  # coerced booleans rejected
        {"active": "true"},
        {"locked": 0},
        {"speed": True},
        {"speed": "1.0"},
        {"speed": 0.49},
        {"speed": 2.51},
        {"ad": "x" * 8001},
        {"ad": "bad\x00nul"},
        {"ad": "bad\x1bescape"},
        {"voice": "morgan-freeman"},
        {"voice": None},
    ],
)
def test_invalid_payloads_fail_without_a_row_mutation(api_db_client, ready_job, db_engine, payload):
    jid = ready_job["job_id"]
    r = _patch(api_db_client, jid, "scene_1", payload)
    assert r.status_code == 422, (payload, r.text)
    with db_engine.begin() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM scene_overrides WHERE job_id = :jid"),
            {"jid": str(jid)},
        ).scalar_one()
    assert count == 0  # nothing was inserted or mutated


def test_huge_json_integers_yield_clean_422_without_mutation(api_db_client, ready_job, db_engine):
    """G6.1 Gate 2: a valid JSON integer with hundreds of digits overflows
    float() — it must be a normal validation failure, never a traceback."""
    jid = ready_job["job_id"]
    huge = ("9" * 400).encode()
    for raw in (b'{"speed": ' + huge + b"}", b'{"speed": -' + huge + b"}"):
        r = api_db_client.patch(
            f"/api/v1/jobs/{jid}/scenes/scene_1",
            content=raw,
            headers={**AUTH, "Content-Type": "application/json"},
        )
        assert r.status_code == 422, raw[:40]
        assert "Traceback" not in r.text and "OverflowError" not in r.text
    with db_engine.begin() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM scene_overrides WHERE job_id = :jid"),
            {"jid": str(jid)},
        ).scalar_one()
    assert count == 0


def test_speed_precision_contract(api_db_client, ready_job, db_engine):
    """G6.1 Gate 2: NUMERIC(4,2) storage — more than two decimal places is
    rejected (2.499 must not silently store as 2.50); boundaries and
    ordinary two-decimal values are accepted unchanged."""
    jid = ready_job["job_id"]
    for rejected in (2.499, 0.505, 1.001, 0.501):
        r = _patch(api_db_client, jid, "scene_1", {"speed": rejected})
        assert r.status_code == 422, rejected
    with db_engine.begin() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM scene_overrides WHERE job_id = :jid"),
            {"jid": str(jid)},
        ).scalar_one()
    assert count == 0
    for accepted in (0.5, 0.55, 1.25, 2, 2.5):
        r = _patch(api_db_client, jid, "scene_1", {"speed": accepted})
        assert r.status_code == 200, accepted
        assert r.json()["override"]["speed"] == float(accepted)  # stored unchanged


def test_lexically_over_precise_json_resolving_to_two_decimals_is_accepted(
    api_db_client, ready_job
):
    """G6.2 honesty recording: standard JSON parsing resolves these lexemes
    to the IEEE-754 values 2.5 and 1.23 BEFORE Pydantic sees them — they are
    indistinguishable from the plain forms after parsing, so they are
    accepted as those parsed values. The precision contract applies to
    PARSED numeric values; lexical decimal preservation is not claimed."""
    jid = ready_job["job_id"]
    r = api_db_client.patch(
        f"/api/v1/jobs/{jid}/scenes/scene_1",
        content=b'{"speed": 2.5000000000000001}',
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["override"]["speed"] == 2.5  # the parsed value, stored exactly
    r = api_db_client.patch(
        f"/api/v1/jobs/{jid}/scenes/scene_1",
        content=b'{"speed": 1.2300000000000000000001, "expectedVersion": 1}',
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["override"]["speed"] == 1.23


def test_url_encoded_trailing_newline_scene_id_is_rejected(api_db_client, ready_job, db_engine):
    """G6.1 Gate 2: `.match()` with `$` would accept 'scene_1\\n' — the
    route must fullmatch; the encoded-newline path yields 422 and no row."""
    jid = ready_job["job_id"]
    r = api_db_client.patch(
        f"/api/v1/jobs/{jid}/scenes/scene_1%0A",
        json={"ad": "x"},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_scene_id"
    with db_engine.begin() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM scene_overrides WHERE job_id = :jid"),
            {"jid": str(jid)},
        ).scalar_one()
    assert count == 0


def test_postgres_rejects_trailing_newline_scene_id_directly(ready_job, db_engine):
    """G6.1 Gate 2 proof: PostgreSQL's ARE `$` anchors to end-of-string, so
    the EXISTING 0004 constraint already rejects 'scene_1\\n' — no forward
    migration is needed."""
    with db_engine.begin() as conn:
        accepted = conn.execute(
            sa.text("SELECT ('scene_1' || chr(10)) ~ '^scene_[1-9][0-9]*$'")
        ).scalar_one()
        assert accepted is False
    with pytest.raises(sa.exc.DBAPIError):
        with db_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO scene_overrides (id, job_id, scene_id, review_status) "
                    "VALUES (:oid, :jid, 'scene_1' || chr(10), 'edited')"
                ),
                {"oid": str(uuid.uuid4()), "jid": str(ready_job["job_id"])},
            )


def test_get_overrides_database_failure_is_sanitized_503(
    api_db_client, ready_job, monkeypatch, caplog
):
    """G6.1 Gate 2: the GET path's database-failure injection — rollback plus
    sanitized 503 persistence_unavailable, no DSN/SQL/traceback leakage."""
    import app.api.scenes as scenes_module

    def _boom(session, job_id):
        raise RuntimeError("SELECT * FROM secret; dsn=postgresql://user:pw@db/x")

    monkeypatch.setattr(scenes_module, "list_overrides", _boom)
    with caplog.at_level("WARNING"):
        r = _overrides(api_db_client, ready_job["job_id"])
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "persistence_unavailable"
    for leak in ("pw@db", "SELECT * FROM secret", "Traceback"):
        assert leak not in r.text and leak not in caplog.text
    assert "category=database" in caplog.text


def test_overrides_get_carries_private_cache_control(api_db_client, ready_job):
    """G6.1 Gate 4: private edit responses are never cached or stored."""
    r = _overrides(api_db_client, ready_job["job_id"])
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-store"


def test_nan_and_infinity_speed_yield_clean_422_not_500(api_db_client, ready_job, db_engine):
    """Non-finite JSON constants parse at the body layer, fail the strict
    speed validator, and — G6 finding — must serialize to a CLEAN 422 (the
    default handler would embed the NaN input and crash the response)."""
    jid = ready_job["job_id"]
    for raw in (b'{"speed": NaN}', b'{"speed": Infinity}', b'{"speed": -Infinity}'):
        r = api_db_client.patch(
            f"/api/v1/jobs/{jid}/scenes/scene_1",
            content=raw,
            headers={**AUTH, "Content-Type": "application/json"},
        )
        assert r.status_code == 422, raw
        assert "speed" in r.text  # a real validation detail, safely encoded
    with db_engine.begin() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM scene_overrides WHERE job_id = :jid"),
            {"jid": str(jid)},
        ).scalar_one()
    assert count == 0


@pytest.mark.parametrize(
    "scene_id",
    ["scene_0", "scene_01", "shot_1", "scene_", "scene_1x", "SCENE_1", "scene_" + "1" * 130],
)
def test_non_canonical_scene_ids_are_rejected(api_db_client, ready_job, scene_id):
    r = _patch(api_db_client, ready_job["job_id"], scene_id, {"ad": "x"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_scene_id"


def test_identity_policy_and_states(api_db_client, ready_job, db_engine):
    jid = ready_job["job_id"]
    # Project ID as job ID, absent job, malformed UUID: generic 404, no row.
    for bad in (str(ready_job["project_id"]), str(uuid.uuid4()), "not-a-uuid"):
        assert _patch(api_db_client, bad, "scene_1", {"ad": "x"}).status_code == 404
        assert _overrides(api_db_client, bad).status_code == 404
    with db_engine.begin() as conn:
        count = conn.execute(sa.text("SELECT count(*) FROM scene_overrides")).scalar_one()
    assert count == 0
    # Non-ready states: safe 409 job_not_editable.
    for status in ("AWAITING_UPLOAD", "QUEUED", "PROCESSING", "FAILED", "COMPLETED"):
        with db_engine.begin() as conn:
            conn.execute(
                sa.text("UPDATE jobs SET status = :status WHERE id = :jid"),
                {"status": status, "jid": str(jid)},
            )
        r = _patch(api_db_client, jid, "scene_1", {"ad": "x"})
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "job_not_editable"
        assert _overrides(api_db_client, jid).status_code == 409


def test_database_failure_is_sanitized_503(api_db_client, ready_job, monkeypatch, caplog):
    import app.api.scenes as scenes_module

    def _boom(session, job_id, scene_id, values, **kwargs):
        raise RuntimeError("dsn=postgresql://user:pw@db/x")

    monkeypatch.setattr(scenes_module, "write_override", _boom)
    with caplog.at_level("WARNING"):
        r = _patch(api_db_client, ready_job["job_id"], "scene_1", {"ad": "x"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "persistence_unavailable"
    assert "pw@db" not in r.text and "pw@db" not in caplog.text
    assert "category=database" in caplog.text


def _parallel_patches(requests: list[tuple[str, str, dict]], n_threads: int) -> list:
    """Barrier-synchronized PATCHes on independent clients/sessions."""
    from app.main import app
    from fastapi.testclient import TestClient

    barrier = threading.Barrier(n_threads)
    results: list = [None] * len(requests)

    def worker(i: int, job_id: str, scene_id: str, payload: dict):
        client = TestClient(app)  # independent client; sessions are per-request
        barrier.wait()
        results[i] = client.patch(
            f"/api/v1/jobs/{job_id}/scenes/{scene_id}", json=payload, headers=AUTH
        )

    threads = [
        threading.Thread(target=worker, args=(i, jid, sid, payload))
        for i, (jid, sid, payload) in enumerate(requests)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_parallel_patches_to_different_scenes_all_persist(api_db_client, ready_job):
    jid = str(ready_job["job_id"])
    scenes = [f"scene_{i}" for i in range(1, 7)]
    results = _parallel_patches([(jid, sid, {"ad": f"text for {sid}"}) for sid in scenes], 6)
    assert all(r.status_code == 200 for r in results)
    data = _overrides(api_db_client, jid).json()
    assert set(data) == set(scenes)
    for sid in scenes:
        assert data[sid]["ad"] == f"text for {sid}"


def test_disjoint_first_writes_to_same_scene_have_one_winner(api_db_client, ready_job):
    """Two first writers race atomically: one inserts and one gets 409."""
    jid = str(ready_job["job_id"])
    results = _parallel_patches(
        [(jid, "scene_1", {"ad": "the words"}), (jid, "scene_1", {"speed": 1.5})], 2
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    loser = next(r for r in results if r.status_code == 409)
    assert loser.json()["detail"]["code"] == "stale_version"
    final = _overrides(api_db_client, jid).json()["scene_1"]
    assert ("ad" in final) != ("speed" in final)
    last = _patch(api_db_client, jid, "scene_1", {"active": True}).json()
    assert last["version"] == 2


def test_same_version_concurrent_patches_have_one_winner(api_db_client, ready_job):
    jid = str(ready_job["job_id"])
    assert _patch(api_db_client, jid, "scene_1", {"ad": "original"}).status_code == 200
    results = _parallel_patches(
        [
            (jid, "scene_1", {"ad": "writer A", "expectedVersion": 1}),
            (jid, "scene_1", {"ad": "writer B", "expectedVersion": 1}),
        ],
        2,
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["detail"]["code"] == (
        "stale_version"
    )
    final = _overrides(api_db_client, jid).json()["scene_1"]
    assert final["ad"] in ("writer A", "writer B")
    assert final["version"] == 2


def test_cors_patch_preflight_and_disallowed_origin(api_db_client, ready_job):
    jid = ready_job["job_id"]
    approved = "http://localhost:5173"
    r = api_db_client.options(
        f"/api/v1/jobs/{jid}/scenes/scene_1",
        headers={
            "Origin": approved,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "Content-Type,X-Portfolio-Token",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == approved
    assert "PATCH" in r.headers["access-control-allow-methods"]
    allowed_headers = r.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed_headers and "x-portfolio-token" in allowed_headers

    denied = api_db_client.options(
        f"/api/v1/jobs/{jid}/scenes/scene_1",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PATCH"},
    )
    assert denied.status_code == 400 or "access-control-allow-origin" not in denied.headers


def test_scenes_artifact_stays_immutable_and_token_never_reaches_s3(
    api_db_client, ready_job, db_engine
):
    # Overrides never write into artifacts (scenes_json untouched by PATCH) …
    jid = ready_job["job_id"]
    assert _patch(api_db_client, jid, "scene_1", {"ad": "edit"}).status_code == 200
    with db_engine.begin() as conn:
        artifact_writes = conn.execute(
            sa.text("SELECT count(*) FROM artifacts WHERE job_id = :jid"), {"jid": str(jid)}
        ).scalar_one()
    assert artifact_writes == 0
    # … and signed URLs never carry the portfolio token (proven on the
    # presigner directly — no header forwarding exists in the GET path).
    from app.services.s3 import generate_download_url

    url = generate_download_url("uploads/x/source/clip.mp4", version_id="v1", expires_in=60)
    assert "test-token" not in url and "X-Portfolio-Token" not in url
