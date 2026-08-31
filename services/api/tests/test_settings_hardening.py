"""Part A3: settings default-validation, numeric bounds, control characters."""

import pytest
from app.core.config import Settings, get_settings
from app.schemas.jobs import CreateJobRequest, UploadSettingsIn
from instadescribe_contracts.environment import (
    LegacyEnvironmentConflictError,
    LegacyEnvironmentWarning,
)
from pydantic import ValidationError


def test_invalid_configured_default_fails_even_when_request_omits_the_field(monkeypatch):
    # Shrink the server allowlists so the schema defaults become invalid; a
    # request that omits settings entirely must now fail (validate_default).
    settings = get_settings()
    monkeypatch.setattr(settings, "model_allowlist", ("some-other-model",))
    with pytest.raises(ValidationError):
        UploadSettingsIn()  # default model now violates the configured allowlist


def test_settings_numeric_bounds_are_enforced(monkeypatch):
    monkeypatch.setenv("INSTADESCRIBE_PRESIGN_EXPIRY_SECS", "5")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("INSTADESCRIBE_PRESIGN_EXPIRY_SECS", "900")
    monkeypatch.setenv("INSTADESCRIBE_MAX_UPLOAD_BYTES", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_api_provider_policy_and_g12_duration_are_exact(monkeypatch):
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "unsupported")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "openai")
    monkeypatch.setenv("INSTADESCRIBE_MAX_DURATION_SECS", "121")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "1")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("INSTADESCRIBE_MAX_DURATION_SECS", "120")
    assert Settings().provider == "openai"
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "3")
    with pytest.raises(ValidationError):
        Settings()

    # Local inference is a workflow-specific investigation worker identity.
    # Allowing it as the API-wide AD provider would create jobs on a queue no
    # compatible worker consumes.
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "local")
    with pytest.raises(ValidationError):
        Settings()


def test_beta_allows_the_published_sixty_minute_limit(monkeypatch):
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "openai")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("INSTADESCRIBE_MAX_DURATION_SECS", "3600")
    monkeypatch.setenv("INSTADESCRIBE_MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024))
    monkeypatch.setenv("INSTADESCRIBE_DEPLOYMENT_TIER", "beta")
    settings = Settings()
    assert settings.max_duration_secs == 3600
    assert settings.max_upload_bytes == 1024 * 1024 * 1024


def test_unknown_deployment_tier_fails_closed(monkeypatch):
    monkeypatch.setenv("INSTADESCRIBE_DEPLOYMENT_TIER", "production-ish")
    with pytest.raises(ValidationError):
        Settings()


def test_workflow_queue_configuration_is_distinct_and_bounded(monkeypatch):
    settings = Settings()
    assert settings.work_queue_name == "instascribe-work"
    assert settings.investigation_queue_name == "instadescribe-investigation"

    monkeypatch.setenv("INSTADESCRIBE_INVESTIGATION_QUEUE", "instascribe-work")
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.setenv("INSTADESCRIBE_INVESTIGATION_QUEUE", "instadescribe-investigation")
    shared = "http://127.0.0.1:4566/000000000000/shared"
    monkeypatch.setenv("INSTADESCRIBE_WORK_QUEUE_URL", shared)
    monkeypatch.setenv("INSTADESCRIBE_INVESTIGATION_QUEUE_URL", shared)
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.delenv("INSTADESCRIBE_WORK_QUEUE_URL")
    monkeypatch.setenv(
        "INSTADESCRIBE_INVESTIGATION_QUEUE_URL",
        "http://127.0.0.1:4566/000000000000/instascribe-work",
    )
    with pytest.raises(ValidationError):
        Settings()

    monkeypatch.setenv("INSTADESCRIBE_INVESTIGATION_QUEUE_URL", "ftp://invalid")
    with pytest.raises(ValidationError):
        Settings()


def test_sqs_publishers_use_workflow_specific_queue_urls(monkeypatch):
    import uuid
    from datetime import UTC, datetime

    from app.services import sqs as sqs_service
    from instadescribe_contracts.queue import QueueMessage

    sent: list[dict[str, str]] = []

    class Client:
        def send_message(self, **kwargs):
            sent.append(kwargs)

    message = QueueMessage(
        schema_version=1,
        message_id=uuid.uuid4(),
        task_type="ANALYZE",
        job_id=uuid.UUID("00000000-0000-4000-8000-000000000101"),
        requested_at=datetime.now(UTC),
    )
    monkeypatch.setattr(sqs_service, "_sqs_client", lambda: Client())
    monkeypatch.setattr(sqs_service, "work_queue_url", lambda: "audio-queue")
    monkeypatch.setattr(sqs_service, "investigation_queue_url", lambda: "investigation-queue")

    sqs_service.send_task_message(message)
    sqs_service.send_investigation_task_message(message)

    assert [entry["QueueUrl"] for entry in sent] == ["audio-queue", "investigation-queue"]
    assert sent[0]["MessageBody"] == sent[1]["MessageBody"] == message.to_body()


def test_pipeline_revision_trim_and_bound(monkeypatch):
    monkeypatch.setenv("INSTADESCRIBE_PIPELINE_REVISION", "   ")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("INSTADESCRIBE_PIPELINE_REVISION", "x" * 121)
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("INSTADESCRIBE_PIPELINE_REVISION", "  rev-1  ")
    assert Settings().pipeline_revision == "rev-1"


def test_integration_api_key_pepper_is_never_accepted_when_weak(monkeypatch):
    monkeypatch.setenv("INSTADESCRIBE_API_KEY_PEPPER", "too-short")
    with pytest.raises(ValidationError):
        Settings()


def test_api_settings_accept_legacy_namespace_but_reject_conflicts(monkeypatch):
    monkeypatch.delenv("INSTADESCRIBE_ENABLE_DOCS", raising=False)
    monkeypatch.setenv("INSTASCRIBE_ENABLE_DOCS", "0")
    with pytest.warns(LegacyEnvironmentWarning):
        assert Settings().enable_docs is False

    monkeypatch.setenv("INSTADESCRIBE_ENABLE_DOCS", "1")
    with pytest.raises(LegacyEnvironmentConflictError) as caught:
        Settings()
    assert "INSTADESCRIBE_ENABLE_DOCS" in str(caught.value)
    assert "INSTASCRIBE_ENABLE_DOCS" in str(caught.value)


def _payload(**overrides):
    base = {
        "name": "ok",
        "durationSecs": 10.0,
        "fileName": "a.mp4",
        "contentType": "video/mp4",
        "fileSizeBytes": 100,
    }
    base.update(overrides)
    return base


def test_nul_and_control_characters_are_validation_errors_not_500s():
    with pytest.raises(ValidationError):
        CreateJobRequest.model_validate(_payload(name="bad\x00name"))
    with pytest.raises(ValidationError):
        CreateJobRequest.model_validate(_payload(name="bad\x1bname"))
    with pytest.raises(ValidationError):
        CreateJobRequest.model_validate(_payload(settings={"customPrompt": "bad\x00prompt"}))
    # Prompts may keep normal whitespace controls.
    req = CreateJobRequest.model_validate(_payload(settings={"customPrompt": "line1\nline2\t."}))
    assert "\n" in req.settings.custom_prompt
