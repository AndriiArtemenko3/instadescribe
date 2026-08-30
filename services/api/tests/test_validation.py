"""Every validation bound and spend allowlist; filename sanitization;
normalized worker settings; forbidden client-supplied provenance."""

import pytest
from app.main import app
from app.schemas.jobs import CreateJobRequest
from fastapi.testclient import TestClient
from pydantic import ValidationError

AUTH = {"X-Portfolio-Token": "test-token"}

client = TestClient(app)


def valid_payload(**overrides) -> dict:
    payload = {
        "name": "My clip",
        "durationSecs": 120.0,
        "fileName": "clip.mp4",
        "contentType": "video/mp4",
        "fileSizeBytes": 10_000_000,
        "settings": {
            "model": "gpt-4.1",
            "frameQuality": "low",
            "fps": 1.0,
            "chunkSizeSecs": 60,
            "audioExtraction": True,
            "customPrompt": "",
            "language": None,
            "detailLevel": 3,
            "presetStyle": "documentary",
        },
    }
    payload.update(overrides)
    return payload


def post(payload: dict):
    return client.post("/api/v1/jobs", json=payload, headers=AUTH)


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": "   "},
        {"name": "x" * 201},
        {"durationSecs": 0},
        {"durationSecs": 301},
        {"fileName": "../../etc/passwd.mp4"},
        {"fileName": "a/b.mp4"},
        {"fileName": "clip\x00.mp4"},
        {"fileName": "clip.exe"},
        {"fileName": "clip"},
        {"contentType": "video/x-msvideo"},
        {"contentType": "application/octet-stream"},
        {"fileSizeBytes": 0},
        {"fileSizeBytes": 250 * 1024 * 1024 + 1},
        {"provider": "openai"},
        {"pipelineRevision": "main"},
    ],
)
def test_top_level_bounds_and_forbidden_fields(mutation):
    assert post(valid_payload(**mutation)).status_code == 422


@pytest.mark.parametrize(
    "mutation",
    [
        {"model": "gpt-5.4"},
        {"fps": 8},
        {"fps": 2.0},
        {"frameQuality": "high"},
        {"chunkSizeSecs": 30},
        {"chunkSizeSecs": 240},
        {"detailLevel": 0},
        {"detailLevel": 6},
        {"presetStyle": "anime"},
        {"language": "definitely-not-a-tag"},
        {"customPrompt": "x" * 2001},
        {"provider": "openai"},
        {"pipelineRevision": "abc123"},
    ],
)
def test_settings_allowlists_and_forbidden_fields(mutation):
    payload = valid_payload()
    payload["settings"] = {**payload["settings"], **mutation}
    assert post(payload).status_code == 422


def test_filename_is_sanitized_to_a_safe_basename():
    req = CreateJobRequest.model_validate(valid_payload(fileName="weird nâme!.MP4"))
    assert req.file_name == "weird_n_me.mp4"


@pytest.mark.parametrize(
    "mutation",
    [
        {"fileName": "clip.webm", "contentType": "video/mp4"},
        {"fileName": "clip.mp4", "contentType": "video/webm"},
        {"fileName": "clip.mov", "contentType": "video/mp4"},
        {"fileName": "clip.mp4", "contentType": "video/quicktime"},
        {"fileName": "clip.webm", "contentType": "video/quicktime"},
    ],
)
def test_extension_and_content_type_must_pair_exactly(mutation):
    """G5.1 C3: each value being individually allowlisted is NOT enough —
    the extension<->MIME pair is the contract."""
    assert post(valid_payload(**mutation)).status_code == 422


@pytest.mark.parametrize(
    "pair",
    [
        {"fileName": "clip.mp4", "contentType": "video/mp4"},
        {"fileName": "clip.mov", "contentType": "video/quicktime"},
        {"fileName": "clip.webm", "contentType": "video/webm"},
    ],
)
def test_consistent_extension_content_type_pairs_validate(pair):
    CreateJobRequest.model_validate(valid_payload(**pair))


def test_normalized_worker_settings_match_the_pipeline_contract():
    req = CreateJobRequest.model_validate(valid_payload())
    assert req.to_worker_settings() == {
        "model": "gpt-4.1",
        "frame_quality": "low",
        "fps": 1.0,
        "chunk_size": 60,
        "audio_extraction": True,
        "custom_prompt": "",
        "language": None,
        "detail_level": 3,
        "preset_style": "documentary",
        "project_name": "My clip",
        "duration_secs": 120.0,
    }


def test_defaults_are_safe_when_settings_omitted():
    payload = valid_payload()
    del payload["settings"]
    req = CreateJobRequest.model_validate(payload)
    assert req.settings.model == "gpt-4.1"
    assert req.settings.fps == 1.0
    assert req.settings.frame_quality == "low"


def test_eight_fps_is_rejected_for_cloud_v01():
    with pytest.raises(ValidationError):
        CreateJobRequest.model_validate(valid_payload(settings={"model": "gpt-4.1", "fps": 8.0}))
