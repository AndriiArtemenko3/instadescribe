"""Process-tree and wall-clock guarantees for the production renderer."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from instadescribe_worker.config import WorkerSettings
from instadescribe_worker.render import (
    RenderCancelled,
    RenderWorkerFailure,
    _render_child_environment,
    _run_default_renderer,
)

_BLOCKING_TREE = """
import json, os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
grand = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)",
])
pathlib.Path(sys.argv[1]).write_text(json.dumps({"child": os.getpid(), "grand": grand.pid}))
time.sleep(300)
"""


def _settings(**updates) -> WorkerSettings:
    return WorkerSettings().model_copy(
        update={
            "provider": "fake",
            "render_timeout_secs": 1,
            "grace_secs": 1,
            **updates,
        }
    )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _tree_pids(path: Path, deadline_secs: float = 3) -> dict[str, int]:
    deadline = time.monotonic() + deadline_secs
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.02)
    raise AssertionError("renderer child never recorded its process tree")


def _assert_tree_gone(pids: dict[str, int], deadline_secs: float = 5) -> None:
    deadline = time.monotonic() + deadline_secs
    while time.monotonic() < deadline:
        if not _pid_alive(pids["child"]) and not _pid_alive(pids["grand"]):
            return
        time.sleep(0.05)
    raise AssertionError("renderer process tree survived termination")


def _invoke(settings: WorkerSettings, workspace: Path, heartbeat) -> dict[str, Path]:
    return _run_default_renderer(
        settings,
        object(),  # heartbeat test doubles never access the SQLAlchemy session
        heartbeat,
        source_video=workspace / "source.mp4",
        scenes=[],
        entities_by_id={},
        output_dir=workspace / "deliverables",
        project_name="Bounded render",
        default_voice="onyx",
    )


def test_render_child_environment_excludes_database_and_aws_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-cross")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross")

    environment = _render_child_environment(_settings(), tmp_path)

    assert environment["INSTADESCRIBE_BACKEND"] == "fake"
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "DATABASE_URL" not in environment
    assert "OPENAI_API_KEY" not in environment


def test_isolated_render_child_loads_explicit_pipeline_and_contract_sources(tmp_path):
    pipeline = tmp_path / "pipeline"
    providers = pipeline / "providers"
    providers.mkdir(parents=True)
    (providers / "__init__.py").write_text("", encoding="utf-8")
    (providers / "factory.py").write_text(
        "def set_active_backend(name):\n    assert name == 'fake'\n",
        encoding="utf-8",
    )
    (pipeline / "bundle_export.py").write_text(
        """
from instadescribe_contracts.provider import TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW

def render_all_deliverables(**kwargs):
    assert TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW > 0
    names = {
        "mp4": "described_video.mp4",
        "mp3": "audio_description.mp3",
        "srt": "audio_description.srt",
        "csv": "audio_description.csv",
        "docx": "audio_description.docx",
    }
    output = kwargs["output_dir"]
    output.mkdir(parents=True)
    for kind, name in names.items():
        (output / name).write_bytes(kind.encode())
""",
        encoding="utf-8",
    )

    class Healthy:
        def assert_healthy(self, _session):
            return None

    outputs = _invoke(
        _settings(render_timeout_secs=30, pipeline_source=str(pipeline)),
        tmp_path,
        Healthy(),
    )

    assert set(outputs) == {"mp4", "mp3", "srt", "csv", "docx"}
    assert all(path.read_bytes() == kind.encode() for kind, path in outputs.items())


def test_render_deadline_terms_kills_and_reaps_the_whole_process_group(monkeypatch, tmp_path):
    script = tmp_path / "blocking-render.py"
    pids_path = tmp_path / "renderer-pids.json"
    script.write_text(_BLOCKING_TREE, encoding="utf-8")
    monkeypatch.setattr(
        "instadescribe_worker.render._render_child_command",
        lambda _request: [sys.executable, str(script), str(pids_path)],
    )

    class Healthy:
        def assert_healthy(self, _session):
            return None

    with pytest.raises(RenderWorkerFailure, match="render_failed") as exc:
        _invoke(_settings(), tmp_path, Healthy())

    assert "deadline" in exc.value.public_message
    _assert_tree_gone(_tree_pids(pids_path))


def test_fence_loss_terms_kills_and_reaps_the_whole_process_group(monkeypatch, tmp_path):
    script = tmp_path / "blocking-render.py"
    pids_path = tmp_path / "renderer-pids.json"
    script.write_text(_BLOCKING_TREE, encoding="utf-8")
    monkeypatch.setattr(
        "instadescribe_worker.render._render_child_command",
        lambda _request: [sys.executable, str(script), str(pids_path)],
    )

    class CancelWhenRunning:
        def assert_healthy(self, _session):
            if pids_path.exists():
                raise RenderCancelled

    with pytest.raises(RenderCancelled):
        _invoke(_settings(render_timeout_secs=30), tmp_path, CancelWhenRunning())

    _assert_tree_gone(_tree_pids(pids_path))


def test_worker_shutdown_hook_terms_kills_and_reaps_registered_render_tree(monkeypatch, tmp_path):
    from instadescribe_worker.executor import WorkerShutdownRequested, current_child
    from instadescribe_worker.main import _handle_sigterm

    script = tmp_path / "blocking-render.py"
    pids_path = tmp_path / "renderer-pids.json"
    script.write_text(_BLOCKING_TREE, encoding="utf-8")
    monkeypatch.setattr(
        "instadescribe_worker.render._render_child_command",
        lambda _request: [sys.executable, str(script), str(pids_path)],
    )

    class Healthy:
        def assert_healthy(self, _session):
            return None

    result: list[BaseException | None] = [None]

    def run_render() -> None:
        try:
            _invoke(_settings(render_timeout_secs=30), tmp_path, Healthy())
        except BaseException as exc:
            result[0] = exc

    runner = threading.Thread(target=run_render)
    runner.start()
    pids = _tree_pids(pids_path)
    assert current_child() is not None and current_child().pid == pids["child"]

    # The handler only flips the lock-free latch. The renderer's ordinary
    # poll/finally path must still terminate and reap its full process tree.
    _handle_sigterm(None, None)
    runner.join(timeout=10)

    assert not runner.is_alive()
    assert isinstance(result[0], WorkerShutdownRequested)
    assert current_child() is None
    _assert_tree_gone(pids)


def test_shutdown_between_popen_and_registration_is_sticky_and_reaps_tree(monkeypatch, tmp_path):
    """A signal in the Popen→registry seam sees no child, but its sticky
    latch makes the just-spawned renderer abort immediately after registration.

    The registry is deliberately lock-free, so the simulated signal path can
    execute synchronously inside registration without lock re-entry/deadlock.
    """

    from instadescribe_worker import executor
    from instadescribe_worker import render as render_module

    script = tmp_path / "blocking-render.py"
    pids_path = tmp_path / "renderer-pids.json"
    script.write_text(_BLOCKING_TREE, encoding="utf-8")
    monkeypatch.setattr(
        render_module,
        "_render_child_command",
        lambda _request: [sys.executable, str(script), str(pids_path)],
    )
    real_register = executor.register_current_child
    captured_pids: dict[str, int] = {}

    def signal_before_register(child) -> None:
        captured_pids.update(_tree_pids(pids_path))
        assert executor.current_child() is None
        executor.request_shutdown()
        real_register(child)

    monkeypatch.setattr(render_module, "register_current_child", signal_before_register)

    class Healthy:
        def assert_healthy(self, _session):
            return None

    started = time.monotonic()
    with pytest.raises(executor.WorkerShutdownRequested):
        _invoke(_settings(render_timeout_secs=30), tmp_path, Healthy())

    assert time.monotonic() - started < 10
    assert executor.current_child() is None
    _assert_tree_gone(captured_pids)
