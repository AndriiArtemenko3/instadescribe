"""G8.1 focused tests for the acceptance-gate tooling (Parts B–E).

Everything here is injected/mocked — no test starts, stops or deletes any
real Docker resource, and no real bucket or queue is touched.
"""

import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from g8_accounting import reconcile_bucket  # noqa: E402
from g8_common import (  # noqa: E402
    CleanupError,
    GateLockError,
    acquire_gate_lock,
    assert_no_residue,
    checked_query,
    gate_lock_path,
    project_containers,
)
from g8_image_proof import IMAGE_REF_RE, compressed_image_size  # noqa: E402
from g8_log_order import assert_ready_before_ack, parse_worker_events  # noqa: E402
from g8_source_digest import iter_production_inputs, production_source_digest  # noqa: E402

# ── B1: production-input digest ─────────────────────────────────────────────


def _fake_repo(tmp_path: Path) -> Path:
    (tmp_path / "services/worker/instadescribe_worker").mkdir(parents=True)
    (tmp_path / "services/api/app/models").mkdir(parents=True)
    (tmp_path / "services/api/app/domain").mkdir(parents=True)
    (tmp_path / "packages/contracts/instadescribe_contracts").mkdir(parents=True)
    (tmp_path / "modular_pipeline").mkdir(parents=True)
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "services/worker/Dockerfile").write_text("FROM x\n")
    (tmp_path / "services/worker/requirements.txt").write_text("torch==1\n")
    (tmp_path / "services/worker/requirements.in").write_text("torch\n")
    (tmp_path / ".dockerignore").write_text(".git\n")
    (tmp_path / "services/api/app/__init__.py").write_text("")
    (tmp_path / "services/api/app/db").mkdir(parents=True)
    (tmp_path / "services/api/app/db/__init__.py").write_text("")
    (tmp_path / "services/api/app/db/base.py").write_text("Base = object\n")
    (tmp_path / "services/api/app/core").mkdir(parents=True)
    (tmp_path / "services/api/app/core/__init__.py").write_text("")
    (tmp_path / "services/api/app/core/tenancy.py").write_text("TENANCY = 1\n")
    (tmp_path / "services/api/app/repositories").mkdir(parents=True)
    (tmp_path / "services/api/app/repositories/__init__.py").write_text("")
    (tmp_path / "services/api/app/repositories/lifecycle.py").write_text("REPOSITORY = 1\n")
    (tmp_path / "services/api/app/services").mkdir(parents=True)
    (tmp_path / "services/api/app/services/__init__.py").write_text("")
    (tmp_path / "services/api/app/services/lifecycle.py").write_text("LIFECYCLE = 1\n")
    (tmp_path / "services/api/app/services/quota.py").write_text("QUOTA = 1\n")
    (tmp_path / "services/api/app/services/tts_previews.py").write_text("PREVIEWS = 1\n")
    (tmp_path / "services/worker/instadescribe_worker/consumer.py").write_text("A = 1\n")
    (tmp_path / "services/api/app/models/job.py").write_text("B = 1\n")
    (tmp_path / "packages/contracts/instadescribe_contracts/queue.py").write_text("C = 1\n")
    (tmp_path / "modular_pipeline/run_job.py").write_text("D = 1\n")
    (tmp_path / "docs/evidence.md").write_text("evidence only\n")
    return tmp_path


def test_digest_changes_on_relevant_file_change(tmp_path):
    repo = _fake_repo(tmp_path)
    before = production_source_digest(repo, "worker")
    (repo / "services/worker/instadescribe_worker/consumer.py").write_text("A = 2\n")
    assert production_source_digest(repo, "worker") != before


def test_digest_changes_on_dockerignore_change(tmp_path):
    repo = _fake_repo(tmp_path)
    before = production_source_digest(repo, "worker")
    (repo / ".dockerignore").write_text(".git\nextra\n")
    assert production_source_digest(repo, "worker") != before


def test_digest_ignores_evidence_only_documentation(tmp_path):
    repo = _fake_repo(tmp_path)
    before = production_source_digest(repo, "worker")
    (repo / "docs/evidence.md").write_text("rewritten evidence prose\n")
    (repo / "docs/new-evidence.md").write_text("brand new evidence file\n")
    assert production_source_digest(repo, "worker") == before


def test_digest_is_deterministic_and_covers_copied_api_subset(tmp_path):
    repo = _fake_repo(tmp_path)
    assert production_source_digest(repo, "worker") == production_source_digest(repo, "worker")
    before = production_source_digest(repo, "worker")
    (repo / "services/api/app/models/job.py").write_text("B = 99\n")
    assert production_source_digest(repo, "worker") != before
    preview_bound = production_source_digest(repo, "worker")
    (repo / "services/api/app/services/tts_previews.py").write_text("PREVIEWS = 2\n")
    assert production_source_digest(repo, "worker") != preview_bound


def test_digest_pycache_never_participates(tmp_path):
    repo = _fake_repo(tmp_path)
    before = production_source_digest(repo, "worker")
    cache = repo / "services/worker/instadescribe_worker/__pycache__"
    cache.mkdir()
    (cache / "consumer.cpython-312.pyc").write_bytes(b"\x00")
    assert production_source_digest(repo, "worker") == before
    files = list(iter_production_inputs(repo, "worker"))
    assert not any("__pycache__" in p.parts for p in files)


# ── C: gate lock ────────────────────────────────────────────────────────────


def test_gate_lock_contention_fails_second_acquirer(tmp_path, monkeypatch):
    first = acquire_gate_lock("test-gate-contention", lock_dir=tmp_path)
    assert first >= 0
    # A second invocation — even from a DIFFERENT cwd/worktree-style path —
    # resolves the SAME lock file and must fail before touching anything.
    wt_a, wt_b = tmp_path / "worktree-a", tmp_path / "worktree-b"
    wt_a.mkdir()
    wt_b.mkdir()
    monkeypatch.chdir(wt_a)
    path_a = gate_lock_path("test-gate-contention", lock_dir=tmp_path)
    monkeypatch.chdir(wt_b)
    path_b = gate_lock_path("test-gate-contention", lock_dir=tmp_path)
    assert path_a == path_b  # stable across worktrees/checkouts
    with pytest.raises(GateLockError):
        acquire_gate_lock("test-gate-contention", lock_dir=tmp_path)


def test_gate_lock_is_per_gate(tmp_path):
    acquire_gate_lock("test-gate-one", lock_dir=tmp_path)
    acquire_gate_lock("test-gate-two", lock_dir=tmp_path)  # different gate: fine


# ── C: label discovery + fail-closed cleanup (injected Docker results) ─────


def _fake_runner(stdout="", returncode=0, raises=None):
    def runner(cmd):
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    return runner


def test_orphan_profiled_container_is_discovered():
    runner = _fake_runner(stdout="abc123\ndef456\n")
    assert project_containers("some-project", runner) == ["abc123", "def456"]


def test_container_residue_fails(tmp_path):
    runner = _fake_runner(stdout="leftover\n")
    with pytest.raises(CleanupError, match="container-residue"):
        assert_no_residue("some-project", runner)


def test_network_and_volume_residue_fail():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        if "network" in cmd:
            return SimpleNamespace(returncode=0, stdout="netid\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(CleanupError, match="network-residue"):
        assert_no_residue("some-project", runner)

    def runner2(cmd):
        if "volume" in cmd:
            return SimpleNamespace(returncode=0, stdout="volid\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(CleanupError, match="volume-residue"):
        assert_no_residue("some-project", runner2)


def test_docker_query_failure_fails_closed():
    with pytest.raises(CleanupError, match="docker-query-nonzero"):
        checked_query(["docker", "ps"], _fake_runner(returncode=1))
    with pytest.raises(CleanupError, match="docker-query-error"):
        checked_query(["docker", "ps"], _fake_runner(raises=OSError("daemon down")))
    with pytest.raises(CleanupError):
        assert_no_residue("some-project", _fake_runner(returncode=125))


def test_cleanup_failure_preserves_primary_failure_category():
    # The runtime scripts catch CleanupError in their finally blocks and
    # report it by category while the primary failure still decides the
    # exit — here we prove the category shape carries no raw output.
    try:
        assert_no_residue("p", _fake_runner(raises=ConnectionError("http://secret:2375")))
    except CleanupError as exc:
        assert "secret" not in str(exc)
        assert str(exc).startswith("docker-query-error:")
    else:
        pytest.fail("expected CleanupError")


# ── D2: queue triple ────────────────────────────────────────────────────────


def test_delayed_message_counts_as_residue():
    from g8_common import queue_attrs

    class FakeSQS:
        def get_queue_url(self, QueueName):
            return {"QueueUrl": "q"}

        def get_queue_attributes(self, QueueUrl, AttributeNames):
            assert "ApproximateNumberOfMessagesDelayed" in AttributeNames
            return {
                "Attributes": {
                    "ApproximateNumberOfMessages": "0",
                    "ApproximateNumberOfMessagesNotVisible": "0",
                    "ApproximateNumberOfMessagesDelayed": "1",
                }
            }

    assert queue_attrs(FakeSQS(), "any") == ("0", "0", "1")  # caller must fail on != 0,0,0


# ── D3: structured log order ───────────────────────────────────────────────


def _log(event, job="j1", attempt=1, extra=""):
    return f'worker-1  | {{"event": "{event}", "job_id": "{job}", "attempt": {attempt}{extra}}}'


def test_ready_before_ack_valid_order_passes():
    logs = "\n".join(
        [
            _log("job_claimed"),
            _log("job_ready"),
            _log("message_success"),
        ]
    )
    evidence = assert_ready_before_ack(logs, "j1")
    assert "job_ready[1] -> message_success[2]" in evidence


def test_ack_before_ready_rejected():
    logs = "\n".join([_log("message_success"), _log("job_ready")])
    with pytest.raises(ValueError, match="does not occur after"):
        assert_ready_before_ack(logs, "j1")


def test_missing_duplicate_or_mismatched_events_rejected():
    with pytest.raises(ValueError, match="exactly one job_ready"):
        assert_ready_before_ack(_log("message_success"), "j1")
    logs = "\n".join([_log("job_ready"), _log("job_ready"), _log("message_success")])
    with pytest.raises(ValueError, match="exactly one job_ready"):
        assert_ready_before_ack(logs, "j1")
    logs = "\n".join([_log("job_ready", attempt=1), _log("message_success", attempt=2)])
    with pytest.raises(ValueError, match="attempt mismatch"):
        assert_ready_before_ack(logs, "j1")
    # Substring presence in prose must NOT satisfy the structured proof.
    prose = "worker-1  | plain text mentioning job_ready and message_success"
    with pytest.raises(ValueError):
        assert_ready_before_ack(prose, "j1")


def test_success_ack_pending_always_rejects():
    logs = "\n".join(
        [
            _log("job_ready"),
            _log("success_ack_pending"),
            _log("message_success"),
        ]
    )
    with pytest.raises(ValueError, match="success_ack_pending"):
        assert_ready_before_ack(logs, "j1")


def test_parse_ignores_unstructured_lines():
    logs = "random noise\n" + _log("job_ready") + "\n{not json}"
    events = parse_worker_events(logs)
    assert [e["event"] for e in events] == ["job_ready"]


# ── D4: bucket reconciliation (mocked S3) ──────────────────────────────────


class FakeS3:
    def __init__(self, keys, versions=None, markers=None, page_size=2):
        self.keys = keys
        self.versions = versions if versions is not None else {k: 1 for k in keys}
        self.markers = markers or []
        self.page_size = page_size

    def list_objects_v2(self, Bucket, ContinuationToken=None):
        start = int(ContinuationToken or 0)
        chunk = self.keys[start : start + self.page_size]
        truncated = start + self.page_size < len(self.keys)
        page = {"Contents": [{"Key": k} for k in chunk], "IsTruncated": truncated}
        if truncated:
            page["NextContinuationToken"] = str(start + self.page_size)
        return page

    def list_object_versions(self, Bucket, KeyMarker=None, VersionIdMarker=None):
        flat = [k for k, n in sorted(self.versions.items()) for _ in range(n)]
        start = int(KeyMarker or 0)
        chunk = flat[start : start + self.page_size]
        truncated = start + self.page_size < len(flat)
        page = {
            "Versions": [{"Key": k, "VersionId": f"v{i}"} for i, k in enumerate(chunk)],
            "DeleteMarkers": [{"Key": m} for m in (self.markers if start == 0 else [])],
            "IsTruncated": truncated,
        }
        if truncated:
            page["NextKeyMarker"] = str(start + self.page_size)
        return page


def test_reconcile_accepts_exact_paginated_bucket():
    keys = ["uploads/a", "jobs/j/attempts/1/analysis/scenes.json", "jobs/j/attempts/1/p.jpg"]
    account = reconcile_bucket(FakeS3(keys), "b", set(keys))
    assert account["objects"] == 3 and account["delete_markers"] == 0


def test_reconcile_rejects_extra_object_missing_object_and_markers():
    keys = ["uploads/a", "uploads/rogue"]
    with pytest.raises(ValueError, match="unknown objects"):
        reconcile_bucket(FakeS3(keys), "b", {"uploads/a"})
    with pytest.raises(ValueError, match="missing"):
        reconcile_bucket(FakeS3(["uploads/a"]), "b", {"uploads/a", "uploads/gone"})
    with pytest.raises(ValueError, match="delete markers"):
        reconcile_bucket(FakeS3(["uploads/a"], markers=["uploads/a"]), "b", {"uploads/a"})


def test_reconcile_rejects_extra_version_and_unknown_version():
    with pytest.raises(ValueError, match="extra object versions"):
        reconcile_bucket(FakeS3(["uploads/a"], versions={"uploads/a": 2}), "b", {"uploads/a"})
    with pytest.raises(ValueError, match="unknown keys"):
        reconcile_bucket(
            FakeS3(["uploads/a"], versions={"uploads/a": 1, "ghost": 1}),
            "b",
            {"uploads/a"},
        )


# ── E: shell-free compressed size ──────────────────────────────────────────


def test_compressed_size_streams_and_checks_return_code():
    payload = b"layer-bytes " * 4096

    class FakeProc:
        def __init__(self):
            import io

            self.stdout = io.BytesIO(payload)
            self.returncode = 0

        def wait(self):
            return self.returncode

    size = compressed_image_size("instadescribe-worker:g8", popen=lambda *a, **k: FakeProc())
    assert 0 < size < len(payload)
    # Sanity: the count equals a real gzip-container compression of the bytes.
    ref = zlib.compressobj(6, zlib.DEFLATED, 31)
    expected = len(ref.compress(payload)) + len(ref.flush())
    assert size == expected


def test_docker_save_failure_cannot_produce_a_size():
    class FailingProc:
        def __init__(self):
            import io

            self.stdout = io.BytesIO(b"partial")
            self.returncode = 1

        def wait(self):
            return self.returncode

    with pytest.raises(RuntimeError, match="docker save failed"):
        compressed_image_size("instadescribe-worker:g8", popen=lambda *a, **k: FailingProc())


def test_shell_metacharacters_rejected_before_execution():
    calls = []
    for evil in ("img; rm -rf /", "img$(reboot)", "img|cat", "img `x`", "img>out"):
        with pytest.raises(ValueError, match="invalid image reference"):
            compressed_image_size(evil, popen=lambda *a, **k: calls.append(a))
    assert calls == []  # nothing was ever executed
    assert IMAGE_REF_RE.fullmatch("instadescribe-worker:g8")
    assert IMAGE_REF_RE.fullmatch("instadescribe-worker@sha256:" + "a" * 64)


# ── B2: aggregate target shape (static, no execution) ──────────────────────


def test_g8_acceptance_target_is_strictly_sequential():
    makefile = (Path(__file__).resolve().parents[3] / "Makefile").read_text()
    recipe = makefile.split("g8-acceptance:")[1]
    steps = [
        line.strip().replace("$(MAKE) ", "")
        for line in recipe.splitlines()[1:]
        if line.strip().startswith("$(MAKE)")
    ]
    assert steps == [
        "g8-build",
        "g8-image-proof",
        "g8-api-build",
        "g8-api-image-proof",
        "g8-memtest",
        "smoke-local",
    ]
    # No parallel prerequisites: the target line itself declares none.
    target_line = [line for line in makefile.splitlines() if line.startswith("g8-acceptance:")][0]
    assert target_line.split("##")[0].strip() == "g8-acceptance:"
