"""Typed worker configuration — independent of the API's settings module.

The worker never imports FastAPI startup or API token/CORS settings. Missing
or invalid required configuration fails fast WITHOUT printing secret values
(`hide_input_in_errors` strips inputs from validation errors, and the
entrypoint additionally reduces any validation failure to a category event).
"""

from functools import lru_cache
from typing import ClassVar

from instadescribe_contracts.environment import bridged_environment
from instadescribe_contracts.provider import (
    OPENAI_BETA_MAX_PROVIDER_CALLS,
    OPENAI_G12_MAX_DURATION_SECS,
    OPENAI_MAX_CALL_ATTEMPTS_PER_CHUNK,
    OPENAI_STANDARD_CHUNK_COVERAGE_SECS,
    PROVIDER_ALLOWLIST,
    PROVIDER_MAX_ATTEMPTS,
    ProviderName,
)
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", validate_default=True, hide_input_in_errors=True
    )

    def __init__(self, **values: object) -> None:
        # Canonical settings are read by Pydantic while the old namespace is
        # exposed only for the duration of construction. Conflicts fail before
        # any secret-adjacent value can reach validation output or logs.
        names = (
            field.validation_alias
            for field in type(self).model_fields.values()
            if isinstance(field.validation_alias, str)
            and field.validation_alias.startswith("INSTADESCRIBE_")
        )
        with bridged_environment(canonical_names=names):
            super().__init__(**values)

    database_url: str = Field(validation_alias="DATABASE_URL", min_length=1)
    # Deployment-level provider selection. The queue carries no provider
    # input; jobs are stamped by the API and must exactly match this worker.
    provider: ProviderName = Field(default="fake", validation_alias="INSTADESCRIBE_PROVIDER")
    provider_allowlist: ClassVar[tuple[ProviderName, ...]] = PROVIDER_ALLOWLIST
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    aws_region: str = Field(default="eu-west-2", validation_alias="AWS_DEFAULT_REGION")
    s3_endpoint_internal: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_S3_ENDPOINT_INTERNAL"
    )
    sqs_endpoint_internal: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_SQS_ENDPOINT_INTERNAL"
    )
    # Defaults preserve the deployed v0.1 physical resources; a new beta
    # stack injects its explicit InstaDescribe bucket and queue values.
    media_bucket: str = Field(
        default="instascribe-media", min_length=1, validation_alias="INSTADESCRIBE_MEDIA_BUCKET"
    )
    work_queue_name: str = Field(
        default="instascribe-work",
        min_length=1,
        max_length=80,
        validation_alias="INSTADESCRIBE_WORK_QUEUE",
    )
    work_queue_url: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_WORK_QUEUE_URL"
    )

    worker_id: str = Field(
        default="worker-local",
        min_length=1,
        max_length=120,
        validation_alias="INSTADESCRIBE_WORKER_ID",
    )
    long_poll_secs: int = Field(
        default=10, ge=0, le=20, validation_alias="INSTADESCRIBE_LONG_POLL_SECS"
    )
    subprocess_timeout_secs: int = Field(
        default=1500, ge=30, le=10800, validation_alias="INSTADESCRIBE_SUBPROCESS_TIMEOUT_SECS"
    )
    grace_secs: int = Field(default=10, ge=1, le=60, validation_alias="INSTADESCRIBE_GRACE_SECS")
    max_duration_secs: int = Field(
        default=300, ge=1, le=3600, validation_alias="INSTADESCRIBE_MAX_DURATION_SECS"
    )
    deployment_tier: str = Field(
        default="portfolio", validation_alias="INSTADESCRIBE_DEPLOYMENT_TIER"
    )
    max_attempts: int = Field(default=3, ge=1, le=3, validation_alias="INSTADESCRIBE_MAX_ATTEMPTS")
    # Bounded G12 cost controls. They are passed to the subprocess only in
    # OpenAI mode; fake children retain the pipeline's historical defaults.
    max_provider_calls: int = Field(
        default=6,
        ge=1,
        le=OPENAI_BETA_MAX_PROVIDER_CALLS,
        validation_alias="INSTADESCRIBE_MAX_PROVIDER_CALLS",
    )
    max_provider_output_tokens: int = Field(
        default=8000,
        ge=1,
        le=8000,
        validation_alias="INSTADESCRIBE_MAX_PROVIDER_OUTPUT_TOKENS",
    )
    workspace_root: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_WORKSPACE_ROOT"
    )
    retry_visibility_delay_secs: int = Field(
        default=30, ge=0, le=900, validation_alias="INSTADESCRIBE_RETRY_VISIBILITY_DELAY_SECS"
    )
    # v0.2 crash recovery.  The database lease is the ownership authority;
    # SQS visibility is renewed to the same-or-longer horizon while work is
    # live.  A heartbeat runs often enough that one missed interval cannot
    # silently consume the whole lease.
    lease_duration_secs: int = Field(
        default=300,
        ge=30,
        le=1800,
        validation_alias="INSTADESCRIBE_LEASE_DURATION_SECS",
    )
    heartbeat_interval_secs: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias="INSTADESCRIBE_HEARTBEAT_INTERVAL_SECS",
    )
    heartbeat_visibility_timeout_secs: int = Field(
        default=300,
        ge=30,
        le=43200,
        validation_alias="INSTADESCRIBE_HEARTBEAT_VISIBILITY_TIMEOUT_SECS",
    )
    # Render work is database-polled and independently fenced from analysis.
    # A dedicated heartbeat renews the lease during blocking TTS/ffmpeg work;
    # progress and upload boundaries remain additional ownership checks.
    render_lease_duration_secs: int = Field(
        default=1800,
        ge=60,
        le=3600,
        validation_alias="INSTADESCRIBE_RENDER_LEASE_DURATION_SECS",
    )
    render_heartbeat_interval_secs: int = Field(
        default=15,
        ge=1,
        le=300,
        validation_alias="INSTADESCRIBE_RENDER_HEARTBEAT_INTERVAL_SECS",
    )
    # The render child is allowed to outlive an individual database lease
    # because that lease is renewed, but never this wall-clock deadline. This
    # caps both compute spend and the number of renewals if a media tool hangs.
    render_timeout_secs: int = Field(
        default=7200,
        ge=300,
        le=10800,
        validation_alias="INSTADESCRIBE_RENDER_TIMEOUT_SECS",
    )
    # One-line TTS preview work is independently fenced and intentionally has
    # a much shorter recovery horizon than a five-format media render.
    preview_lease_duration_secs: int = Field(
        default=180,
        ge=30,
        le=600,
        validation_alias="INSTADESCRIBE_PREVIEW_LEASE_DURATION_SECS",
    )
    # Where the immutable pipeline source lives (copied per attempt).
    pipeline_source: str = Field(
        default="/app/modular_pipeline", validation_alias="INSTADESCRIBE_PIPELINE_SOURCE"
    )
    # REQUIRED provenance binding (G5.1 B4): 'dev' locally, the immutable
    # image/commit revision later. A claimed job whose persisted revision
    # differs fails deterministically as pipeline_revision_mismatch — it is
    # never silently processed under false provenance. Not a secret; not
    # client-controlled (the API stamps it server-side at creation).
    # Same semantics as the API/DB contract (G8 B2): trimmed, non-empty,
    # at most 120 characters — the sa.String(120) column bound.
    pipeline_revision: str = Field(validation_alias="INSTADESCRIBE_PIPELINE_REVISION")

    @field_validator("pipeline_revision")
    @classmethod
    def _revision_trimmed_and_bounded(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 120:
            raise ValueError("pipeline revision must be 1-120 characters after trimming")
        return v

    @field_validator("work_queue_url")
    @classmethod
    def _queue_url_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v.startswith(("http://", "https://")) or len(v) > 512:
            raise ValueError("work queue URL must be a bounded http(s) URL")
        return v

    @field_validator("deployment_tier")
    @classmethod
    def _deployment_tier(cls, value: str) -> str:
        if value not in {"portfolio", "beta"}:
            raise ValueError("deployment tier must be portfolio or beta")
        return value

    @model_validator(mode="after")
    def _g12_provider_requirements(self) -> "WorkerSettings":
        if self.provider == "openai":
            key = self.openai_api_key.get_secret_value() if self.openai_api_key else ""
            if not key or key != key.strip():
                # The validation body is never logged; main emits a category
                # and count only. Do not include key material in this message.
                raise ValueError("OpenAI worker credential is missing or invalid")
            if (
                self.deployment_tier != "beta"
                and self.max_duration_secs > OPENAI_G12_MAX_DURATION_SECS
            ):
                raise ValueError("OpenAI G12 duration limit must not exceed 120 seconds")
            required_calls = (
                (self.max_duration_secs + OPENAI_STANDARD_CHUNK_COVERAGE_SECS - 1)
                // OPENAI_STANDARD_CHUNK_COVERAGE_SECS
            ) * OPENAI_MAX_CALL_ATTEMPTS_PER_CHUNK
            if self.max_provider_calls < required_calls:
                raise ValueError("OpenAI provider call budget is below the duration bound")
            if (
                self.deployment_tier == "beta"
                and self.subprocess_timeout_secs < self.max_duration_secs * 2
            ):
                raise ValueError("beta OpenAI subprocess timeout is below the duration bound")
        if self.deployment_tier != "beta" and self.subprocess_timeout_secs > 1700:
            raise ValueError("portfolio subprocess timeout must not exceed 1700 seconds")
        if self.max_attempts != PROVIDER_MAX_ATTEMPTS[self.provider]:
            raise ValueError("configured attempt bound does not match provider policy")
        if self.heartbeat_interval_secs * 3 > self.lease_duration_secs:
            raise ValueError("heartbeat interval must leave at least one missed-beat margin")
        if self.heartbeat_visibility_timeout_secs != self.lease_duration_secs:
            raise ValueError("queue visibility heartbeat must equal the database lease")
        if self.render_heartbeat_interval_secs * 3 > self.render_lease_duration_secs:
            raise ValueError("render heartbeat must leave at least one missed-beat margin")
        if self.grace_secs >= self.render_timeout_secs:
            raise ValueError("render termination grace must be below the render deadline")
        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()


def reset_worker_settings() -> None:
    get_worker_settings.cache_clear()
