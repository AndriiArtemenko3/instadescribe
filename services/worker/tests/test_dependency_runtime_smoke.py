"""Fast contract tests for the dependency runtime smoke orchestrator.

The special dependency job and G8 image proof execute the real heavyweight
operations.  These tests keep dispatch and evidence parsing deterministic in
the normal API test environment, which intentionally does not install Torch.
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dependency_runtime_smoke as smoke  # noqa: E402
import g8_image_proof  # noqa: E402


def _fake_runtime_source_graph(tmp_path: Path, *, include_core: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    script = repo / "services" / "worker" / "scripts" / "dependency_runtime_smoke.py"
    script.parent.mkdir(parents=True)
    (repo / "packages" / "contracts").mkdir(parents=True)
    (repo / "services" / "api").mkdir(parents=True)
    if include_core:
        (repo / "packages" / "investigation-core" / "src").mkdir(parents=True)
    return repo, script


def test_repo_bootstrap_exposes_investigation_core_source_root(monkeypatch, tmp_path):
    repo, script = _fake_runtime_source_graph(tmp_path)
    existing_path = ["pre-existing-path"]
    monkeypatch.setattr(smoke, "__file__", str(script))
    monkeypatch.setattr(sys, "path", existing_path.copy())

    smoke._bootstrap_repo_imports()

    assert sys.path == [
        str(repo),
        str(repo / "services" / "worker"),
        str(repo / "packages" / "contracts"),
        str(repo / "services" / "api"),
        str(repo / "packages" / "investigation-core" / "src"),
        *existing_path,
    ]


def test_repo_bootstrap_requires_complete_runtime_source_graph(monkeypatch, tmp_path):
    _, script = _fake_runtime_source_graph(tmp_path, include_core=False)
    existing_path = ["pre-existing-path"]
    monkeypatch.setattr(smoke, "__file__", str(script))
    monkeypatch.setattr(sys, "path", existing_path.copy())

    smoke._bootstrap_repo_imports()

    assert sys.path == existing_path


def test_worker_profile_does_not_cross_local_audio_boundary(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(smoke, "run_worker_checks", lambda: calls.append("worker") or {"w": 1})
    monkeypatch.setattr(
        smoke, "run_local_audio_checks", lambda: calls.append("local-audio") or {"a": 1}
    )

    result = smoke.run_smoke("worker")

    assert calls == ["worker"]
    assert result == {"status": "ok", "profile": "worker", "checks": {"worker": {"w": 1}}}


def test_all_profile_runs_both_explicit_boundaries(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(smoke, "run_worker_checks", lambda: calls.append("worker") or {"w": 1})
    monkeypatch.setattr(
        smoke, "run_local_audio_checks", lambda: calls.append("local-audio") or {"a": 1}
    )

    result = smoke.run_smoke("all")

    assert calls == ["worker", "local-audio"]
    assert result["checks"] == {"worker": {"w": 1}, "local_audio": {"a": 1}}


def test_unknown_profile_fails_closed():
    with pytest.raises(ValueError, match="unknown dependency smoke profile"):
        smoke.run_smoke("almost-worker")


def test_output_parser_uses_marked_machine_readable_line():
    payload = {"status": "ok", "profile": "worker", "checks": {"worker": {}}}
    output = "library diagnostic\n" + smoke.OUTPUT_PREFIX + json.dumps(payload) + "\n"

    assert smoke.parse_smoke_output(output) == payload


@pytest.mark.parametrize(
    "output",
    ["", "{}", smoke.OUTPUT_PREFIX + "[]"],
)
def test_output_parser_rejects_missing_or_non_object_evidence(output):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        smoke.parse_smoke_output(output)


def test_g8_image_proof_runs_worker_smoke_networkless_and_read_only(monkeypatch):
    calls: list[tuple[list[str], int]] = []

    def fake_run(command: list[str], timeout: int = 900) -> str:
        calls.append((command, timeout))
        return "proof-output"

    monkeypatch.setattr(g8_image_proof, "run", fake_run)

    assert g8_image_proof.dependency_smoke_in_image(timeout=123) == "proof-output"
    assert len(calls) == 1
    command, timeout = calls[0]
    assert timeout == 123
    assert command[:7] == [
        "docker",
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
    ]
    assert "--mount" in command
    mount = command[command.index("--mount") + 1]
    assert f"source={g8_image_proof.DEPENDENCY_SMOKE}" in mount
    assert "target=/tmp/dependency_runtime_smoke.py" in mount
    assert mount.endswith(",readonly")
    assert command[-2:] == ["--profile", "worker"]
