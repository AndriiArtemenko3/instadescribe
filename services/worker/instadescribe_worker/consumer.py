"""Strict SQS consumer (B4, hardened by G5.1 A2–A4/B3/B4) and the
claim→process→finalize/fail loop.

One message at a time; parsing only through the strict wire parser; the
COMPLETE persisted logical identity (jobId + messageId + original
requestedAt; taskType/schema are enforced by the parser) is validated BEFORE
any terminal acknowledgement or claim. Poison and forged/stale identities
stay undeleted for the redrive policy with zero job mutation.

Terminal acknowledgement policy (canonical identity required first):
- READY_FOR_REVIEW/COMPLETED duplicate  -> acknowledged, no rerun;
- CANCELLED duplicate                   -> acknowledged (documented policy:
  cancellation is a durable owner action; no compute will ever run, so
  retaining the message would only pollute the DLQ);
- FAILED duplicate                      -> acknowledged ONLY for the finite
  non-retryable code set; retryable, unknown and retry_exhausted codes are
  left for DLQ/repair;
- any terminal delete failure           -> sanitized ack-pending event, state
  untouched, redelivery retries the acknowledgement (never a
  processing-failure transition).

Guarded-transition results are honored everywhere: False is ownership loss —
no delete, no visibility change, no success/failure claim. SQS deletion after
a successful commit is a SEPARATE acknowledgement step (A4): its failure is
`success_ack_pending`, never a processing failure. v0.2 adds database leases,
expired-PROCESSING reclaim and paired SQS visibility heartbeats; delivery is
still explicitly at-least-once, never exactly-once.
"""

from functools import lru_cache

import boto3
import sqlalchemy as sa
from app.domain.states import JobState
from app.models import Job
from app.models.lifecycle import Asset
from app.services.quota import (
    QuotaExceededError,
    QuotaStateError,
    reconcile_measured_media,
)
from instadescribe_contracts.queue import QueueMessage
from instadescribe_contracts.settings import StoredJobSettings

from instadescribe_worker import artifacts as artifacts_mod
from instadescribe_worker import claim as claim_mod
from instadescribe_worker.config import PROVIDER_ALLOWLIST, WorkerSettings, get_worker_settings
from instadescribe_worker.db import get_sessionmaker
from instadescribe_worker.executor import (
    WorkerShutdownRequested,
    run_pipeline,
    shutdown_requested,
)
from instadescribe_worker.failures import ACK_SAFE_FAILED_CODES, FailureCode, JobFailure
from instadescribe_worker.heartbeat import (
    HeartbeatQueueUnavailableError,
    LeaseDatabaseUnavailableError,
    LeaseHeartbeat,
    LeaseLostError,
)
from instadescribe_worker.logging import log
from instadescribe_worker.media_validation import validate_media
from instadescribe_worker.progress import ProgressMirror
from instadescribe_worker.source import download_source, download_verified_asset
from instadescribe_worker.workspace import build_workspace, write_job_files

SUCCESS_TERMINAL = {JobState.READY_FOR_REVIEW.value, JobState.COMPLETED.value}


@lru_cache
def _sqs():
    settings = get_worker_settings()
    return boto3.client(
        "sqs", region_name=settings.aws_region, endpoint_url=settings.sqs_endpoint_internal
    )


@lru_cache
def _s3():
    settings = get_worker_settings()
    return boto3.client(
        "s3", region_name=settings.aws_region, endpoint_url=settings.s3_endpoint_internal
    )


@lru_cache
def _queue_url() -> str:
    settings = get_worker_settings()
    if settings.work_queue_url:
        return settings.work_queue_url
    return _sqs().get_queue_url(QueueName=settings.work_queue_name)["QueueUrl"]


def reset_worker_caches() -> None:
    _sqs.cache_clear()
    _s3.cache_clear()
    _queue_url.cache_clear()


def _ack(receipt: str, *, job_id, outcome: str, pending_event: str, **extra) -> str:
    """Terminal/success acknowledgement as its own step: a delete failure is
    a sanitized *_ack_pending outcome — state stays untouched and canonical
    redelivery retries the acknowledgement."""
    try:
        _sqs().delete_message(QueueUrl=_queue_url(), ReceiptHandle=receipt)
    except Exception as exc:
        log(
            pending_event,
            level="warning",
            job_id=job_id,
            category="queue-ack",
            error_class=type(exc).__name__,
        )
        return f"{outcome}_ack_pending"
    log(f"message_{outcome}", job_id=job_id, **extra)
    return outcome


def _normalize_in_progress_visibility(settings: WorkerSettings, receipt: str, *, job_id) -> None:
    """Replace a queue-default visibility horizon with the bounded lease one.

    A duplicate delivery cannot claim a fresh PROCESSING lease, but leaving
    its receipt at an infrastructure default (for example 30–60 minutes)
    creates an avoidable recovery gap.  Failure is safe and sanitized: the
    receipt remains at its existing timeout and the database row is untouched.
    """
    try:
        _sqs().change_message_visibility(
            QueueUrl=_queue_url(),
            ReceiptHandle=receipt,
            VisibilityTimeout=settings.heartbeat_visibility_timeout_secs,
        )
    except Exception as exc:
        log(
            "in_progress_visibility_failed",
            level="warning",
            job_id=job_id,
            category="queue-heartbeat",
            error_class=type(exc).__name__,
        )


def run_once(settings: WorkerSettings | None = None) -> str:
    """Consume at most one message; returns a stable outcome string used by
    tests, logs, backoff and the smoke evidence."""
    if shutdown_requested():
        return "shutdown"
    settings = settings or get_worker_settings()
    try:
        sqs = _sqs()
        queue_url = _queue_url()
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=settings.long_poll_secs,
            AttributeNames=["ApproximateReceiveCount"],
        )
    except Exception as exc:
        # Sanitized: category + class name only — never SDK text, which can
        # embed endpoints, credentials hints or signed URLs.
        log(
            "queue_unavailable",
            level="error",
            category="queue-receive",
            error_class=type(exc).__name__,
        )
        return "infra_error"
    messages = response.get("Messages", [])
    if not messages:
        return "empty"
    raw = messages[0]
    receipt = raw["ReceiptHandle"]
    receive_count = raw.get("Attributes", {}).get("ApproximateReceiveCount")

    try:
        message = QueueMessage.from_body(raw["Body"])
    except Exception:
        # Poison: never the raw body, never deleted — redrive owns it.
        log("message_poison", level="warning", category="parse", receive_count=receive_count)
        return "poison"

    session = None
    try:
        try:
            session = get_sessionmaker()()
            job = session.get(Job, message.job_id)
        except Exception as exc:
            log(
                "pre_claim_db_unavailable",
                level="error",
                category="db-load",
                error_class=type(exc).__name__,
            )
            return "infra_error"

        # A2: the COMPLETE logical identity gates EVERY terminal policy. A
        # forged/mismatched message naming a real (even terminal) job is
        # stale/poison-like: no mutation, no delete; redrive owns it.
        if (
            job is None
            or job.enqueue_message_id != message.message_id
            or job.enqueue_requested_at != message.requested_at
        ):
            log(
                "message_stale_identity",
                level="warning",
                message_id=message.message_id,
                receive_count=receive_count,
            )
            return "stale"

        # G12: one worker deployment serves exactly one server-controlled
        # provider. A differently stamped job remains byte-for-byte untouched
        # and unacknowledged; a correctly configured worker may consume it.
        # The provider is also included in the atomic claim predicate below
        # to close a load-to-claim race.
        if job.provider != settings.provider:
            log(
                "message_provider_mismatch",
                level="warning",
                job_id=job.id,
                category="provider",
            )
            return "provider_mismatch"

        if job.status in SUCCESS_TERMINAL:
            return _ack(
                receipt,
                job_id=job.id,
                outcome="duplicate_success",
                pending_event="terminal_ack_pending",
                message_id=message.message_id,
            )
        if job.status == JobState.CANCELLED.value:
            return _ack(
                receipt,
                job_id=job.id,
                outcome="duplicate_cancelled",
                pending_event="terminal_ack_pending",
            )
        if job.status == JobState.FAILED.value:
            if job.error_code in ACK_SAFE_FAILED_CODES:
                return _ack(
                    receipt,
                    job_id=job.id,
                    outcome="duplicate_failed",
                    pending_event="terminal_ack_pending",
                    error_code=job.error_code,
                )
            if job.error_code == FailureCode.RETRY_EXHAUSTED.value:
                log("message_exhausted_await_dlq", level="warning", job_id=job.id)
                return "exhausted_await_dlq"  # leave for DLQ transfer
            # Retryable or UNKNOWN error code on a FAILED row: never assume
            # it is safe to swallow the message — leave it for DLQ/repair.
            log(
                "message_failed_await_repair",
                level="warning",
                job_id=job.id,
                error_code=job.error_code,
            )
            return "failed_await_repair"

        was_processing = job.status == JobState.PROCESSING.value
        try:
            claimed = claim_mod.claim_job(
                session,
                job.id,
                message.message_id,
                message.requested_at,
                configured_max_attempts=settings.max_attempts,
                provider=settings.provider,
                lease_duration_secs=settings.lease_duration_secs,
            )
            if claimed is None:
                if claim_mod.reject_attempt_policy_mismatch(
                    session,
                    job.id,
                    message.message_id,
                    message.requested_at,
                    configured_max_attempts=settings.max_attempts,
                    provider=settings.provider,
                ):
                    log(
                        "job_attempt_policy_rejected",
                        level="warning",
                        job_id=job.id,
                        category="attempt-policy",
                    )
                    return _ack(
                        receipt,
                        job_id=job.id,
                        outcome="failed_attempt_policy",
                        pending_event="failed_ack_pending",
                        error_code=FailureCode.INVALID_SETTINGS.value,
                    )
                if claim_mod.exhaust_unclaimable(
                    session,
                    job.id,
                    message.message_id,
                    message.requested_at,
                    configured_max_attempts=settings.max_attempts,
                    provider=settings.provider,
                ):
                    # Durable FAILED committed; message left undeleted so the
                    # redrive policy moves it to the DLQ.
                    log("job_exhausted", level="warning", job_id=job.id)
                    return "exhausted"
                if was_processing:
                    # A fresh lease (or a concurrent renew/reclaim winner) is
                    # authoritative.  Keep the message for later redelivery.
                    _normalize_in_progress_visibility(settings, receipt, job_id=job.id)
                    log("message_in_progress", job_id=job.id)
                    return "in_progress"
                # Both consumers may have loaded QUEUED before the winner's
                # atomic claim committed.  The loser therefore has
                # was_processing=False even though the authoritative row is
                # now PROCESSING.  Bound every otherwise-unresolved claim
                # loss to the lease horizon rather than inheriting a long
                # queue default.  A terminal/race state remains untouched and
                # is re-evaluated on the bounded redelivery.
                _normalize_in_progress_visibility(settings, receipt, job_id=job.id)
                log("claim_lost", job_id=message.job_id, receive_count=receive_count)
                return "lost_claim"  # state moved on; redelivery re-evaluates
        except Exception as exc:
            log(
                "claim_db_unavailable",
                level="error",
                category="db-claim",
                error_class=type(exc).__name__,
            )
            return "infra_error"

        log(
            "job_reclaimed" if was_processing else "job_claimed",
            job_id=claimed.id,
            message_id=message.message_id,
            worker_label=settings.worker_id,
            claim_token=claimed.worker_id,
            attempt=claimed.attempt_count,
            receive_count=receive_count,
        )
        return _process_claimed(settings, session, claimed, receipt)
    finally:
        if session is not None:
            session.close()


def _process_claimed(settings: WorkerSettings, session, job: Job, receipt: str) -> str:
    token = job.worker_id  # this claim's fencing token (A1)
    workspace = None
    heartbeat = LeaseHeartbeat(
        session,
        _sqs(),
        _queue_url(),
        receipt,
        job.id,
        token,
        lease_duration_secs=settings.lease_duration_secs,
        visibility_timeout_secs=settings.heartbeat_visibility_timeout_secs,
        interval_secs=settings.heartbeat_interval_secs,
    )
    try:
        # Close the claim-to-first-model-work gap and verify both ownership
        # authorities before any source or provider work starts.
        heartbeat.pulse(force=True)
        provider = (job.provider or "").strip()
        if provider not in PROVIDER_ALLOWLIST or provider != settings.provider:
            raise JobFailure(
                FailureCode.INVALID_SETTINGS, "provider is not allowlisted for this worker"
            )
        # B4: false provenance is a finite non-retryable failure BEFORE any
        # source/model work — never silent processing under the wrong revision.
        if job.pipeline_revision != settings.pipeline_revision:
            raise JobFailure(
                FailureCode.PIPELINE_REVISION_MISMATCH,
                "job was created for a different pipeline revision",
            )
        # B4: strict stored-settings contract — malformed persisted settings
        # are deterministic invalid_settings, not three retryable crashes.
        try:
            stored_settings = StoredJobSettings.model_validate(job.settings)
        except Exception:
            raise JobFailure(
                FailureCode.INVALID_SETTINGS, "persisted settings fail the stored contract"
            ) from None
        if job.model != stored_settings.model:
            raise JobFailure(FailureCode.INVALID_SETTINGS, "job model provenance is inconsistent")
        if not job.input_object_key or not job.source_etag:
            raise JobFailure(FailureCode.INVALID_SETTINGS, "job is missing verified source state")

        workspace = build_workspace(settings.workspace_root, settings.pipeline_source, str(job.id))
        heartbeat.pulse(force=True)
        source_sha = download_source(_s3(), settings.media_bucket, job, workspace.video_path)
        heartbeat.pulse(force=True)
        measured_duration = validate_media(
            workspace.video_path,
            job.input_content_type or "",
            settings.max_duration_secs,
            source_name=job.input_object_key,
        )
        # C3: the MEASURED duration is authoritative — persist it under the
        # claim guard before model work; a failed guard is stale ownership.
        try:
            owns_reconciled_quota = reconcile_measured_media(
                session,
                job.id,
                token,
                actual_seconds=round(measured_duration, 3),
            )
        except QuotaExceededError:
            raise JobFailure(
                FailureCode.QUOTA_EXCEEDED,
                "organization media quota is exhausted",
            ) from None
        except QuotaStateError:
            raise JobFailure(
                FailureCode.INTERNAL_ERROR,
                "media quota could not be reconciled",
            ) from None
        if not owns_reconciled_quota:
            log("stale_owner_abort", level="warning", job_id=job.id, phase="duration")
            return "stale_owner"

        transcript_asset = session.execute(
            sa.select(Asset).where(
                Asset.organization_id == job.organization_id,
                Asset.job_id == job.id,
                Asset.asset_type == "source_transcript",
            )
        ).scalar_one_or_none()
        provided_transcript_path = None
        provided_transcript_format = None
        if transcript_asset is not None:
            if transcript_asset.transcript_format not in {"vtt", "srt"}:
                raise JobFailure(
                    FailureCode.INVALID_TRANSCRIPT,
                    "provided transcript format is invalid",
                )
            provided_transcript_format = transcript_asset.transcript_format
            provided_transcript_path = (
                workspace.job_dir / f"provided-transcript.{provided_transcript_format}"
            )
            heartbeat.pulse(force=True)
            download_verified_asset(
                _s3(),
                settings.media_bucket,
                transcript_asset,
                provided_transcript_path,
            )
            # Validate before the child can make a paid model call. run_job.py
            # parses the same bytes again to build the exact analysis files;
            # there is deliberately no hidden ASR fallback on any failure.
            from modular_pipeline.timed_transcript import (
                TimedTranscriptError,
                parse_timed_transcript_bytes,
            )

            try:
                parse_timed_transcript_bytes(
                    provided_transcript_path.read_bytes(),
                    provided_transcript_format,
                    video_duration_seconds=measured_duration,
                )
            except TimedTranscriptError:
                raise JobFailure(
                    FailureCode.INVALID_TRANSCRIPT,
                    "provided transcript is not a valid timed UTF-8 document",
                ) from None
        write_job_files(
            workspace,
            str(job.id),
            job.settings,
            measured_duration_secs=round(measured_duration, 3),
            provided_transcript_path=provided_transcript_path,
            provided_transcript_format=provided_transcript_format,
        )
        heartbeat.pulse(force=True)

        result = run_pipeline(
            workspace,
            str(job.id),
            timeout_secs=settings.subprocess_timeout_secs,
            grace_secs=settings.grace_secs,
            on_progress=ProgressMirror(session, job.id, token),
            on_tick=heartbeat.pulse,
            provider=settings.provider,
            openai_api_key=(
                settings.openai_api_key.get_secret_value()
                if settings.provider == "openai" and settings.openai_api_key
                else None
            ),
            max_provider_calls=settings.max_provider_calls,
            max_provider_output_tokens=settings.max_provider_output_tokens,
        )
        heartbeat.pulse(force=True)
        if result.timed_out:
            raise JobFailure(FailureCode.PIPELINE_TIMEOUT, "pipeline exceeded its time budget")
        if result.exit_code != 0:
            # Authoritative even when status.json is absent/stale/'ready'.
            log(
                "pipeline_nonzero_exit",
                level="warning",
                job_id=job.id,
                exit_code=result.exit_code,
            )
            raise JobFailure(FailureCode.PIPELINE_FAILED, "pipeline exited abnormally")

        uploads = artifacts_mod.validate_outputs(
            workspace,
            str(job.id),
            job.attempt_count,
            provider=provider,
            model=stored_settings.model,
            expected_chunk_size=stored_settings.chunk_size,
        )
        finalized = artifacts_mod.upload_and_finalize(
            session,
            _s3(),
            settings.media_bucket,
            job,
            token,
            uploads,
            source_sha,
            before_upload=heartbeat.pulse,
        )
        if not finalized:
            log("finalize_lost_ownership", level="warning", job_id=job.id)
            return "stale_finalize"  # message stays; no success reported
    except WorkerShutdownRequested:
        log("job_shutdown_abort", level="warning", job_id=job.id)
        # No acknowledgement or terminal transition: the processing lease and
        # SQS visibility expire naturally for a later fenced reclaim.
        return "shutdown"
    except LeaseLostError:
        log("lease_lost_abort", level="warning", job_id=job.id)
        return "stale_owner"
    except LeaseDatabaseUnavailableError:
        log("lease_db_unavailable", level="error", job_id=job.id, category="db-heartbeat")
        return "db_unavailable"
    except HeartbeatQueueUnavailableError:
        log(
            "queue_heartbeat_unavailable",
            level="warning",
            job_id=job.id,
            category="queue-heartbeat",
        )
        return _handle_failure(
            settings,
            session,
            job,
            receipt,
            JobFailure(FailureCode.HEARTBEAT_FAILED, "processing heartbeat failed"),
            token,
        )
    except JobFailure as failure:
        return _handle_failure(settings, session, job, receipt, failure, token)
    except Exception as exc:
        # Class name only — never raw exception text.
        log(
            "job_internal_error",
            level="error",
            job_id=job.id,
            error_code="internal_error",
            error_class=type(exc).__name__,
        )
        return _handle_failure(
            settings,
            session,
            job,
            receipt,
            JobFailure(FailureCode.INTERNAL_ERROR, "unexpected worker failure"),
            token,
        )
    finally:
        heartbeat.close()
        # run_pipeline has already destroyed the process tree on every exit
        # path, so no live process can hold files in this workspace.
        if workspace is not None:
            workspace.cleanup()

    # A4: the processing result IS successful (rows + READY_FOR_REVIEW are
    # committed). Acknowledgement is a separate step whose failure must never
    # be misreported as a processing failure or shorten visibility.
    log("job_ready", job_id=job.id, attempt=job.attempt_count)
    return _ack(
        receipt,
        job_id=job.id,
        outcome="success",
        pending_event="success_ack_pending",
        attempt=job.attempt_count,
    )


def _handle_failure(
    settings: WorkerSettings, session, job: Job, receipt: str, failure: JobFailure, token: str
) -> str:
    """A3: every guarded result is honored — False means ownership was lost,
    so nothing is deleted, no visibility changes, and no failure is claimed."""
    try:
        if not failure.retryable:
            committed = claim_mod.guarded_transition(
                session,
                job.id,
                token,
                JobState.FAILED,
                error_code=failure.code.value,
                error_message=failure.public_message,
                worker_id=None,
                completed_at=sa.func.now(),
            )
            if not committed:
                log("stale_owner_no_transition", level="warning", job_id=job.id)
                return "stale_owner"
        elif job.attempt_count >= job.max_attempts:
            committed = claim_mod.guarded_transition(
                session,
                job.id,
                token,
                JobState.FAILED,
                error_code=FailureCode.RETRY_EXHAUSTED.value,
                error_message="processing attempts exhausted",
                worker_id=None,
                completed_at=sa.func.now(),
            )
            if not committed:
                log("stale_owner_no_transition", level="warning", job_id=job.id)
                return "stale_owner"
            log(
                "job_failed_exhausted",
                level="warning",
                job_id=job.id,
                failure_code=failure.code.value,
            )
            # The last heartbeat may have extended this receipt well beyond
            # the queue's ordinary test/operational redrive cadence.  Once
            # durable exhaustion is committed, make it promptly visible so
            # SQS can perform its configured DLQ transfer.  Failure remains
            # recoverable: the receipt is retained and its existing timeout
            # eventually expires.
            try:
                _sqs().change_message_visibility(
                    QueueUrl=_queue_url(),
                    ReceiptHandle=receipt,
                    VisibilityTimeout=settings.retry_visibility_delay_secs,
                )
            except Exception:
                log("exhausted_visibility_failed", level="warning", job_id=job.id)
            return "failed_exhausted"  # message left for DLQ redrive
        else:
            committed = claim_mod.guarded_transition(
                session,
                job.id,
                token,
                JobState.QUEUED,
                error_code=failure.code.value,
                error_message=failure.public_message,
                worker_id=None,
            )
            if not committed:
                log("stale_owner_no_transition", level="warning", job_id=job.id)
                return "stale_owner"
            try:
                _sqs().change_message_visibility(
                    QueueUrl=_queue_url(),
                    ReceiptHandle=receipt,
                    VisibilityTimeout=settings.retry_visibility_delay_secs,
                )
            except Exception:
                # Recoverable at the queue's 1800s default; not a heartbeat.
                log("retry_visibility_failed", level="warning", job_id=job.id)
            log("job_requeued", level="warning", job_id=job.id, error_code=failure.code)
            return "retry_requeued"
    except Exception as exc:
        # Database unavailable: no safe transition committed — never delete.
        try:
            session.rollback()
        except Exception:
            pass
        log(
            "failure_handling_db_unavailable",
            level="error",
            job_id=job.id,
            error_class=type(exc).__name__,
        )
        return "db_unavailable"

    # Deterministic failure was durably committed; acknowledge separately
    # (same policy as success acknowledgement — A4).
    log("job_failed_deterministic", level="warning", job_id=job.id, error_code=failure.code)
    return _ack(
        receipt,
        job_id=job.id,
        outcome="failed_deterministic",
        pending_event="failed_ack_pending",
        error_code=failure.code,
    )
