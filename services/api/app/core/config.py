"""Typed application configuration.

`DATABASE_URL` and every secret-adjacent value are supplied by the environment
(Compose locally, the task definition + Secrets Manager in AWS). The DSN and
the portfolio-token digest must never be logged or echoed into responses —
nothing in this module or its consumers prints them. Only clearly documented
local/test placeholder material may appear in the repository.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import ClassVar
from urllib.parse import urlsplit

from instadescribe_contracts.environment import bridged_environment
from instadescribe_contracts.provider import (
    OPENAI_G12_MAX_DURATION_SECS,
    PROVIDER_MAX_ATTEMPTS,
    ProviderName,
)
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True, slots=True)
class CognitoConfiguration:
    """Validated, route-local Cognito verifier configuration."""

    issuer: str
    app_client_id: str
    jwks_url: str


class Settings(BaseSettings):
    # validate_default: misconfigured explicit values fail fast at load
    # instead of surfacing as runtime surprises.
    model_config = SettingsConfigDict(extra="ignore", validate_default=True)

    def __init__(self, **values: object) -> None:
        # Read the canonical namespace while preserving a temporary, fail-closed
        # bridge for deployments that still emit v0.1 INSTASCRIBE_* names.
        names = (
            field.validation_alias
            for field in type(self).model_fields.values()
            if isinstance(field.validation_alias, str)
            and field.validation_alias.startswith("INSTADESCRIBE_")
        )
        with bridged_environment(canonical_names=names):
            super().__init__(**values)

    # Absent DATABASE_URL keeps liveness working and turns readiness 503
    # ("configuration") instead of crashing the process at import.
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    # FastAPI docs/OpenAPI routes: enabled locally, switched off for the
    # public portfolio environment before G9 (G1 review correction #4).
    enable_docs: bool = Field(default=True, validation_alias="INSTADESCRIBE_ENABLE_DOCS")

    # --- G3: portfolio token (SHA-256 hex digest of the token, never the token) ---
    portfolio_token_sha256: str | None = Field(
        default=None, validation_alias="PORTFOLIO_TOKEN_SHA256"
    )

    # --- G3: S3/media configuration ---
    media_bucket: str = Field(
        # The legacy physical bucket name is intentionally unchanged; new beta
        # stacks pass their explicit InstaDescribe bucket through the canonical
        # environment variable.
        default="instascribe-media",
        validation_alias="INSTADESCRIBE_MEDIA_BUCKET",
    )
    aws_region: str = Field(default="eu-west-2", validation_alias="AWS_DEFAULT_REGION")
    # Split endpoint views: SDK calls vs browser-visible presigned URLs.
    # None means real AWS endpoints (deployment).
    s3_endpoint_internal: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_S3_ENDPOINT_INTERNAL"
    )
    s3_endpoint_public: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_S3_ENDPOINT_PUBLIC"
    )
    s3_force_path_style: bool = Field(
        default=False, validation_alias="INSTADESCRIBE_S3_FORCE_PATH_STYLE"
    )
    # Conservative numeric bounds (not truthiness): a mistyped expiry or limit
    # fails configuration load rather than producing surprising policies.
    presign_expiry_secs: int = Field(
        default=900, ge=60, le=3600, validation_alias="INSTADESCRIBE_PRESIGN_EXPIRY_SECS"
    )
    # G6: manifest download URLs are deliberately shorter-lived than upload
    # POSTs — one manifest request signs every reference against one common
    # expiry instant; URLs are never persisted or logged.
    download_presign_expiry_secs: int = Field(
        default=300,
        ge=60,
        le=900,
        validation_alias="INSTADESCRIBE_DOWNLOAD_PRESIGN_EXPIRY_SECS",
    )

    # --- G4: SQS (container-internal endpoint only — never the browser view) ---
    sqs_endpoint_internal: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_SQS_ENDPOINT_INTERNAL"
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
    # Investigation jobs are intentionally isolated from the legacy audio-
    # description queue.  The default names a new resource; it never renames
    # or repurposes the deployed v0.1 queue.
    investigation_queue_name: str = Field(
        default="instadescribe-investigation",
        min_length=1,
        max_length=80,
        validation_alias="INSTADESCRIBE_INVESTIGATION_QUEUE",
    )
    investigation_queue_url: str | None = Field(
        default=None,
        validation_alias="INSTADESCRIBE_INVESTIGATION_QUEUE_URL",
    )

    @field_validator("work_queue_url", "investigation_queue_url")
    @classmethod
    def _queue_url_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v.startswith(("http://", "https://")) or len(v) > 512:
            raise ValueError("work queue URL must be a bounded http(s) URL")
        return v

    @model_validator(mode="after")
    def _queue_isolation(self) -> "Settings":
        if self.work_queue_name == self.investigation_queue_name:
            raise ValueError("audio-description and investigation queues must be distinct")
        if (
            self.work_queue_url is not None
            and self.investigation_queue_url is not None
            and self.work_queue_url == self.investigation_queue_url
        ):
            raise ValueError("audio-description and investigation queue URLs must be distinct")
        work_url_name = (
            urlsplit(self.work_queue_url).path.rstrip("/").rsplit("/", 1)[-1]
            if self.work_queue_url
            else None
        )
        investigation_url_name = (
            urlsplit(self.investigation_queue_url).path.rstrip("/").rsplit("/", 1)[-1]
            if self.investigation_queue_url
            else None
        )
        if work_url_name == self.investigation_queue_name:
            raise ValueError("audio-description queue URL targets the investigation queue")
        if investigation_url_name == self.work_queue_name:
            raise ValueError("investigation queue URL targets the audio-description queue")
        return self

    # --- G3: portfolio limits and spend bounds (server-authoritative) ---
    max_upload_bytes: int = Field(
        default=250 * 1024 * 1024,
        ge=1,
        le=1024 * 1024 * 1024,
        validation_alias="INSTADESCRIBE_MAX_UPLOAD_BYTES",
    )
    max_duration_secs: int = Field(
        default=300, ge=1, le=3600, validation_alias="INSTADESCRIBE_MAX_DURATION_SECS"
    )
    deployment_tier: str = Field(
        default="portfolio", validation_alias="INSTADESCRIBE_DEPLOYMENT_TIER"
    )
    max_attempts: int = Field(default=3, ge=1, le=3, validation_alias="INSTADESCRIBE_MAX_ATTEMPTS")
    allowed_origins: list[str] = Field(
        default=["http://localhost:5173"], validation_alias="INSTADESCRIBE_ALLOWED_ORIGINS"
    )
    integration_review_base_url: str = Field(
        default="http://localhost:5173",
        validation_alias="INSTADESCRIBE_REVIEW_BASE_URL",
    )
    # Server-side HMAC key for integration API-key digests. It is deliberately
    # optional so the deployed legacy-only portfolio remains healthy; the
    # integration authentication path fails closed while it is absent.
    integration_api_key_pepper: str | None = Field(
        default=None,
        validation_alias="INSTADESCRIBE_API_KEY_PEPPER",
        repr=False,
    )
    # Browser/BFF authentication is optional for the legacy portfolio runtime.
    # `/api/app/v1` fails closed while any value is absent or malformed; these
    # settings intentionally do not weaken global liveness/readiness behavior.
    cognito_issuer: str | None = Field(default=None, validation_alias="COGNITO_ISSUER")
    cognito_app_client_id: str | None = Field(
        default=None,
        validation_alias="COGNITO_APP_CLIENT_ID",
        repr=False,
    )
    cognito_user_pool_id: str | None = Field(
        default=None,
        validation_alias="COGNITO_USER_POOL_ID",
    )
    cognito_jwks_url: str | None = Field(default=None, validation_alias="COGNITO_JWKS_URL")
    # Canonical unpadded base64url for exactly 32 random bytes. Validation is
    # route-local so a missing/malformed Browser trust boundary fails closed
    # without taking down legacy liveness.
    browser_assertion_secret: str | None = Field(
        default=None,
        validation_alias="BROWSER_ASSERTION_SECRET",
        repr=False,
    )
    # Operator-managed beta allowlist.  Webhook registration is deliberately
    # not self-service; the dispatcher refuses every destination while this is
    # empty and re-checks DNS immediately before each attempt.
    webhook_allowed_hosts: tuple[str, ...] = Field(
        default=(),
        validation_alias="INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS",
    )
    # The client never selects a provider or pipeline revision. This is a
    # code-bounded deployment setting; no environment variable can widen the
    # exact fake/openai set.
    provider: ProviderName = Field(default="fake", validation_alias="INSTADESCRIBE_PROVIDER")
    # ``local`` is an investigation-worker identity, not an audio-description
    # deployment mode. Investigation creation stamps it explicitly and routes
    # to the dedicated queue; the shared API provider setting remains bounded
    # to the two audio-description backends.
    provider_allowlist: ClassVar[tuple[ProviderName, ...]] = ("fake", "openai")
    model_allowlist: tuple[str, ...] = ("gpt-4.1",)
    fps_allowlist: tuple[float, ...] = (0.5, 1.0)
    frame_quality_allowlist: tuple[str, ...] = ("low",)
    chunk_size_allowlist: tuple[int, ...] = (60, 120)
    preset_style_allowlist: tuple[str, ...] = (
        "documentary",
        "cinematic",
        "news",
        "sports",
        "education",
    )
    allowed_content_types: tuple[str, ...] = ("video/mp4", "video/quicktime", "video/webm")
    allowed_extensions: tuple[str, ...] = (".mp4", ".mov", ".webm")

    # Immutable server-supplied provenance ('dev' locally; the immutable
    # code/image revision in deployment). Clients may never choose it.
    pipeline_revision: str | None = Field(
        default=None, validation_alias="INSTADESCRIBE_PIPELINE_REVISION"
    )

    @field_validator("pipeline_revision")
    @classmethod
    def _revision_trimmed_and_bounded(cls, v: str | None) -> str | None:
        # None stays allowed (readiness reports 'configuration'); an explicit
        # value must be non-empty after trimming and fit the 120-char column.
        if v is None:
            return None
        v = v.strip()
        if not 1 <= len(v) <= 120:
            raise ValueError("pipeline revision must be 1-120 characters after trimming")
        return v

    @field_validator("integration_review_base_url")
    @classmethod
    def _review_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if (
            not value.startswith(("http://", "https://"))
            or len(value) > 500
            or any(char.isspace() for char in value)
        ):
            raise ValueError("review base URL must be a bounded http(s) URL")
        return value

    @field_validator("integration_api_key_pepper")
    @classmethod
    def _api_key_pepper(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value.encode("utf-8")) < 32:
            raise ValueError("integration API key pepper must be at least 32 bytes")
        return value

    @field_validator("webhook_allowed_hosts")
    @classmethod
    def _webhook_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw in values:
            host = raw.strip().rstrip(".").lower()
            if (
                not host
                or len(host) > 253
                or any(char.isspace() for char in host)
                or ":" in host
                or "/" in host
                or host.startswith(".")
                or host.endswith(".")
            ):
                raise ValueError("webhook allowlist must contain bare DNS hostnames")
            normalized.append(host)
        if len(set(normalized)) != len(normalized):
            raise ValueError("webhook allowlist must not contain duplicates")
        return tuple(normalized)

    @model_validator(mode="after")
    def _g12_provider_limits(self) -> "Settings":
        # Real-provider G12 is a bounded smoke, not an unbounded public SaaS
        # workload. Fake remains compatible with the existing five-minute
        # local/cloud evaluation path.
        if self.provider not in self.provider_allowlist:
            raise ValueError("API provider must be fake or openai")
        if (
            self.deployment_tier != "beta"
            and self.provider == "openai"
            and self.max_duration_secs > OPENAI_G12_MAX_DURATION_SECS
        ):
            raise ValueError("OpenAI G12 duration limit must not exceed 120 seconds")
        if self.max_attempts != PROVIDER_MAX_ATTEMPTS[self.provider]:
            raise ValueError("configured attempt bound does not match provider policy")
        return self

    @field_validator("deployment_tier")
    @classmethod
    def _deployment_tier(cls, value: str) -> str:
        if value not in {"portfolio", "beta"}:
            raise ValueError("deployment tier must be portfolio or beta")
        return value

    def token_digest_valid(self) -> bool:
        d = self.portfolio_token_sha256
        return bool(d) and len(d) == 64 and all(c in "0123456789abcdefABCDEF" for c in d)

    def cognito_configuration(self) -> CognitoConfiguration | None:
        """Return a strict Cognito trust boundary, or ``None`` to fail closed.

        Configuration errors are intentionally route-local: a missing browser
        identity integration must not prevent the legacy API from booting.
        Requiring the conventional JWKS path on the issuer origin also prevents
        a typo from silently changing which host supplies signing keys.
        """

        if not self.cognito_issuer or not self.cognito_app_client_id or not self.cognito_jwks_url:
            return None
        issuer = self.cognito_issuer.strip().rstrip("/")
        client_id = self.cognito_app_client_id.strip()
        jwks_url = self.cognito_jwks_url.strip()
        if (
            not issuer
            or not 1 <= len(client_id) <= 256
            or any(char.isspace() for char in client_id)
            or len(issuer) > 2048
            or len(jwks_url) > 2048
            or any(char.isspace() for char in issuer + jwks_url)
        ):
            return None
        try:
            issuer_parts = urlsplit(issuer)
            jwks_parts = urlsplit(jwks_url)
            issuer_port = issuer_parts.port
            jwks_port = jwks_parts.port
        except ValueError:
            return None
        if (
            issuer_parts.scheme != "https"
            or jwks_parts.scheme != "https"
            or not issuer_parts.hostname
            or not jwks_parts.hostname
            or issuer_parts.username
            or issuer_parts.password
            or jwks_parts.username
            or jwks_parts.password
            or issuer_parts.query
            or issuer_parts.fragment
            or jwks_parts.query
            or jwks_parts.fragment
            or issuer_parts.hostname.lower() != jwks_parts.hostname.lower()
            or issuer_port != jwks_port
        ):
            return None
        expected_jwks_path = f"{issuer_parts.path.rstrip('/')}/.well-known/jwks.json"
        if jwks_parts.path != expected_jwks_path:
            return None
        return CognitoConfiguration(
            issuer=issuer,
            app_client_id=client_id,
            jwks_url=jwks_url,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
