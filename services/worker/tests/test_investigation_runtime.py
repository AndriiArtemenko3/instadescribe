from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from instadescribe_investigation_core import (
    BeliefConfig,
    CandidatePrior,
    FrameDescriptor,
    FrameRejectionReason,
    InvestigationKind,
    InvestigationStatus,
    LocalRunExpectation,
    ModelProvenance,
    SourceRecord,
    local_run_result_from_primitive,
    local_run_result_to_primitive,
    to_primitive,
)
from instadescribe_worker import investigation_runtime as runtime_module
from instadescribe_worker.config import WorkerSettings
from instadescribe_worker.executor import reset_shutdown_state
from instadescribe_worker.failures import FailureCode, JobFailure
from instadescribe_worker.investigation_executor import (
    _child_environment,
    _import_roots,
    execute_local_investigation,
    host_inference_lease,
)
from instadescribe_worker.investigation_runtime import (
    ExtractedFrame,
    InvestigationRuntimeSettings,
    _evidence_from_observations,
    _frame_embedding_provider,
    _keyframe_evidence,
    _model_digest_from_tags,
    _Observation,
    _request_manifest_digest,
    _request_manifest_entry,
    _runtime_version_from_payload,
    extract_ranked_frames,
    fixture_candidates,
    fixture_model_provenance,
    run_local_observation,
)


def _local_fixture_settings(monkeypatch) -> WorkerSettings:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/x")
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "local")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("INSTADESCRIBE_INVESTIGATION_RUNTIME", "fixture")
    monkeypatch.setenv("INSTADESCRIBE_TEST_FIXTURE_RUNTIME", "true")
    return WorkerSettings()


def _source(source_id: str, content_sha256: str) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        content_sha256=content_sha256,
        collected_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
        license_basis="licensed:CC BY 4.0",
        publisher="Fixture Publisher",
        source_url="https://publisher.example.test/fixture",
        published_at=datetime(2026, 8, 30, 10, tzinfo=UTC),
        consent_basis=None,
        redistribution_policy="metadata_only",
        retention_policy="retentionDays=14;purgeAfter=2026-09-13T12:00:00Z",
    )


def test_fixture_local_observation_is_deterministic_and_offline(monkeypatch, tmp_path):
    settings = _local_fixture_settings(monkeypatch)

    def refuse_network(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(
        "instadescribe_worker.investigation_runtime.urllib.request.build_opener",
        refuse_network,
    )
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"rights-cleared-test-video")
    source_sha256 = hashlib.sha256(media.read_bytes()).hexdigest()
    source = _source("source-1", source_sha256)

    first = run_local_observation(
        media,
        tmp_path,
        source=source,
        duration_seconds=30,
        settings=settings,
        investigation_id="investigation-1",
        trace_id="trace-1",
        kind=InvestigationKind.GEOLOCATE_PROVENANCE,
    )
    second = run_local_observation(
        media,
        tmp_path,
        source=source,
        duration_seconds=30,
        settings=settings,
        investigation_id="investigation-1",
        trace_id="trace-1",
        kind=InvestigationKind.GEOLOCATE_PROVENANCE,
    )

    assert first.investigation.status is InvestigationStatus.NEEDS_REVIEW
    assert first.source.content_sha256 == source_sha256
    assert first.belief == second.belief
    assert first.evidence == second.evidence
    assert first.investigation.model_provenance[0].runtime == "in-process-test-seam"
    keyframes = [item for item in first.evidence if item.attributes.get("role") == "keyframe"]
    assert [item.frame_time_ms for item in keyframes] == [4_000, 12_500]
    assert len({item.correlation_group for item in first.evidence}) == 4
    assert not first.belief.abstained


def test_fixture_abstention_is_deterministic_and_has_no_supported_hypothesis(monkeypatch, tmp_path):
    monkeypatch.setenv("INSTADESCRIBE_TEST_FIXTURE_SCENARIO", "abstention")
    settings = _local_fixture_settings(monkeypatch)
    media = tmp_path / "insufficient-evidence.mp4"
    media.write_bytes(b"rights-cleared-insufficient-evidence-video")
    source_sha256 = hashlib.sha256(media.read_bytes()).hexdigest()
    source = _source("source-abstention", source_sha256)

    result = run_local_observation(
        media,
        tmp_path,
        source=source,
        duration_seconds=30,
        settings=settings,
        investigation_id="investigation-abstention",
        trace_id="trace-abstention",
        kind=InvestigationKind.GEOLOCATE_PROVENANCE,
    )

    assert result.belief.abstained is True
    assert result.investigation.final_hypothesis_id is None
    assert result.investigation.confidence is None
    assert "insufficientIndependentEvidence" in result.belief.abstention_reasons
    assert [item.attributes.get("role") for item in result.evidence] == [
        "keyframe",
        "keyframe",
    ]


def test_fixture_runtime_crosses_strict_isolated_json_boundary(monkeypatch, tmp_path):
    settings = _local_fixture_settings(monkeypatch)
    workspace = tmp_path / "job"
    workspace.mkdir()
    media = workspace / "source.mp4"
    media.write_bytes(b"rights-cleared-isolated-test-video")
    source_sha256 = hashlib.sha256(media.read_bytes()).hexdigest()
    source = _source("source-isolated", source_sha256)
    investigation_id = "11111111-1111-4111-8111-111111111111"
    trace_id = "22222222-2222-4222-8222-222222222222"
    ticks: list[bool] = []
    reset_shutdown_state()

    result = execute_local_investigation(
        settings,
        media_path=media,
        workspace=workspace,
        source=source,
        duration_seconds=30,
        investigation_id=investigation_id,
        trace_id=trace_id,
        kind=InvestigationKind.GEOLOCATE_PROVENANCE,
        on_tick=lambda: ticks.append(True),
    )

    assert result.investigation.status is InvestigationStatus.NEEDS_REVIEW
    assert result.source.content_sha256 == source_sha256
    assert result.source == source
    assert result.investigation.investigation_id == investigation_id
    assert result.investigation.trace_id == trace_id
    assert len(result.trace) >= 1
    assert ticks
    assert (workspace / "investigation-request.json").stat().st_mode & 0o777 == 0o600
    assert (workspace / "investigation-result.json").stat().st_mode & 0o777 == 0o600
    request = json.loads((workspace / "investigation-request.json").read_text())
    assert request["runtime"]["investigation_semantic_keyframes_enabled"] is False
    assert request["runtime"]["investigation_semantic_novelty_weight"] == 0.3
    assert request["runtime"]["investigation_semantic_similarity_threshold"] is None
    assert request["runtime"]["investigation_frame_embedding_model_path"] is None

    expected = LocalRunExpectation(
        source=source,
        investigation_id=investigation_id,
        trace_id=trace_id,
        candidates=fixture_candidates("supportive"),
        model_provenance=(fixture_model_provenance("supportive"),),
        belief_config=BeliefConfig(),
    )
    tampered = local_run_result_to_primitive(result)
    tampered["source"]["retention_policy"] = "keepForever"
    with pytest.raises(ValueError, match="parent expectation"):
        local_run_result_from_primitive(tampered, expected=expected)


def test_live_ollama_execution_fails_closed_before_child_launch(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/x")
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "local")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "3")
    settings = WorkerSettings()
    workspace = tmp_path / "job"
    workspace.mkdir()
    media = workspace / "source.mp4"
    media.write_bytes(b"rights-cleared-live-handshake-gate")
    source_sha256 = hashlib.sha256(media.read_bytes()).hexdigest()

    with pytest.raises(JobFailure, match="proposal handshake") as error:
        execute_local_investigation(
            settings,
            media_path=media,
            workspace=workspace,
            source=_source("source-live", source_sha256),
            duration_seconds=30,
            investigation_id="33333333-3333-4333-8333-333333333333",
            trace_id="44444444-4444-4444-8444-444444444444",
            kind=InvestigationKind.GEOLOCATE_PROVENANCE,
            on_tick=lambda: None,
        )

    assert error.value.code.value == "invalid_settings"
    assert not (workspace / "investigation-request.json").exists()


def test_investigation_child_environment_uses_private_workspace_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/private/host-home")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    workspace = tmp_path / "job"
    workspace.mkdir()

    environment = _child_environment(workspace)

    assert set(environment) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "LANG",
        "PYTHONUNBUFFERED",
    }
    assert environment["HOME"] == str(workspace / ".home")
    assert environment["HOME"] != "/private/host-home"
    assert (workspace / ".home").stat().st_mode & 0o777 == 0o700
    assert environment["TMPDIR"] == str(workspace / ".tmp")
    assert environment["XDG_CACHE_HOME"] == str(workspace / ".cache")
    assert "AWS_SECRET_ACCESS_KEY" not in environment


def test_investigation_import_roots_resolve_source_checkout():
    worker_root, core_root, contracts_root = _import_roots()

    assert (worker_root / "instadescribe_worker").is_dir()
    assert (core_root / "instadescribe_investigation_core").is_dir()
    assert (contracts_root / "instadescribe_contracts").is_dir()


def test_investigation_import_roots_resolve_flattened_production_layout(tmp_path):
    app_root = tmp_path / "app"
    worker_package = app_root / "instadescribe_worker"
    worker_package.mkdir(parents=True)
    (app_root / "instadescribe_investigation_core").mkdir()
    (app_root / "instadescribe_contracts").mkdir()

    roots = _import_roots(worker_package / "investigation_executor.py")

    assert roots == (app_root, app_root, app_root)


def test_investigation_import_roots_reject_incomplete_runtime_layout(tmp_path):
    worker_package = tmp_path / "app" / "instadescribe_worker"
    worker_package.mkdir(parents=True)

    with pytest.raises(JobFailure, match="runtime packages"):
        _import_roots(worker_package / "investigation_executor.py")


def test_host_inference_lease_serializes_workers(monkeypatch, tmp_path):
    settings = _local_fixture_settings(monkeypatch).model_copy(
        update={"workspace_root": str(tmp_path), "subprocess_timeout_secs": 1}
    )
    entered = threading.Event()
    release = threading.Event()
    holder_errors: list[BaseException] = []

    def hold_lease() -> None:
        try:
            with host_inference_lease(settings, on_tick=lambda: None):
                entered.set()
                release.wait(timeout=5)
        except BaseException as error:  # pragma: no cover - reported below
            holder_errors.append(error)

    holder = threading.Thread(target=hold_lease)
    holder.start()
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(JobFailure, match="host lease"):
            with host_inference_lease(settings, on_tick=lambda: None):
                raise AssertionError("second worker acquired the host lease")
    finally:
        release.set()
        holder.join(timeout=2)
    assert not holder.is_alive()
    assert holder_errors == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("investigation_ollama_url", "https://example.test:11434"),
        ("investigation_max_keyframes", 25),
        ("investigation_batch_size", 9),
    ],
)
def test_isolated_runtime_settings_reject_tampering(field, value):
    payload = {
        "investigation_runtime": "ollama",
        "investigation_test_fixture_enabled": False,
        "investigation_test_fixture_scenario": "supportive",
        "investigation_model": "qwen3.5:4b",
        "investigation_ollama_url": "http://127.0.0.1:11434",
        "investigation_timeout_secs": 180,
        "investigation_max_keyframes": 16,
        "investigation_batch_size": 4,
        "investigation_image_long_edge": 1024,
    }
    payload[field] = value

    with pytest.raises(ValueError):
        InvestigationRuntimeSettings.model_validate(payload)


def test_ollama_model_provenance_uses_the_installed_artifact_digest():
    digest = "a" * 64
    payload = {
        "models": [
            {
                "name": "qwen3.5:4b",
                "model": "qwen3.5:4b",
                "digest": f"sha256:{digest}",
                "untrustedExtra": "ignored by the provenance projection",
            }
        ]
    }

    assert _model_digest_from_tags(payload, "qwen3.5:4b") == digest
    with pytest.raises(ValueError, match="not installed"):
        _model_digest_from_tags(payload, "qwen3-vl:8b")
    payload["models"][0]["digest"] = "mutable-tag-name"
    with pytest.raises(ValueError, match="invalid"):
        _model_digest_from_tags(payload, "qwen3.5:4b")


def test_ollama_runtime_and_batch_request_manifest_are_exact(monkeypatch, tmp_path):
    settings = _local_fixture_settings(monkeypatch)
    descriptors = tuple(
        FrameDescriptor(
            frame_id=f"frame-{index}",
            artifact_id=f"artifact-{index}",
            source_content_sha256="f" * 64,
            content_sha256=character * 64,
            shot_index=index,
            time_ms=index * 1_000,
            size_bytes=100,
            width=640,
            height=360,
        )
        for index, character in enumerate(("a", "b"), start=1)
    )
    frames = tuple(
        ExtractedFrame(descriptor=descriptor, path=tmp_path / f"{descriptor.frame_id}.jpg")
        for descriptor in descriptors
    )
    entry = _request_manifest_entry(
        settings,
        frames,
        prompt="Exact bounded prompt",
        schema={"type": "object", "additionalProperties": False},
    )

    digest = _request_manifest_digest([entry])
    assert digest == _request_manifest_digest([entry])
    assert len(digest) == 64
    assert entry["frames"] == [
        {"artifactId": "artifact-1", "sha256": "a" * 64, "timeMs": 1_000},
        {"artifactId": "artifact-2", "sha256": "b" * 64, "timeMs": 2_000},
    ]
    assert _request_manifest_digest([{**entry, "prompt": "Changed prompt"}]) != digest
    assert (
        _request_manifest_digest([{**entry, "frames": list(reversed(entry["frames"]))}]) != digest
    )
    assert _runtime_version_from_payload({"version": "0.12.3"}) == "0.12.3"
    with pytest.raises(ValueError, match="unexpected shape"):
        _runtime_version_from_payload({"version": "0.12.3", "build": "mutable"})
    with pytest.raises(ValueError, match="invalid"):
        _runtime_version_from_payload({"version": "contains spaces"})


def test_model_cannot_split_one_frame_into_independent_correlation_groups(tmp_path):
    descriptor = FrameDescriptor(
        frame_id="frame-1",
        artifact_id="artifact-1",
        source_content_sha256="f" * 64,
        content_sha256="a" * 64,
        shot_index=0,
        time_ms=1_000,
        size_bytes=100,
        width=640,
        height=360,
    )
    frame = ExtractedFrame(descriptor=descriptor, path=tmp_path / "frame.jpg")
    candidates = (CandidatePrior("candidate-1", "Candidate A", 1.0),)
    observations = [
        _Observation.model_validate(
            {
                "summary": "Sign text",
                "kind": "ocr",
                "frameIndex": 0,
                "correlationGroup": "model-says-text",
                "reliability": 0.9,
                "contributions": [{"candidateIndex": 0, "score": 0.8}],
            }
        ),
        _Observation.model_validate(
            {
                "summary": "The same sign visually",
                "kind": "visual",
                "frameIndex": 0,
                "correlationGroup": "model-says-independent",
                "reliability": 0.8,
                "contributions": [{"candidateIndex": 0, "score": 0.7}],
            }
        ),
    ]

    evidence = _evidence_from_observations(observations, (frame,), candidates, batch_index=0)

    assert {item.correlation_group for item in evidence} == {f"frame-{'a' * 32}"}
    assert {item.attributes["modelCorrelationGroup"] for item in evidence} == {
        "model-says-text",
        "model-says-independent",
    }


def test_keyframe_ffmpeg_restricts_input_protocols(monkeypatch, tmp_path):
    from instadescribe_worker.investigation_runtime import _run_ffmpeg_frame

    commands: list[list[str]] = []

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    _run_ffmpeg_frame(
        tmp_path / "source.mp4",
        tmp_path / "frame.jpg",
        seconds=1,
        long_edge=768,
    )

    command = commands[0]
    index = command.index("-protocol_whitelist")
    assert command[index + 1] == "file,pipe"
    assert index < command.index("-i")
    assert command[command.index("-f") + 1] == "mov"
    assert command[command.index("-enable_drefs") + 1] == "0"
    assert command[command.index("-use_absolute_path") + 1] == "0"
    assert command[command.index("-max_streams") + 1] == "32"
    assert command[command.index("-threads") + 1] == "1"
    assert command[command.index("-filter_threads") + 1] == "1"


# --- semantic keyframe embeddings ---------------------------------------------

# Nine dimensions: the three scene vectors live in the first four components and
# frames 3..7 map to the basis vectors e4..e8, which are orthogonal to all of them.
_EMBEDDING_A = (0.82, 0.11, 0.43, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0)
_EMBEDDING_A_VARIANT = (0.80, 0.13, 0.45, 0.07, 0.0, 0.0, 0.0, 0.0, 0.0)
_EMBEDDING_UNRELATED = (-0.10, 0.78, 0.04, 0.60, 0.0, 0.0, 0.0, 0.0, 0.0)


class RecordingEmbeddingProvider:
    """Deterministic FrameEmbeddingProvider that records every inference call."""

    def __init__(self, *, fail_on: str | None = None, dimension_drift: bool = False) -> None:
        self.calls: list[str] = []
        self._fail_on = fail_on
        self._dimension_drift = dimension_drift

    @property
    def provenance(self):
        return ModelProvenance("fake-embedding", "1", "a" * 64, "in-process-test")

    @property
    def network_access(self) -> bool:
        return False

    def embed_frame(self, frame_path):
        self.calls.append(frame_path.name)
        if frame_path.name == self._fail_on:
            raise JobFailure(FailureCode.PIPELINE_FAILED, "frame embedding inference failed")
        index = int(frame_path.stem.split("-")[-1])
        if index == 0:
            return _EMBEDDING_A
        if index == 1:
            return _EMBEDDING_A_VARIANT
        if index == 2:
            return _EMBEDDING_UNRELATED
        if self._dimension_drift:
            return (1.0, 0.0)
        basis = [0.0] * 9
        basis[index + 1] = 1.0
        return tuple(basis)


def _fake_frame_extraction(monkeypatch) -> None:
    def fake_ffmpeg(media_path, destination, *, seconds, long_edge):
        destination.write_bytes(f"{destination.name}@{seconds:.3f}".encode())

    monkeypatch.setattr(runtime_module, "_run_ffmpeg_frame", fake_ffmpeg)

    def walsh_hash(path):
        # Walsh codes: every pair of distinct 64-bit rows differs in exactly 32
        # bits, so the pHash gate never fires and the pHash-neighbour novelty
        # feature is 1.0 for every frame. Ranking then depends only on the
        # semantic term, which is what these tests isolate.
        row = int(path.stem.split("-")[-1]) + 1
        bits = 0
        for column in range(64):
            bits = (bits << 1) | ((row & column).bit_count() & 1)
        return f"{bits:016x}"

    monkeypatch.setattr(runtime_module, "perceptual_hash", walsh_hash)
    monkeypatch.setattr(runtime_module, "_image_features", lambda path: (640, 360, 0.8, 0.8, 0.0))


def _live_settings(monkeypatch, tmp_path, **environment: str) -> WorkerSettings:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/x")
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "local")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("INSTADESCRIBE_INVESTIGATION_MAX_KEYFRAMES", "4")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return WorkerSettings()


def _extract(settings, tmp_path, provider):
    tmp_path.mkdir(parents=True, exist_ok=True)
    media = tmp_path / "source.mp4"
    media.write_bytes(b"rights-cleared-embedding-fixture")
    return extract_ranked_frames(
        media,
        tmp_path,
        source_sha256=hashlib.sha256(media.read_bytes()).hexdigest(),
        duration_seconds=30,
        settings=settings,
        embedding_provider=provider,
    )


def test_semantic_keyframes_disabled_performs_no_inference_and_keeps_selection(
    monkeypatch, tmp_path
):
    _fake_frame_extraction(monkeypatch)
    settings = _live_settings(monkeypatch, tmp_path)
    provider = RecordingEmbeddingProvider()

    with_provider = _extract(settings, tmp_path / "a", provider)
    without_provider = _extract(settings, tmp_path / "b", None)

    assert provider.calls == []
    assert with_provider.candidate_count == 8
    assert all(item.descriptor.embedding is None for item in with_provider.selected)
    assert [item.descriptor.frame_id for item in with_provider.selected] == [
        item.descriptor.frame_id for item in without_provider.selected
    ]
    assert with_provider.rejected == without_provider.rejected
    assert with_provider.selector_version == "ffmpeg-uniform+heuristic-v1"
    assert "semanticKeyframes" not in with_provider.step_attributes()
    evidence = _keyframe_evidence(with_provider.selected, fixture_candidates("supportive"))
    assert set(evidence[0].attributes) == {
        "role",
        "frameSha256",
        "selector",
        "rank",
        "width",
        "height",
    }
    assert evidence[0].attributes["selector"] == "ffmpeg-uniform+heuristic-v1"


def test_semantic_keyframes_embed_each_candidate_once_and_reject_redundant_frames(
    monkeypatch, tmp_path
):
    _fake_frame_extraction(monkeypatch)
    settings = _live_settings(
        monkeypatch,
        tmp_path,
        INSTADESCRIBE_INVESTIGATION_SEMANTIC_KEYFRAMES="true",
        INSTADESCRIBE_INVESTIGATION_SEMANTIC_NOVELTY_WEIGHT="0",
        INSTADESCRIBE_INVESTIGATION_SEMANTIC_SIMILARITY_THRESHOLD="0.9",
        INSTADESCRIBE_FRAME_EMBEDDING_MODEL_PATH=str(tmp_path / "vision_model.onnx"),
    )
    provider = RecordingEmbeddingProvider()

    ranked = _extract(settings, tmp_path, provider)

    assert provider.calls == [f"frame-{index:03d}.jpg" for index in range(8)]
    assert ranked.embedding_inference_calls == 8 == ranked.candidate_count
    assert ranked.embedding_dimension == 9
    assert ranked.selector_version == "ffmpeg-uniform+heuristic-v1+semantic-v1"
    selected_ids = [item.descriptor.frame_id for item in ranked.selected]
    assert selected_ids == ["frame-000", "frame-002", "frame-003", "frame-004"]
    assert ranked.step_attributes()["rejections"] == {"semanticDuplicate": 1, "globalLimit": 3}
    redundant = next(item for item in ranked.rejected if item.frame_id == "frame-001")
    assert redundant.reason is FrameRejectionReason.SEMANTIC_DUPLICATE
    assert redundant.duplicate_of == "frame-000"
    first = ranked.selected[0].keyframe
    assert first is not None
    assert (first.embedding_similarity_max, first.semantic_novelty) == (None, 1.0)
    unrelated = next(item for item in ranked.selected if item.descriptor.frame_id == "frame-002")
    assert unrelated.keyframe.embedding_similarity_max < 0.1
    assert unrelated.keyframe.semantic_novelty > 0.9
    attributes = ranked.step_attributes()
    assert attributes["rejections"]["semanticDuplicate"] == 1
    assert attributes["semanticKeyframes"] == {
        "embeddingInferenceCalls": 8,
        "embeddingDimension": 9,
        "embeddingModel": {
            "name": "fake-embedding",
            "version": "1",
            "digest": "a" * 64,
            "runtime": "in-process-test",
        },
        "semanticDuplicatesRejected": 1,
    }
    evidence = _keyframe_evidence(
        ranked.selected,
        fixture_candidates("supportive"),
        selector_version=ranked.selector_version,
    )
    assert evidence[0].attributes["selector"] == "ffmpeg-uniform+heuristic-v1+semantic-v1"
    assert evidence[0].attributes["semanticNovelty"] == 1.0
    assert evidence[0].attributes["embeddingSimilarityMax"] is None
    unrelated_evidence = next(
        item for item in evidence if item.artifact_id == unrelated.descriptor.artifact_id
    )
    assert unrelated_evidence.attributes["embeddingSimilarityMax"] < 0.1
    assert "embedding" not in json.dumps(to_primitive(unrelated_evidence)).replace(
        "embeddingSimilarityMax", ""
    )


def test_semantic_weight_alone_penalizes_redundancy_without_rejecting(monkeypatch, tmp_path):
    _fake_frame_extraction(monkeypatch)
    settings = _live_settings(
        monkeypatch,
        tmp_path,
        INSTADESCRIBE_INVESTIGATION_SEMANTIC_KEYFRAMES="true",
        INSTADESCRIBE_INVESTIGATION_SEMANTIC_NOVELTY_WEIGHT="0.5",
        INSTADESCRIBE_FRAME_EMBEDDING_MODEL_PATH=str(tmp_path / "vision_model.onnx"),
    )

    ranked = _extract(settings, tmp_path / "semantic", RecordingEmbeddingProvider())
    baseline = _extract(
        _live_settings(
            monkeypatch, tmp_path, INSTADESCRIBE_INVESTIGATION_SEMANTIC_KEYFRAMES="false"
        ),
        tmp_path / "baseline",
        None,
    )

    # Baseline: equal scores, so the first four frames by time win.
    assert [item.descriptor.frame_id for item in baseline.selected] == [
        "frame-000",
        "frame-001",
        "frame-002",
        "frame-003",
    ]
    # Semantic weight: the A-variant (novelty ~0) and the partially similar
    # unrelated scene (novelty ~0.95) lose to fully orthogonal later frames;
    # nothing is hard-rejected as a semantic duplicate.
    assert [item.descriptor.frame_id for item in ranked.selected] == [
        "frame-000",
        "frame-003",
        "frame-004",
        "frame-005",
    ]
    assert {item.reason for item in ranked.rejected} == {FrameRejectionReason.GLOBAL_LIMIT}
    drained = [item.frame_id for item in ranked.rejected]
    assert drained.index("frame-002") < drained.index("frame-001")
    assert ranked.selected[1].keyframe.semantic_novelty == 1.0


def test_semantic_keyframes_enabled_without_a_provider_fails_closed(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="no frame embedding model path"):
        _live_settings(monkeypatch, tmp_path, INSTADESCRIBE_INVESTIGATION_SEMANTIC_KEYFRAMES="true")
    with pytest.raises(ValueError, match="neither the novelty weight nor the similarity"):
        _live_settings(
            monkeypatch,
            tmp_path,
            INSTADESCRIBE_INVESTIGATION_SEMANTIC_KEYFRAMES="true",
            INSTADESCRIBE_INVESTIGATION_SEMANTIC_NOVELTY_WEIGHT="0",
            INSTADESCRIBE_FRAME_EMBEDDING_MODEL_PATH="/models/vision_model.onnx",
        )
    with pytest.raises(ValueError, match="no frame embedding model path"):
        InvestigationRuntimeSettings.model_validate(
            {
                "investigation_runtime": "ollama",
                "investigation_model": "qwen3.5:4b",
                "investigation_ollama_url": "http://127.0.0.1:11434",
                "investigation_timeout_secs": 180,
                "investigation_max_keyframes": 16,
                "investigation_batch_size": 4,
                "investigation_image_long_edge": 1024,
                "investigation_semantic_keyframes_enabled": True,
            }
        )
    with pytest.raises(JobFailure, match="no frame embedding model path") as unset:
        _frame_embedding_provider(
            SimpleNamespace(
                investigation_semantic_keyframes_enabled=True,
                investigation_frame_embedding_model_path=None,
            )
        )
    assert unset.value.code.value == "invalid_settings"
    with pytest.raises(JobFailure, match="model file is unavailable"):
        _frame_embedding_provider(
            SimpleNamespace(
                investigation_semantic_keyframes_enabled=True,
                investigation_frame_embedding_model_path=str(tmp_path / "absent.onnx"),
            )
        )
    assert (
        _frame_embedding_provider(
            SimpleNamespace(
                investigation_semantic_keyframes_enabled=False,
                investigation_frame_embedding_model_path=None,
            )
        )
        is None
    )


def test_embedding_failures_abort_extraction_explicitly(monkeypatch, tmp_path):
    _fake_frame_extraction(monkeypatch)
    settings = _live_settings(
        monkeypatch,
        tmp_path,
        INSTADESCRIBE_INVESTIGATION_SEMANTIC_KEYFRAMES="true",
        INSTADESCRIBE_FRAME_EMBEDDING_MODEL_PATH=str(tmp_path / "vision_model.onnx"),
    )

    failing = RecordingEmbeddingProvider(fail_on="frame-002.jpg")
    with pytest.raises(JobFailure, match="inference failed"):
        _extract(settings, tmp_path / "fail", failing)
    assert failing.calls == ["frame-000.jpg", "frame-001.jpg", "frame-002.jpg"]

    drifting = RecordingEmbeddingProvider(dimension_drift=True)
    with pytest.raises(JobFailure, match="keyframe selection input is invalid") as error:
        _extract(settings, tmp_path / "drift", drifting)
    assert error.value.code.value == "pipeline_failed"
