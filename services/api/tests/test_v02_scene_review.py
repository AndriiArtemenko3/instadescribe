"""v0.2 scene review state and optimistic-concurrency contract."""

import os
import queue
import threading
import time
import uuid

import pytest
import sqlalchemy as sa

AUTH = {"X-Portfolio-Token": "test-token"}

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


@pytest.fixture()
def ready_job(db_engine):
    project_id, job_id = uuid.uuid4(), uuid.uuid4()
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:id, 'review')"),
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


def _patch(client, job_id, payload, scene_id="scene_1"):
    return client.patch(f"/api/v1/jobs/{job_id}/scenes/{scene_id}", json=payload, headers=AUTH)


def _get(client, job_id):
    return client.get(f"/api/v1/jobs/{job_id}/overrides", headers=AUTH)


def test_edit_approve_edit_and_reject_transitions_are_truthful(api_db_client, ready_job):
    _project_id, job_id = ready_job

    inserted = _patch(api_db_client, job_id, {"ad": "first edit"})
    assert inserted.status_code == 200
    assert inserted.headers["cache-control"] == "private, no-store"
    assert inserted.json()["version"] == 1
    assert inserted.json()["reviewStatus"] == "edited"
    assert inserted.json()["reviewedAt"] is None

    approved = _patch(
        api_db_client,
        job_id,
        {"reviewStatus": "approved", "expectedVersion": 1},
    )
    assert approved.status_code == 200
    assert approved.json()["version"] == 2
    assert approved.json()["reviewStatus"] == "approved"
    assert approved.json()["reviewedAt"].endswith("Z")

    edited = _patch(
        api_db_client,
        job_id,
        {"ad": "changed after approval", "expectedVersion": 2},
    )
    assert edited.status_code == 200
    assert edited.json()["reviewStatus"] == "edited"
    assert edited.json()["reviewedAt"] is None

    rejected = _patch(
        api_db_client,
        job_id,
        {"reviewStatus": "rejected", "expectedVersion": 3},
    )
    assert rejected.status_code == 200
    assert rejected.json()["reviewStatus"] == "rejected"
    assert rejected.json()["reviewedAt"].endswith("Z")

    back_to_edited = _patch(
        api_db_client,
        job_id,
        {"reviewStatus": "edited", "expectedVersion": 4},
    )
    assert back_to_edited.status_code == 200
    assert back_to_edited.json()["reviewStatus"] == "edited"
    assert back_to_edited.json()["reviewedAt"] is None

    value = _get(api_db_client, job_id).json()["scene_1"]
    assert value["version"] == 5
    assert value["reviewStatus"] == "edited"
    assert value["reviewedAt"] is None
    assert value["ad"] == "changed after approval"


def test_edit_and_approval_can_be_one_atomic_write(api_db_client, ready_job):
    _project_id, job_id = ready_job
    response = _patch(
        api_db_client,
        job_id,
        {"ad": "reviewed wording", "reviewStatus": "approved", "expectedVersion": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["reviewStatus"] == "approved"
    assert body["reviewedAt"] is not None


def test_generated_is_inferred_from_no_row_and_rejected_as_a_client_transition(
    api_db_client, ready_job
):
    _project_id, job_id = ready_job
    assert _get(api_db_client, job_id).json() == {}
    response = _patch(
        api_db_client,
        job_id,
        {"ad": "human words", "reviewStatus": "generated"},
    )
    assert response.status_code == 422
    assert _get(api_db_client, job_id).json() == {}


def test_existing_row_requires_exact_positive_expected_version(api_db_client, ready_job):
    _project_id, job_id = ready_job
    assert _patch(api_db_client, job_id, {"ad": "v1"}).status_code == 200

    for payload in ({"ad": "no version"}, {"ad": "zero", "expectedVersion": 0}):
        stale = _patch(api_db_client, job_id, payload)
        assert stale.status_code == 409
        assert stale.json() == {
            "detail": {
                "code": "stale_version",
                "message": "resource changed; refresh and retry",
            }
        }

    exact = _patch(api_db_client, job_id, {"ad": "v2", "expectedVersion": 1})
    assert exact.status_code == 200
    assert exact.json()["version"] == 2

    stale = _patch(api_db_client, job_id, {"ad": "lost update", "expectedVersion": 1})
    assert stale.status_code == 409
    current = _get(api_db_client, job_id).json()["scene_1"]
    assert current["ad"] == "v2"
    assert current["version"] == 2


def test_positive_expected_version_cannot_create_missing_row(api_db_client, ready_job):
    _project_id, job_id = ready_job
    response = _patch(api_db_client, job_id, {"ad": "x", "expectedVersion": 1})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_version"
    assert _get(api_db_client, job_id).json() == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"expectedVersion": 0},
        {"ad": "x", "expectedVersion": -1},
        {"ad": "x", "expectedVersion": True},
        {"ad": "x", "expectedVersion": "1"},
        {"reviewStatus": "pending"},
        {"reviewStatus": "generated"},
        {"reviewStatus": None},
        {"ad": "x", "expected_version": 0},
        {"review_status": "approved"},
    ],
)
def test_version_and_review_contract_is_strict(api_db_client, ready_job, payload):
    _project_id, job_id = ready_job
    response = _patch(api_db_client, job_id, payload)
    assert response.status_code == 422
    assert _get(api_db_client, job_id).json() == {}


def test_same_expected_version_concurrency_has_one_winner(api_db_client, ready_job):
    from app.main import app
    from fastapi.testclient import TestClient

    _project_id, job_id = ready_job
    assert _patch(api_db_client, job_id, {"ad": "base"}).status_code == 200

    barrier = threading.Barrier(2)
    responses = [None, None]

    def writer(index, text):
        client = TestClient(app)
        barrier.wait()
        responses[index] = _patch(client, job_id, {"ad": text, "expectedVersion": 1})

    threads = [
        threading.Thread(target=writer, args=(0, "writer-a")),
        threading.Thread(target=writer, args=(1, "writer-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()["detail"][
            "code"
        ]
        == "stale_version"
    )
    current = _get(api_db_client, job_id).json()["scene_1"]
    assert current["version"] == 2
    assert current["ad"] in {"writer-a", "writer-b"}


def test_transition_wins_and_waiting_edit_rechecks_job_state(
    api_db_client, ready_job, db_engine, monkeypatch
):
    """A PATCH waits on the Job row held by a state transition, then reads
    the committed non-editable state and refuses the edit. GET stays unlocked.

    PostgreSQL's own wait-event evidence makes the ordering deterministic;
    the test does not infer serialization from thread timing alone.
    """
    import app.api.scenes as scenes_module
    from app.domain.states import JobState
    from app.models import Job
    from app.repositories.artifacts import load_job_with_project_for_update
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    _project_id, job_id = ready_job
    transition_holds_lock = threading.Event()
    release_transition = threading.Event()
    patch_finished = threading.Event()
    patch_pid: queue.Queue[int] = queue.Queue()
    response_box = []
    thread_errors = []

    def transition():
        try:
            with Session(db_engine) as session:
                job = session.execute(
                    sa.select(Job).where(Job.id == job_id).with_for_update(of=Job)
                ).scalar_one()
                job.status = JobState.FAILED.value
                session.flush()
                transition_holds_lock.set()
                if not release_transition.wait(timeout=10):
                    raise AssertionError("transition release timed out")
                session.commit()
        except BaseException as exc:  # surfaced in the main test thread
            thread_errors.append(exc)

    real_loader = load_job_with_project_for_update

    def observed_locked_loader(session, parsed_job_id):
        patch_pid.put(session.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
        return real_loader(session, parsed_job_id)

    monkeypatch.setattr(scenes_module, "load_job_with_project_for_update", observed_locked_loader)

    def edit():
        try:
            client = TestClient(api_db_client.app)
            response_box.append(_patch(client, job_id, {"ad": "must not cross boundary"}))
        except BaseException as exc:  # surfaced in the main test thread
            thread_errors.append(exc)
        finally:
            patch_finished.set()

    transition_thread = threading.Thread(target=transition)
    edit_thread = threading.Thread(target=edit)
    edit_started = False
    transition_thread.start()
    try:
        assert transition_holds_lock.wait(timeout=5)
        # GET is deliberately unlocked and remains available while the state
        # transition holds the row lock (it sees the last committed state).
        unlocked_get = _get(api_db_client, job_id)
        assert unlocked_get.status_code == 200

        edit_thread.start()
        edit_started = True
        backend_pid = patch_pid.get(timeout=5)
        deadline = time.monotonic() + 5
        wait_type = None
        with db_engine.connect() as observer:
            while time.monotonic() < deadline:
                wait_type = observer.execute(
                    sa.text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": backend_pid},
                ).scalar_one_or_none()
                if wait_type == "Lock":
                    break
                time.sleep(0.01)
        assert wait_type == "Lock"
        assert not patch_finished.is_set()
    finally:
        release_transition.set()
        transition_thread.join(timeout=10)
        if edit_started:
            edit_thread.join(timeout=10)
    assert not transition_thread.is_alive()
    assert not edit_started or not edit_thread.is_alive()
    assert not thread_errors
    assert len(response_box) == 1
    response = response_box[0]
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_not_editable"
    assert _get(api_db_client, job_id).status_code == 409
    with db_engine.begin() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM scene_overrides WHERE job_id = :jid"),
                {"jid": str(job_id)},
            ).scalar_one()
            == 0
        )


def test_generated_scenes_artifact_remains_immutable(api_db_client, ready_job, db_engine):
    _project_id, job_id = ready_job
    assert _patch(api_db_client, job_id, {"ad": "human edit"}).status_code == 200
    with db_engine.begin() as conn:
        assert (
            conn.execute(
                sa.text("SELECT count(*) FROM artifacts WHERE job_id = :jid"),
                {"jid": str(job_id)},
            ).scalar_one()
            == 0
        )
