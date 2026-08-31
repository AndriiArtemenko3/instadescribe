"""Fenced persistence boundary for the video-investigation worker.

Heavy local inference runs outside the database transaction.  This module
turns an already validated ``LocalRunResult`` into tenant-scoped evidence,
steps and a belief snapshot, then publishes ``needs_review`` only when the
same live Job fence still owns the processing lease.  A stale attempt rolls
back every derived row and can never publish evidence.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from app.domain.states import JobState
from app.models import (
    BeliefSnapshot as PersistedBeliefSnapshot,
)
from app.models import (
    EvidenceItem as PersistedEvidenceItem,
)
from app.models import (
    Investigation as PersistedInvestigation,
)
from app.models import (
    InvestigationStep as PersistedInvestigationStep,
)
from app.models import (
    Job,
    JobEvent,
)
from app.models import (
    SourceRecord as PersistedSourceRecord,
)
from instadescribe_contracts.provider import (
    INVESTIGATION_MVP_MAX_DURATION_SECS,
    INVESTIGATION_MVP_MIN_DURATION_SECS,
)
from instadescribe_investigation_core import (
    LocalRunResult,
    to_primitive,
)
from instadescribe_investigation_core import (
    SourceRecord as CoreSourceRecord,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from instadescribe_worker.failures import FailureCode, JobFailure

_HEX_64 = re.compile(r"^[a-f0-9]{64}$")
_EVIDENCE_NAMESPACE = uuid.UUID("6aeeeb75-029c-474f-af4e-13bdc39b6c03")
_TRACE_NAMESPACE = uuid.UUID("5f14bc5d-4c92-481d-8422-d85505e61177")


class StoredInvestigationSettings(BaseModel):
    """Exact server-authored settings accepted by the local worker."""

    model_config = ConfigDict(extra="forbid")

    workflow_kind: Literal["video_investigation"]
    investigation_id: uuid.UUID
    investigation_kind: Literal["geolocate_provenance"]
    connectivity_policy: Literal["local"]


def parent_trace_id(investigation_id: uuid.UUID) -> uuid.UUID:
    """Derive the stable trace UUID a retry must reuse before child launch."""

    return uuid.uuid5(_TRACE_NAMESPACE, str(investigation_id))


def _canonical_ipc_timestamp(value: datetime) -> datetime:
    """Match the core's RFC3339 millisecond precision before strict IPC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise JobFailure(FailureCode.INVALID_SETTINGS, "source timestamp is not timezone-aware")
    utc_value = value.astimezone(UTC)
    return utc_value.replace(microsecond=(utc_value.microsecond // 1_000) * 1_000)


def parent_source_record(
    source: PersistedSourceRecord,
    *,
    content_sha256: str,
) -> CoreSourceRecord:
    """Project the complete durable source lineage into the Apache contract."""

    if _HEX_64.fullmatch(content_sha256) is None:
        raise JobFailure(FailureCode.INVALID_SETTINGS, "source digest is invalid")
    if source.media_sha256 is not None and source.media_sha256 != content_sha256:
        raise JobFailure(FailureCode.INVALID_SETTINGS, "source digest changed across attempts")
    license_basis = source.legal_basis
    if source.license_name:
        license_basis = f"{source.legal_basis}:{source.license_name}"
    consent_basis = (
        source.legal_basis if source.legal_basis in {"consent", "analyst_authorized"} else None
    )
    purge_after = (
        _canonical_ipc_timestamp(source.purge_after)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    return CoreSourceRecord(
        source_id=str(source.id),
        content_sha256=content_sha256,
        collected_at=_canonical_ipc_timestamp(source.collected_at),
        license_basis=license_basis,
        publisher=None,
        source_url=source.publisher_url,
        published_at=(
            _canonical_ipc_timestamp(source.published_at)
            if source.published_at is not None
            else None
        ),
        consent_basis=consent_basis,
        redistribution_policy=source.redistribution_policy,
        retention_policy=f"retentionDays={source.retention_days};purgeAfter={purge_after}",
    )


def validate_investigation_media_duration(duration_seconds: float) -> None:
    """Reapply the MVP envelope to the authoritative ffprobe duration."""

    if not (
        INVESTIGATION_MVP_MIN_DURATION_SECS
        <= duration_seconds
        <= INVESTIGATION_MVP_MAX_DURATION_SECS
    ):
        raise JobFailure(
            FailureCode.INVALID_MEDIA,
            "investigation media duration must be between "
            f"{INVESTIGATION_MVP_MIN_DURATION_SECS}s and "
            f"{INVESTIGATION_MVP_MAX_DURATION_SECS}s",
        )


def _owned_job_predicate(job_id: uuid.UUID, worker_id: str):
    return sa.exists(
        sa.select(Job.id).where(
            Job.id == job_id,
            Job.workflow_kind == "video_investigation",
            Job.status == JobState.PROCESSING.value,
            Job.worker_id == worker_id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > sa.func.now(),
        )
    )


def mark_investigation_stage(
    session: Session,
    job: Job,
    worker_id: str,
    status: str,
    *,
    runtime_provenance: dict | None = None,
) -> bool:
    """Persist a visible internal stage only for the current Job fence."""

    if status not in {"preprocessing", "investigating"}:
        raise ValueError("investigation worker stage is invalid")
    values: dict[str, object] = {"status": status, "updated_at": sa.func.now()}
    if runtime_provenance is not None:
        values["runtime_provenance"] = runtime_provenance
    changed = session.execute(
        sa.update(PersistedInvestigation)
        .where(
            PersistedInvestigation.organization_id == job.organization_id,
            PersistedInvestigation.job_id == job.id,
            PersistedInvestigation.status.in_(
                ("queued", "preprocessing") if status == "investigating" else ("queued",)
            ),
            _owned_job_predicate(job.id, worker_id),
        )
        .values(**values)
    ).rowcount
    session.commit()
    return changed == 1


def _stable_evidence_id(investigation_id: uuid.UUID, external_id: str) -> uuid.UUID:
    return uuid.uuid5(_EVIDENCE_NAMESPACE, f"{investigation_id}:{external_id}")


def _hex_digest(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return (
        normalized if _HEX_64.fullmatch(normalized) else hashlib.sha256(value.encode()).hexdigest()
    )


def _evidence_kind(value: str, attributes: dict) -> str:
    if attributes.get("role") == "keyframe":
        return "keyframe"
    return {
        "visual": "visual",
        "ocr": "ocr",
        "audio": "audio",
        "metadata": "metadata",
        "geoPrior": "geospatial",
        "web": "web",
        "visualMatch": "visual",
        "analyst": "visual",
    }.get(value, "visual")


def _verification_state(value: str) -> str:
    return {"verified": "verified", "rejected": "rejected"}.get(value, "proposed")


def _polarity(contributions: list[dict]) -> str:
    scores = [item.get("score") for item in contributions]
    numeric = [float(value) for value in scores if isinstance(value, int | float)]
    if numeric and max(numeric) > 0:
        return "supports"
    if numeric and min(numeric) < 0:
        return "contradicts"
    return "neutral"


def _candidate_rows(result: LocalRunResult) -> list[dict]:
    return [
        {
            "id": candidate.candidate_id,
            "label": candidate.label,
            "probability": candidate.probability,
        }
        for candidate in result.belief.candidates
    ]


def finalize_investigation(
    session: Session,
    job: Job,
    worker_id: str,
    result: LocalRunResult,
    *,
    source_sha256: str,
    runtime_provenance: dict,
) -> bool:
    """Atomically publish a local result under the current live Job fence."""

    investigation = session.execute(
        sa.select(PersistedInvestigation)
        .where(
            PersistedInvestigation.organization_id == job.organization_id,
            PersistedInvestigation.job_id == job.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if investigation is None or investigation.status not in {"preprocessing", "investigating"}:
        session.rollback()
        return False

    source = session.execute(
        sa.select(PersistedSourceRecord)
        .where(
            PersistedSourceRecord.organization_id == job.organization_id,
            PersistedSourceRecord.job_id == job.id,
            PersistedSourceRecord.investigation_id == investigation.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if source is None:
        session.rollback()
        return False

    expected_kind = {
        "geolocate_provenance": "geolocateProvenance",
        "damage_change": "damageChange",
    }[investigation.kind]
    expected_source = parent_source_record(source, content_sha256=source_sha256)
    if (
        result.source != expected_source
        or result.investigation.source_id != expected_source.source_id
        or result.investigation.investigation_id != str(investigation.id)
        or result.investigation.trace_id != str(parent_trace_id(investigation.id))
        or result.investigation.kind.value != expected_kind
        or result.investigation.connectivity_policy.value != "local"
    ):
        session.rollback()
        raise JobFailure(
            FailureCode.INVALID_SETTINGS,
            "investigation result does not match durable parent state",
        )

    # Retried attempts replace only unpublished derived rows.  Because the
    # Job success update and all inserts share this transaction, no reader can
    # ever observe a half-published result.
    session.execute(
        sa.delete(PersistedBeliefSnapshot).where(
            PersistedBeliefSnapshot.organization_id == job.organization_id,
            PersistedBeliefSnapshot.investigation_id == investigation.id,
        )
    )
    session.execute(
        sa.delete(PersistedInvestigationStep).where(
            PersistedInvestigationStep.organization_id == job.organization_id,
            PersistedInvestigationStep.investigation_id == investigation.id,
        )
    )
    session.execute(
        sa.delete(PersistedEvidenceItem).where(
            PersistedEvidenceItem.organization_id == job.organization_id,
            PersistedEvidenceItem.investigation_id == investigation.id,
        )
    )

    evidence_ids = {
        item.evidence_id: _stable_evidence_id(investigation.id, item.evidence_id)
        for item in result.evidence
    }
    for item in result.evidence:
        primitive = to_primitive(item)
        contributions = primitive.get("contributions", [])
        bbox = None
        if item.bbox_xywh is not None:
            x, y, width, height = item.bbox_xywh
            bbox = {"x": x, "y": y, "width": width, "height": height}
        session.add(
            PersistedEvidenceItem(
                id=evidence_ids[item.evidence_id],
                organization_id=job.organization_id,
                job_id=job.id,
                investigation_id=investigation.id,
                kind=_evidence_kind(item.kind.value, primitive.get("attributes", {})),
                observation={
                    "summary": item.observation,
                    "details": {
                        "externalEvidenceId": item.evidence_id,
                        "artifactId": item.artifact_id,
                        "attributes": primitive.get("attributes", {}),
                        "contributions": contributions,
                    },
                },
                frame_time_ms=item.frame_time_ms,
                bbox=bbox,
                polarity=_polarity(contributions),
                reliability=Decimal(str(item.reliability)),
                verification_state=_verification_state(item.verification_state.value),
                correlation_group=item.correlation_group,
                created_at=item.created_at,
            )
        )

    previous_entropy: Decimal | None = None
    for sequence, step in enumerate(result.steps, start=1):
        output_ids = [
            str(evidence_ids[item]) for item in step.output_evidence_ids if item in evidence_ids
        ]
        input_ids = [
            str(evidence_ids[item]) for item in step.input_evidence_ids if item in evidence_ids
        ]
        entropy_after = Decimal(str(step.entropy_after)) if step.entropy_after is not None else None
        session.add(
            PersistedInvestigationStep(
                organization_id=job.organization_id,
                job_id=job.id,
                investigation_id=investigation.id,
                sequence=sequence,
                kind=step.action.value[:40],
                tool=(step.tool_version or "investigation-core")[:120],
                state="completed" if step.status.value == "succeeded" else step.status.value,
                input_evidence_ids=input_ids,
                output_evidence_ids=output_ids,
                model_digest=_hex_digest(step.model_digest),
                prompt_digest=_hex_digest(step.prompt_digest),
                latency_ms=step.latency_ms,
                peak_memory_mb=(
                    step.peak_memory_bytes // (1024 * 1024)
                    if step.peak_memory_bytes is not None
                    else None
                ),
                cost_microunits=(
                    round(step.cost_units * 1_000_000) if step.cost_units is not None else None
                ),
                policy_decision=(
                    "not_required"
                    if step.egress_decision.value == "notApplicable"
                    else (
                        "rejected"
                        if step.egress_decision.value == "blocked"
                        else step.egress_decision.value
                    )
                ),
                entropy_before=(
                    Decimal(str(step.entropy_before))
                    if step.entropy_before is not None
                    else previous_entropy
                ),
                entropy_after=entropy_after,
                started_at=step.started_at,
                completed_at=step.completed_at,
            )
        )
        if entropy_after is not None:
            previous_entropy = entropy_after

    candidates = _candidate_rows(result)
    session.add(
        PersistedBeliefSnapshot(
            organization_id=job.organization_id,
            job_id=job.id,
            investigation_id=investigation.id,
            sequence=1,
            candidates=candidates,
            entropy=Decimal(str(result.belief.entropy)),
            abstained=result.belief.abstained,
            created_at=result.belief.created_at,
        )
    )

    source.media_sha256 = source_sha256
    primary_model = (
        result.investigation.model_provenance[0] if result.investigation.model_provenance else None
    )
    investigation.trace_id = uuid.UUID(result.investigation.trace_id)
    investigation.model_provenance = {
        "executedLocally": True,
        "modelId": primary_model.name if primary_model is not None else None,
        "modelDigest": _hex_digest(primary_model.digest) if primary_model is not None else None,
        "promptDigest": (
            _hex_digest(primary_model.prompt_digest) if primary_model is not None else None
        ),
    }
    resolved_runtime_provenance = {
        key: value for key, value in runtime_provenance.items() if value is not None
    }
    if primary_model is not None:
        resolved_runtime_provenance["runtimeVersion"] = primary_model.version
    investigation.runtime_provenance = resolved_runtime_provenance
    investigation.abstained = result.belief.abstained
    investigation.abstention_reason = (
        ",".join(result.belief.abstention_reasons)[:500] if result.belief.abstained else None
    )
    # The baseline softmax is intentionally exposed in BeliefSnapshot. It is
    # not validation-calibrated yet, so never copy it into the calibrated
    # confidence field (including on abstentions).
    investigation.calibrated_confidence = None
    investigation.status = "needs_review"
    investigation.updated_at = datetime.now(UTC)

    finalized_version = session.execute(
        sa.update(Job)
        .where(
            Job.id == job.id,
            Job.organization_id == job.organization_id,
            Job.workflow_kind == "video_investigation",
            Job.status == JobState.PROCESSING.value,
            Job.worker_id == worker_id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > sa.func.now(),
        )
        .values(
            status=JobState.READY_FOR_REVIEW.value,
            progress=100,
            stage="complete",
            completed_at=datetime.now(UTC),
            worker_id=None,
            lease_expires_at=None,
            version=Job.version + 1,
            updated_at=sa.func.now(),
        )
        .returning(Job.version)
    ).scalar_one_or_none()
    if finalized_version is None:
        session.rollback()
        return False

    occurred_at = datetime.now(UTC)
    event_id = uuid.uuid4()
    session.execute(
        pg_insert(JobEvent)
        .values(
            id=event_id,
            organization_id=job.organization_id,
            job_id=job.id,
            event_type="job.needs_review",
            job_version=finalized_version,
            payload={
                "id": str(event_id),
                "type": "job.needs_review",
                "jobId": str(job.id),
                "state": "needs_review",
                "workflowKind": "videoInvestigation",
                "investigationId": str(investigation.id),
                "occurredAt": occurred_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            },
            occurred_at=occurred_at,
            available_at=occurred_at,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "job_id", "event_type"])
    )
    session.commit()
    return True
