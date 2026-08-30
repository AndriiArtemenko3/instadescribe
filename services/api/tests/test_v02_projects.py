"""v0.2 project rename/star optimistic-concurrency contract."""

import os
import threading
import uuid

import pytest
import sqlalchemy as sa

AUTH = {"X-Portfolio-Token": "test-token"}

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


@pytest.fixture()
def project_job(db_engine):
    project_id, job_id = uuid.uuid4(), uuid.uuid4()
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:id, 'Original')"),
            {"id": str(project_id)},
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                "VALUES (:jid, :pid, 'test', 'READY_FOR_REVIEW', '{}'::jsonb)"
            ),
            {"jid": str(job_id), "pid": str(project_id)},
        )
    return project_id, job_id


def _patch(client, project_id, payload):
    return client.patch(f"/api/v1/projects/{project_id}", json=payload, headers=AUTH)


def test_rename_and_star_are_atomic_and_visible_in_job_reconciliation(api_db_client, project_job):
    project_id, job_id = project_job
    initial = api_db_client.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json()
    assert initial["projectId"] == str(project_id)
    assert initial["projectVersion"] == 1

    renamed = _patch(
        api_db_client,
        project_id,
        {"name": "  Renamed project  ", "expectedVersion": 1},
    )
    assert renamed.status_code == 200
    assert renamed.headers["cache-control"] == "private, no-store"
    assert renamed.json() == {
        "projectId": str(project_id),
        "name": "Renamed project",
        "starred": False,
        "version": 2,
        "updatedAt": renamed.json()["updatedAt"],
    }
    assert renamed.json()["updatedAt"].endswith("Z")

    starred = _patch(
        api_db_client,
        project_id,
        {"starred": True, "expectedVersion": 2},
    )
    assert starred.status_code == 200
    assert starred.json()["version"] == 3
    assert starred.json()["name"] == "Renamed project"
    assert starred.json()["starred"] is True

    reconciled = api_db_client.get(f"/api/v1/jobs/{job_id}", headers=AUTH).json()
    assert reconciled["project_name"] == "Renamed project"
    assert reconciled["starred"] is True
    assert reconciled["projectVersion"] == 3


def test_stale_project_version_is_sanitized_and_does_not_overwrite(
    api_db_client, project_job, db_engine
):
    project_id, _job_id = project_job
    assert (
        _patch(api_db_client, project_id, {"name": "winner", "expectedVersion": 1}).status_code
        == 200
    )
    stale = _patch(
        api_db_client,
        project_id,
        {"name": "loser", "starred": True, "expectedVersion": 1},
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "detail": {
            "code": "stale_version",
            "message": "resource changed; refresh and retry",
        }
    }
    with db_engine.begin() as conn:
        stored = conn.execute(
            sa.text("SELECT name, starred, version FROM projects WHERE id = :id"),
            {"id": str(project_id)},
        ).one()
    assert tuple(stored) == ("winner", False, 2)


@pytest.mark.parametrize(
    "payload",
    [
        {"expectedVersion": 1},
        {"name": None, "expectedVersion": 1},
        {"starred": None, "expectedVersion": 1},
        {"name": "", "expectedVersion": 1},
        {"name": "bad\nname", "expectedVersion": 1},
        {"starred": 1, "expectedVersion": 1},
        {"starred": "true", "expectedVersion": 1},
        {"name": "x", "expectedVersion": 0},
        {"name": "x", "expectedVersion": True},
        {"name": "x", "expectedVersion": "1"},
        {"name": "x", "expectedVersion": 1, "unknown": "y"},
        {"name": "x", "expected_version": 1},
        {"name": "x"},
    ],
)
def test_project_patch_payload_is_strict(api_db_client, project_job, payload):
    project_id, _job_id = project_job
    assert _patch(api_db_client, project_id, payload).status_code == 422


def test_distinct_project_identity_and_generic_not_found(api_db_client, project_job):
    project_id, job_id = project_job
    # A processing job ID is never accepted as a project ID.
    assert _patch(api_db_client, job_id, {"name": "wrong", "expectedVersion": 1}).status_code == 404
    for missing in (uuid.uuid4(), "not-a-uuid"):
        response = _patch(api_db_client, missing, {"starred": True, "expectedVersion": 1})
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "not_found"
    assert project_id != job_id


def test_project_database_failure_is_sanitized(api_db_client, project_job, monkeypatch, caplog):
    import app.api.projects as projects_module

    project_id, _job_id = project_job

    def boom(*args, **kwargs):
        raise RuntimeError("dsn=postgresql://user:pw@db/private SELECT secret")

    monkeypatch.setattr(projects_module, "update_project", boom)
    with caplog.at_level("WARNING"):
        response = _patch(api_db_client, project_id, {"name": "safe", "expectedVersion": 1})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"
    for leak in ("pw@db", "SELECT secret", "Traceback"):
        assert leak not in response.text and leak not in caplog.text
    assert "category=database" in caplog.text


def test_same_project_version_concurrency_has_one_winner(api_db_client, project_job):
    from app.main import app
    from fastapi.testclient import TestClient

    project_id, _job_id = project_job
    barrier = threading.Barrier(2)
    responses = [None, None]

    def writer(index, name):
        client = TestClient(app)
        barrier.wait()
        responses[index] = _patch(client, project_id, {"name": name, "expectedVersion": 1})

    threads = [
        threading.Thread(target=writer, args=(0, "writer-a")),
        threading.Thread(target=writer, args=(1, "writer-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert winner["version"] == 2
    assert winner["name"] in {"writer-a", "writer-b"}
    assert (
        next(response for response in responses if response.status_code == 409).json()["detail"][
            "code"
        ]
        == "stale_version"
    )
