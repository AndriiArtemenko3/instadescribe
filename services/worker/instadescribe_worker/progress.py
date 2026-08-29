"""Guarded, throttled, monotonic progress mirroring (G5.1 B2).

Bounded 0–99 while processing (100 arrives only with the success
transaction), never moving backwards, and only while this claim's token still
owns the PROCESSING row. The executor polls status.json every 200 ms; an
unthrottled mirror would commit the same (stage, progress) five times a
second for the whole run. Dedup + a modest write interval keep database
traffic bounded while stage changes and near-final progress still land
immediately.
"""

import time
import uuid

import sqlalchemy as sa
from app.models import Job
from sqlalchemy.orm import Session

from instadescribe_worker.claim import guarded_update

_LEGAL_STAGES = {
    "queued",
    "initializing",
    "extracting_frames",
    "transcribing_audio",
    "analyzing_frames",
    "exporting",
    "complete",
}

# Progress-only changes are written at most this often; stage changes and
# >=99% progress always write immediately.
MIN_WRITE_INTERVAL_SECS = 1.0


class ProgressMirror:
    """One instance per claim attempt — carries the claim token and the
    dedup/throttle state."""

    def __init__(
        self,
        session: Session,
        job_id: uuid.UUID,
        owner_token: str,
        *,
        min_interval: float = MIN_WRITE_INTERVAL_SECS,
        clock=time.monotonic,
    ) -> None:
        self._session = session
        self._job_id = job_id
        self._owner_token = owner_token
        self._min_interval = min_interval
        self._clock = clock
        self._last_written: tuple[str, int] | None = None
        self._last_write_at = float("-inf")

    def __call__(self, stage: str, progress: int) -> None:
        if stage not in _LEGAL_STAGES:
            return  # unknown stage strings from a defensive status read are dropped
        bounded = max(0, min(int(progress), 99))
        observed = (stage, bounded)
        if observed == self._last_written:
            return  # dedup: an unchanged observation never writes
        stage_changed = self._last_written is None or stage != self._last_written[0]
        now = self._clock()
        if not stage_changed and bounded < 99 and now - self._last_write_at < self._min_interval:
            return  # throttle progress-only churn
        wrote = guarded_update(
            self._session,
            self._job_id,
            self._owner_token,
            stage=stage,
            progress=sa.func.greatest(Job.progress, bounded),
        )
        if wrote:
            self._last_written = observed
            self._last_write_at = now
