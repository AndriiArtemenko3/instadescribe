"""G8.2 negative regressions. No test touches real Docker/S3/PostgreSQL."""

import inspect
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import g8_api_image_proof  # noqa: E402
import g8_memory_test  # noqa: E402
import smoke_local  # noqa: E402
from g8_accounting import reconcile_bucket, reconcile_database  # noqa: E402
from g8_common import CleanupError, preserve_primary_cleanup  # noqa: E402
from g8_owned_resources import (  # noqa: E402
    OWNER_LABEL,
    RUN_LABEL,
    cleanup_owned_resources,
    inspect_exact,
)
from g8_source_digest import production_source_digest  # noqa: E402


def _repo(root: Path) -> Path:
    files = {
        ".dockerignore": (
            ".git\n**/*.[pP][eE][mM]\n**/*.[kK][eE][yY]\n"
            "**/*.[pP]12\n**/*.[pP][fF][xX]\n**/*.pyc\n**/*.pyo\n"
            "**/.pytest_cache\n**/.mypy_cache\n**/.ruff_cache\n"
        ),
        "services/worker/Dockerfile": "FROM x\n",
        "services/worker/requirements.txt": "x==1\n",
        "services/worker/requirements.in": "x\n",
        "packages/investigation-core/LICENSE": "Apache License\nVersion 2.0\n",
        "packages/investigation-core/src/instadescribe_investigation_core/__init__.py": (
            '__version__ = "0.1.0"\n'
        ),
        "services/api/Dockerfile": "FROM x\n",
        "services/api/requirements.txt": "x==1\n",
        "services/api/requirements.in": "x\n",
        "services/api/app/__init__.py": "",
        "services/api/app/db/__init__.py": "",
        "services/api/app/db/base.py": "Base = object\n",
        "services/api/app/core/__init__.py": "",
        "services/api/app/core/tenancy.py": "TENANCY = 1\n",
        "services/api/app/repositories/__init__.py": "",
        "services/api/app/repositories/lifecycle.py": "REPOSITORY = 1\n",
        "services/api/app/services/__init__.py": "",
        "services/api/app/services/lifecycle.py": "LIFECYCLE = 1\n",
        "services/api/app/services/quota.py": "QUOTA = 1\n",
        "services/api/app/services/tts_previews.py": "PREVIEWS = 1\n",
        "services/api/app/domain/state.py": "STATE = 1\n",
        "services/api/app/models/job.py": "JOB = 1\n",
        "services/worker/instadescribe_worker/main.py": "MAIN = 1\n",
        "packages/contracts/instadescribe_contracts/queue.py": "QUEUE = 1\n",
        "modular_pipeline/run_job.py": "RUN = 1\n",
        "migrations/env.py": "ENV = 1\n",
        "alembic.ini": "[alembic]\n",
        "docs/evidence.md": "outside image\n",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


@pytest.mark.parametrize(
    "relative_path",
    [
        "packages/investigation-core/LICENSE",
        "packages/investigation-core/src/instadescribe_investigation_core/__init__.py",
    ],
)
def test_worker_digest_binds_investigation_core_and_nested_license(tmp_path, relative_path):
    repo = _repo(tmp_path)
    before = production_source_digest(repo, "worker")

    (repo / relative_path).write_text("changed required investigation core input\n")

    assert production_source_digest(repo, "worker") != before


@pytest.mark.parametrize("service", ["worker", "api"])
@pytest.mark.parametrize("name", ["config.yaml", "asset.bin", "page.html", "VERSION"])
def test_complete_tree_file_add_change_remove_affects_digest(tmp_path, service, name):
    repo = _repo(tmp_path)
    tree = (
        repo / "services/worker/instadescribe_worker"
        if service == "worker"
        else repo / "services/api/app"
    )
    before = production_source_digest(repo, service)
    candidate = tree / name
    candidate.write_bytes(b"one")
    added = production_source_digest(repo, service)
    assert added != before
    candidate.write_bytes(b"two")
    changed = production_source_digest(repo, service)
    assert changed != added
    candidate.unlink()
    assert production_source_digest(repo, service) == before


@pytest.mark.parametrize("service", ["worker", "api"])
def test_regular_file_mode_is_bound(tmp_path, service):
    repo = _repo(tmp_path)
    path = (
        repo / "services/worker/instadescribe_worker/main.py"
        if service == "worker"
        else repo / "services/api/app/__init__.py"
    )
    path.chmod(0o644)
    before = production_source_digest(repo, service)
    path.chmod(0o755)
    assert stat.S_IMODE(path.stat().st_mode) == 0o755
    assert production_source_digest(repo, service) != before


@pytest.mark.parametrize("service,missing", [("worker", "modular_pipeline"), ("api", "migrations")])
def test_missing_required_tree_fails(tmp_path, service, missing):
    repo = _repo(tmp_path)
    target = repo / missing
    for child in sorted(target.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    target.rmdir()
    with pytest.raises(ValueError, match="required production tree missing"):
        production_source_digest(repo, service)


@pytest.mark.parametrize("service", ["worker", "api"])
def test_symlink_fails_closed(tmp_path, service):
    repo = _repo(tmp_path)
    tree = (
        repo / "services/worker/instadescribe_worker"
        if service == "worker"
        else repo / "services/api/app"
    )
    (tree / "ambiguous").symlink_to(tree / next(iter(tree.iterdir())).name)
    with pytest.raises(ValueError, match="symlink"):
        production_source_digest(repo, service)


@pytest.mark.parametrize("service", ["worker", "api"])
def test_special_file_fails_closed(tmp_path, service):
    repo = _repo(tmp_path)
    tree = (
        repo / "services/worker/instadescribe_worker"
        if service == "worker"
        else repo / "services/api/app"
    )
    os.mkfifo(tree / "ambiguous-fifo")
    with pytest.raises(ValueError, match="special file"):
        production_source_digest(repo, service)


def test_generated_and_evidence_paths_do_not_change_worker_digest(tmp_path):
    repo = _repo(tmp_path)
    before = production_source_digest(repo, "worker")
    (repo / "docs/evidence.md").write_text("new prose\n")
    cache = repo / "modular_pipeline/__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"generated")
    jobs = repo / "modular_pipeline/jobs"
    jobs.mkdir()
    (jobs / "result.json").write_text("{}")
    assert production_source_digest(repo, "worker") == before


@pytest.mark.parametrize("service", ["worker", "api"])
def test_private_key_suffixes_are_case_insensitively_excluded(tmp_path, service):
    repo = _repo(tmp_path)
    tree = (
        repo / "services/worker/instadescribe_worker"
        if service == "worker"
        else repo / "services/api/app"
    )
    before = production_source_digest(repo, service)
    for index, suffix in enumerate((".PeM", ".KEY", ".P12", ".pFx")):
        (tree / f"forbidden-{index}{suffix}").write_bytes(b"policy-fixture")
    assert production_source_digest(repo, service) == before
    policy = (repo / ".dockerignore").read_text()
    assert all(
        pattern in policy
        for pattern in (
            "**/*.[pP][eE][mM]",
            "**/*.[kK][eE][yY]",
            "**/*.[pP]12",
            "**/*.[pP][fF][xX]",
        )
    )


@pytest.mark.parametrize("service", ["worker", "api"])
def test_generated_cache_files_match_digest_and_context_exclusions(tmp_path, service):
    repo = _repo(tmp_path)
    tree = (
        repo / "services/worker/instadescribe_worker"
        if service == "worker"
        else repo / "services/api/app"
    )
    before = production_source_digest(repo, service)
    (tree / "loose.pyc").write_bytes(b"generated")
    (tree / "loose.pyo").write_bytes(b"generated")
    for cache in (".pytest_cache", ".mypy_cache", ".ruff_cache"):
        path = tree / cache
        path.mkdir()
        (path / "entry").write_bytes(b"generated")
    assert production_source_digest(repo, service) == before
    policy = (SCRIPTS.parents[2] / ".dockerignore").read_text()
    assert all(
        pattern in policy
        for pattern in (
            "**/*.pyc",
            "**/*.pyo",
            "**/.pytest_cache",
            "**/.mypy_cache",
            "**/.ruff_cache",
        )
    )


@pytest.mark.parametrize("service", ["worker", "api"])
def test_mixed_case_bytecode_is_bound_like_the_real_context_policy(tmp_path, service):
    repo = _repo(tmp_path)
    tree = (
        repo / "services/worker/instadescribe_worker"
        if service == "worker"
        else repo / "services/api/app"
    )
    before = production_source_digest(repo, service)
    previous = before
    for index, suffix in enumerate((".PYC", ".PYO", ".PyC", ".pYo")):
        (tree / f"unusual-{index}{suffix}").write_bytes(b"bound-noncanonical-bytecode")
        current = production_source_digest(repo, service)
        assert current != previous
        previous = current

    policy = (SCRIPTS.parents[2] / ".dockerignore").read_text()
    assert "**/*.pyc" in policy and "**/*.pyo" in policy
    assert "**/*.[pP][yY][cC]" not in policy
    assert "**/*.[pP][yY][oO]" not in policy


def test_real_context_and_image_private_suffix_policies_are_case_insensitive():
    repo = SCRIPTS.parents[2]
    policy = (repo / ".dockerignore").read_text()
    assert "**/*.[pP][eE][mM]" in policy
    assert "**/*.[kK][eE][yY]" in policy
    assert "**/*.[pP]12" in policy
    assert "**/*.[pP][fF][xX]" in policy
    for dockerfile in (repo / "services/worker/Dockerfile", repo / "services/api/Dockerfile"):
        text = dockerfile.read_text()
        assert all(f"-iname '*{suffix}'" in text for suffix in (".pem", ".key", ".p12", ".pfx"))


class VersionS3:
    def __init__(self, object_pages, version_pages):
        self.object_pages = list(object_pages)
        self.version_pages = list(version_pages)
        self.object_calls = []
        self.version_calls = []

    def list_objects_v2(self, **kwargs):
        self.object_calls.append(kwargs)
        return self.object_pages.pop(0)

    def list_object_versions(self, **kwargs):
        self.version_calls.append(kwargs)
        return self.version_pages.pop(0)


def test_expected_current_object_without_version_is_rejected():
    s3 = VersionS3(
        [{"Contents": [{"Key": "a"}], "IsTruncated": False}],
        [{"Versions": [], "IsTruncated": False}],
    )
    with pytest.raises(ValueError, match="no version record"):
        reconcile_bucket(s3, "b", {"a"})


@pytest.mark.parametrize(
    "page,match",
    [
        ({"IsTruncated": True}, "usable key marker"),
        ({"IsTruncated": True, "NextKeyMarker": "a"}, "do not advance"),
    ],
)
def test_version_pagination_requires_advancing_markers(page, match):
    pages = [page]
    if page.get("NextKeyMarker"):
        pages = [page, page]
    s3 = VersionS3([{"Contents": [], "IsTruncated": False}], pages)
    with pytest.raises(ValueError, match=match):
        reconcile_bucket(s3, "b", set())


def test_same_key_multi_page_markers_are_not_stale_and_multiple_versions_fail():
    s3 = VersionS3(
        [{"Contents": [{"Key": "a"}], "IsTruncated": False}],
        [
            {
                "Versions": [{"Key": "a", "VersionId": "v2"}],
                "IsTruncated": True,
                "NextKeyMarker": "a",
                "NextVersionIdMarker": "v2",
            },
            {
                "Versions": [{"Key": "a", "VersionId": "v1"}],
                "IsTruncated": False,
            },
        ],
    )
    with pytest.raises(ValueError, match="extra object versions"):
        reconcile_bucket(s3, "b", {"a"})
    assert s3.version_calls[1]["KeyMarker"] == "a"
    assert s3.version_calls[1]["VersionIdMarker"] == "v2"


def _db() -> sa.Engine:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE jobs (id TEXT PRIMARY KEY, project_id TEXT)"))
        conn.execute(
            sa.text("CREATE TABLE artifacts (job_id TEXT, artifact_type TEXT, object_key TEXT)")
        )
        conn.execute(sa.text("CREATE TABLE scene_overrides (job_id TEXT, scene_id TEXT)"))
        conn.execute(sa.text("INSERT INTO projects VALUES ('p1')"))
        conn.execute(sa.text("INSERT INTO jobs VALUES ('j1','p1')"))
        conn.execute(sa.text("INSERT INTO artifacts VALUES ('j1','scenes_json','k1')"))
        conn.execute(sa.text("INSERT INTO scene_overrides VALUES ('j1','scene_1')"))
    return engine


def _reconcile(engine):
    return reconcile_database(
        engine,
        project_id="p1",
        job_id="j1",
        expected_artifacts={("scenes_json", "k1")},
        override_scene_id="scene_1",
    )


def test_database_reconciliation_accepts_exact_identities():
    assert _reconcile(_db()) == {"projects": 1, "jobs": 1, "artifacts": 1, "overrides": 1}


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO projects VALUES ('p2')",
        "INSERT INTO jobs VALUES ('j2','p1')",
        "INSERT INTO artifacts VALUES ('j1','extra','k2')",
        "INSERT INTO scene_overrides VALUES ('j1','scene_2')",
    ],
)
def test_database_reconciliation_rejects_every_extra_row(statement):
    engine = _db()
    with engine.begin() as conn:
        conn.execute(sa.text(statement))
    with pytest.raises(ValueError, match="identity reconciliation"):
        _reconcile(engine)


def test_database_reconciliation_rejects_wrong_count_equal_identity():
    engine = _db()
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE artifacts SET object_key='wrong'"))
    with pytest.raises(ValueError, match="artifacts"):
        _reconcile(engine)


def _validated_artifact_snapshot():
    return {"scenes_json": {"object_key": "k1"}}


def test_smoke_caller_rejects_late_extra_row_after_independent_snapshot():
    engine = _db()
    snapshot = _validated_artifact_snapshot()
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO artifacts VALUES ('j1','late_extra','k2')"))
    with pytest.raises(ValueError, match="identity reconciliation"):
        smoke_local.reconcile_smoke_database(
            engine,
            project_id="p1",
            job_id="j1",
            validated_artifacts=snapshot,
            override_scene_id="scene_1",
        )


def test_smoke_caller_rejects_count_equal_wrong_identity_after_snapshot():
    engine = _db()
    snapshot = _validated_artifact_snapshot()
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE artifacts SET object_key='wrong'"))
    with pytest.raises(ValueError, match="artifacts"):
        smoke_local.reconcile_smoke_database(
            engine,
            project_id="p1",
            job_id="j1",
            validated_artifacts=snapshot,
            override_scene_id="scene_1",
        )


def test_preserve_primary_cleanup_three_outcomes():
    with pytest.raises(CleanupError, match="cleanup-only"):
        preserve_primary_cleanup(lambda: (_ for _ in ()).throw(CleanupError("cleanup-only")), None)

    reports = []
    primary = RuntimeError("primary")
    preserve_primary_cleanup(lambda: None, primary, reports.append)
    assert reports == []
    preserve_primary_cleanup(
        lambda: (_ for _ in ()).throw(SystemExit("raw secret endpoint")), primary, reports.append
    )
    assert reports == ["secondary cleanup failure: cleanup-error:SystemExit"]
    assert "secret" not in reports[0]


def test_memory_compose_always_forces_g8_override(monkeypatch):
    calls = []

    def fake(project, *args, **kwargs):
        calls.append((project, args, kwargs))
        return "ok"

    monkeypatch.setattr(g8_memory_test, "compose", fake)
    assert g8_memory_test.memory_compose("ps", "-q", "api", env={}) == "ok"
    assert calls == [(g8_memory_test.PROJECT, ("ps", "-q", "api"), {"env": {}, "g8_images": True})]


def test_memory_main_orchestration_uses_exact_g8_paths_and_both_api_assertions(monkeypatch):
    events = []

    def fake_preflight(project, **kwargs):
        events.append(("preflight", project, kwargs))

    def fake_compose(project, *args, **kwargs):
        events.append(("compose", project, args, kwargs))
        if args[-3:] == ("ps", "-q", "api"):
            return "api-container\n"
        if args[-3:] == ("ps", "-q", "worker"):
            return "worker-container\n"
        return ""

    def fake_assert(container, image, role):
        events.append(("assert", container, image, role))

    def fake_cleanup(project, **kwargs):
        events.append(("teardown", project, kwargs))

    monkeypatch.setattr(g8_memory_test, "preflight", fake_preflight)
    monkeypatch.setattr(g8_memory_test, "compose", fake_compose)
    monkeypatch.setattr(g8_memory_test, "assert_running_image", fake_assert)
    monkeypatch.setattr(g8_memory_test, "cleanup_compose_project", fake_cleanup)

    env = {"sentinel": "1"}
    g8_memory_test.prepare_stack(env)
    g8_memory_test.start_base_stack(env, "api-image")
    assert g8_memory_test.start_worker_stack(env, "api-image", "worker-image") == "worker-container"
    g8_memory_test.stop_worker_stack(env)
    g8_memory_test.teardown_stack(env)

    assert events[0] == (
        "preflight",
        g8_memory_test.PROJECT,
        {"env": env, "g8_images": True},
    )
    compose_events = [event for event in events if event[0] == "compose"]
    assert len(compose_events) == 6
    assert all(event[3]["g8_images"] is True for event in compose_events)
    assert [event for event in events if event[0] == "assert"] == [
        ("assert", "api-container", "api-image", "api-initial"),
        ("assert", "api-container", "api-image", "api-post-worker"),
        ("assert", "worker-container", "worker-image", "worker"),
    ]
    assert events[-1] == (
        "teardown",
        g8_memory_test.PROJECT,
        {"env": env, "g8_images": True},
    )
    main_source = inspect.getsource(g8_memory_test.main)
    assert all(
        call in main_source
        for call in (
            "prepare_stack(env)",
            'start_base_stack(env, api_binding["image_id"])',
            'start_worker_stack(env, api_binding["image_id"], image_id)',
            "stop_worker_stack(env)",
            "teardown_stack(env)",
        )
    )


def test_mismatched_running_api_image_fails(monkeypatch):
    monkeypatch.setattr(g8_memory_test, "container_state", lambda unused: {"image": "wrong"})
    with pytest.raises(SystemExit):
        g8_memory_test.assert_running_image("api", "expected", "api")


class DockerState:
    def __init__(self, unowned=None, fail_remove=None, keep=None, query_fail=None):
        self.resources = {
            ("container", "api"): ("cid-api", "owner", "run"),
            ("container", "pg"): ("cid-pg", "owner", "run"),
            ("network", "net"): ("nid", "owner", "run"),
            ("volume", "vol"): ("vol", "owner", "run"),
        }
        self.unowned = unowned
        self.fail_remove = fail_remove
        self.keep = keep
        self.query_fail = query_fail
        self.removes = []

    def __call__(self, cmd):
        kind = cmd[1]
        action = cmd[2]
        if action == "inspect":
            name = cmd[3]
            if self.query_fail == (kind, name):
                return SimpleNamespace(returncode=2, stdout="", stderr="daemon unavailable")
            item = self.resources.get((kind, name))
            if item is None:
                missing = {
                    "container": f"Error response from daemon: No such container: {name}\n",
                    "network": f"Error response from daemon: network {name} not found\n",
                    "volume": f"Error response from daemon: get {name}: no such volume\n",
                }
                return SimpleNamespace(returncode=1, stdout="[]\n", stderr=missing[kind])
            identifier, owner, run = item
            if self.unowned == (kind, name):
                owner = "someone-else"
            labels = {OWNER_LABEL: owner, RUN_LABEL: run}
            inspected_name = f"/{name}" if kind == "container" else name
            payload = {"Id": identifier, "Name": inspected_name, "Config": {"Labels": labels}}
            if kind in {"network", "volume"}:
                payload["Labels"] = labels
            return SimpleNamespace(returncode=0, stdout=json.dumps([payload]), stderr="")
        target = cmd[-1]
        self.removes.append((kind, target))
        if self.fail_remove == kind:
            return SimpleNamespace(returncode=1, stdout="", stderr="raw endpoint secret")
        if self.keep != kind:
            for key, value in list(self.resources.items()):
                identifier = value[0]
                if key[0] == kind and target in (key[1], identifier):
                    del self.resources[key]
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _owned_cleanup(state):
    cleanup_owned_resources(
        owner="owner",
        containers=["api", "pg"],
        network="net",
        volume="vol",
        run_id="run",
        runner=state,
    )


def test_owned_cleanup_removes_containers_network_and_volume_by_inspected_identity():
    state = DockerState()
    _owned_cleanup(state)
    assert ("container", "cid-api") in state.removes
    assert ("container", "cid-pg") in state.removes
    assert ("network", "nid") in state.removes
    assert ("volume", "vol") in state.removes


@pytest.mark.parametrize("collision", [("container", "api"), ("network", "net"), ("volume", "vol")])
def test_unowned_collision_is_never_deleted(collision):
    state = DockerState(unowned=collision)
    with pytest.raises(CleanupError, match="unowned"):
        _owned_cleanup(state)
    assert not any(
        kind == collision[0] and target in {collision[1], "cid-api", "nid"}
        for kind, target in state.removes
    )


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
def test_remove_failure_is_sanitized_and_all_owned_removals_attempted(kind):
    state = DockerState(fail_remove=kind)
    with pytest.raises(CleanupError) as captured:
        _owned_cleanup(state)
    assert "raw endpoint" not in str(captured.value)
    assert {item[0] for item in state.removes} == {"container", "network", "volume"}


@pytest.mark.parametrize("kind,name", [("container", "api"), ("network", "net"), ("volume", "vol")])
def test_query_failure_fails_closed(kind, name):
    with pytest.raises(CleanupError, match="query:nonzero"):
        _owned_cleanup(DockerState(query_fail=(kind, name)))


@pytest.mark.parametrize("kind", ["container", "network", "volume"])
def test_surviving_resource_residue_fails(kind):
    with pytest.raises(CleanupError, match="residue"):
        _owned_cleanup(DockerState(keep=kind))


def _inspect_result(kind, name, *, returned_name=None, identifier="immutable-id"):
    labels = {OWNER_LABEL: "owner", RUN_LABEL: "run"}
    payload = {
        "Name": returned_name
        if returned_name is not None
        else (f"/{name}" if kind == "container" else name),
        "Id": identifier,
        "Config": {"Labels": labels},
        "Labels": labels,
    }
    return SimpleNamespace(returncode=0, stdout=json.dumps([payload]), stderr="")


@pytest.mark.parametrize(
    "kind,name,stderr",
    [
        ("container", "api.g8", "Error response from daemon: No such container: api.g8\n"),
        ("network", "net", "Error response from daemon: network net not found\r\n"),
        ("volume", "vol", "Error response from daemon: get vol: no such volume\n"),
    ],
)
def test_exact_canonical_docker_absence_is_accepted(kind, name, stderr):
    result = SimpleNamespace(returncode=1, stdout="[]\n", stderr=stderr)
    assert inspect_exact(kind, name, "owner", runner=lambda unused: result) is None


@pytest.mark.parametrize(
    "kind,name,returncode,stderr",
    [
        ("container", "api", 2, "Error response from daemon: No such container: api\n"),
        ("network", "net", 125, "Error response from daemon: network net not found\n"),
        ("volume", "vol", 137, "Error response from daemon: get vol: no such volume\n"),
    ],
)
def test_canonical_absence_text_with_noncanonical_returncode_fails_closed(
    kind, name, returncode, stderr
):
    result = SimpleNamespace(returncode=returncode, stdout="[]\n", stderr=stderr)
    with pytest.raises(CleanupError, match=rf"^{kind}-query:nonzero$") as captured:
        inspect_exact(kind, name, "owner", runner=lambda unused: result)
    assert stderr not in str(captured.value)


@pytest.mark.parametrize(
    "kind,name,stderr",
    [
        ("container", "api", "daemon endpoint not found"),
        ("container", "api", "socket not found"),
        ("container", "api", "authorization cache: object not found"),
        ("container", "api", "Error response from daemon: No such container: other"),
        ("container", "api.g8", "Error response from daemon: No such container: apiXg8"),
        ("network", "net", "Error response from daemon: No such container: net"),
        ("volume", "vol", " Error response from daemon: get vol: no such volume"),
        ("volume", "vol", "Error response from daemon: get vol: no such volume "),
        ("volume", "vol", "Error response from daemon: get vol: no such volume\npermission denied"),
    ],
)
def test_noncanonical_absence_text_fails_closed_sanitized(kind, name, stderr):
    result = SimpleNamespace(returncode=1, stdout="[]\n", stderr=stderr)
    with pytest.raises(CleanupError, match=rf"^{kind}-query:nonzero$") as captured:
        inspect_exact(kind, name, "owner", runner=lambda unused: result)
    assert stderr not in str(captured.value)


@pytest.mark.parametrize(
    "kind,name,returned",
    [
        ("container", "api", "api"),
        ("container", "api", "/other"),
        ("network", "net", "/net"),
        ("network", "net", "other"),
        ("volume", "vol", "/vol"),
        ("volume", "vol", "other"),
    ],
)
def test_inspected_resource_name_must_match_exact_kind_shape(kind, name, returned):
    result = _inspect_result(kind, name, returned_name=returned)
    with pytest.raises(CleanupError, match=rf"^{kind}-query:name-mismatch$"):
        inspect_exact(kind, name, "owner", runner=lambda unused: result)


def test_api_proof_inventory_commands_are_named_labelled_and_never_auto_removed():
    assert set(g8_api_image_proof.PROOF_CONTAINERS) == {
        g8_api_image_proof.API_NAME,
        g8_api_image_proof.PG_NAME,
        g8_api_image_proof.UID_NAME,
        g8_api_image_proof.CHECKS_NAME,
        g8_api_image_proof.HEADS_NAME,
        g8_api_image_proof.MIGRATE_NAME,
    }
    for name in g8_api_image_proof.PROOF_CONTAINERS:
        command = g8_api_image_proof.owned_container_command(name, "image")
        assert command[:4] == ["docker", "run", "--name", name]
        assert "--rm" not in command
        assert f"{OWNER_LABEL}={g8_api_image_proof.OWNER}" in command
        assert f"{RUN_LABEL}={g8_api_image_proof.RUN_ID}" in command


def test_api_proof_locks_before_first_docker_lookup_and_cleans_on_interruption(monkeypatch):
    events = []

    monkeypatch.setattr(
        g8_api_image_proof, "acquire_gate_lock", lambda unused: events.append("lock")
    )

    def fake_cleanup(current_run=False):
        events.append("cleanup-current" if current_run else "cleanup-stale")

    def interrupted_run(command, **unused):
        events.append(("docker", tuple(command)))
        raise RuntimeError("injected interruption")

    monkeypatch.setattr(g8_api_image_proof, "cleanup", fake_cleanup)
    monkeypatch.setattr(g8_api_image_proof, "run", interrupted_run)
    with pytest.raises(RuntimeError, match="injected interruption"):
        g8_api_image_proof.main()
    assert events[0:2] == ["lock", "cleanup-stale"]
    assert events[2][0] == "docker"
    assert events[-1] == "cleanup-current"
