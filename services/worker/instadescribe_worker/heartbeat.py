"""Bounded, synchronous lease and SQS visibility heartbeats.

The controller is deliberately driven by the executor's existing 200 ms poll
loop instead of owning a background thread.  That gives it deterministic
lifecycle semantics: when the subprocess loop stops there is no timer, thread
or database session left behind.  Every due pulse first renews the durable
PostgreSQL lease under the exact claim token and then extends the current SQS
receipt.  There is still no exactly-once claim: SQS remains at-least-once and
the database token/lease predicates fence duplicate or stale workers.
"""

import time
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from instadescribe_worker.claim import renew_lease


class LeaseLostError(RuntimeError):
    """The claim token no longer owns a live PROCESSING lease."""


class LeaseDatabaseUnavailableError(RuntimeError):
    """The lease could not be authoritatively renewed in PostgreSQL."""


class HeartbeatQueueUnavailableError(RuntimeError):
    """The current SQS receipt visibility could not be extended."""


class LeaseHeartbeat:
    """One executor-scoped, rate-limited ownership heartbeat.

    ``pulse(force=True)`` is used at blocking-stage boundaries.  Ordinary
    executor ticks call ``pulse()``; these are no-ops until the configured
    monotonic interval has elapsed.  Exceptions contain categories only and
    never include SDK/database text that could expose endpoints or secrets.
    """

    def __init__(
        self,
        session: Session,
        sqs,
        queue_url: str,
        receipt_handle: str,
        job_id: uuid.UUID,
        owner_token: str,
        *,
        lease_duration_secs: int,
        visibility_timeout_secs: int,
        interval_secs: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._sqs = sqs
        self._queue_url = queue_url
        self._receipt_handle = receipt_handle
        self._job_id = job_id
        self._owner_token = owner_token
        self._lease_duration_secs = lease_duration_secs
        self._visibility_timeout_secs = visibility_timeout_secs
        self._interval_secs = interval_secs
        self._clock = clock
        self._last_success_at = float("-inf")
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def pulse(self, *, force: bool = False) -> None:
        if self._closed:
            return
        now = self._clock()
        if not force and now - self._last_success_at < self._interval_secs:
            return
        try:
            renewed = renew_lease(
                self._session,
                self._job_id,
                self._owner_token,
                self._lease_duration_secs,
            )
        except Exception:
            try:
                self._session.rollback()
            except Exception:
                pass
            raise LeaseDatabaseUnavailableError("lease database unavailable") from None
        if not renewed:
            raise LeaseLostError("processing lease lost")
        try:
            self._sqs.change_message_visibility(
                QueueUrl=self._queue_url,
                ReceiptHandle=self._receipt_handle,
                VisibilityTimeout=self._visibility_timeout_secs,
            )
        except Exception:
            raise HeartbeatQueueUnavailableError("queue heartbeat unavailable") from None
        self._last_success_at = now

    def close(self) -> None:
        """Prevent any later callback from issuing a heartbeat.

        There is no background worker to join; closing is immediate and
        deterministic on every normal, exceptional and shutdown exit.
        """

        self._closed = True
