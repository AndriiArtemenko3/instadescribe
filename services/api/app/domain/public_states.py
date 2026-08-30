"""Stable public lifecycle projection for B2B integrations.

Internal orchestration may add detail without forcing integrators to mirror
the worker state machine. This mapping is deliberately exhaustive.
"""

from enum import StrEnum

from app.domain.states import JobState


class PublicJobState(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_PUBLIC_STATE: dict[JobState, PublicJobState] = {
    JobState.AWAITING_UPLOAD: PublicJobState.AWAITING_UPLOAD,
    JobState.UPLOAD_COMPLETE: PublicJobState.QUEUED,
    JobState.QUEUED: PublicJobState.QUEUED,
    JobState.PROCESSING: PublicJobState.PROCESSING,
    JobState.READY_FOR_REVIEW: PublicJobState.NEEDS_REVIEW,
    JobState.EXPORT_QUEUED: PublicJobState.RENDERING,
    JobState.EXPORTING: PublicJobState.RENDERING,
    JobState.COMPLETED: PublicJobState.COMPLETED,
    JobState.FAILED: PublicJobState.FAILED,
    JobState.CANCELLED: PublicJobState.CANCELLED,
}


def to_public_state(state: JobState) -> PublicJobState:
    return _PUBLIC_STATE[state]
