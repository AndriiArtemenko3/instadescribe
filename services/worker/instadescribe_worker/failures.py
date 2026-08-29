"""InstaDescribe finite failure classification: a documented error-code set with
bounded safe public messages; raw child/AWS/database text stays in sanitized
worker diagnostics only."""

from enum import StrEnum


class FailureCode(StrEnum):
    INVALID_SETTINGS = "invalid_settings"
    INVALID_MEDIA = "invalid_media"
    INVALID_TRANSCRIPT = "invalid_transcript"
    QUOTA_EXCEEDED = "quota_exceeded"
    SOURCE_IDENTITY_MISMATCH = "source_identity_mismatch"
    SOURCE_DOWNLOAD_FAILED = "source_download_failed"
    PIPELINE_REVISION_MISMATCH = "pipeline_revision_mismatch"
    PIPELINE_FAILED = "pipeline_failed"
    PIPELINE_TIMEOUT = "pipeline_timeout"
    HEARTBEAT_FAILED = "heartbeat_failed"
    ARTIFACTS_INVALID = "artifacts_invalid"
    RETRY_EXHAUSTED = "retry_exhausted"
    INTERNAL_ERROR = "internal_error"


# Deterministic — retrying cannot help; safe to acknowledge duplicates.
NON_RETRYABLE = frozenset(
    {
        FailureCode.INVALID_SETTINGS,
        FailureCode.INVALID_MEDIA,
        FailureCode.INVALID_TRANSCRIPT,
        FailureCode.QUOTA_EXCEEDED,
        FailureCode.SOURCE_IDENTITY_MISMATCH,
        FailureCode.PIPELINE_REVISION_MISMATCH,
    }
)

# Exact error-code strings whose FAILED duplicates are safe to acknowledge.
# Retryable codes, retry_exhausted and UNKNOWN codes stay undeleted for
# DLQ/repair (G5.1 A2).
ACK_SAFE_FAILED_CODES = frozenset(code.value for code in NON_RETRYABLE)


class JobFailure(Exception):
    def __init__(self, code: FailureCode, public_message: str) -> None:
        self.code = code
        self.retryable = code not in NON_RETRYABLE and code != FailureCode.RETRY_EXHAUSTED
        # Bounded, safe wording only — never raw exception/AWS/child text.
        self.public_message = public_message[:200]
        super().__init__(f"{code.value}: {self.public_message}")
