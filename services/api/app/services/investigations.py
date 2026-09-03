"""Browser-wire serialization for persisted video-investigation records."""

from __future__ import annotations

from typing import Any

from app.models import (
    AnalystDecision,
    BeliefSnapshot,
    EvidenceItem,
    InvestigationStep,
)
from app.repositories.investigations import InvestigationRow
from app.schemas.investigations import (
    AnalystDecisionResponse,
    BeliefSnapshotResponse,
    EvidenceItemResponse,
    InvestigationDetail,
    InvestigationSourceResponse,
    InvestigationStepResponse,
    InvestigationSummary,
    KeyframeResponse,
)

_KIND_TO_WIRE = {
    "geolocate_provenance": "geolocateProvenance",
    "damage_change": "damageChange",
}
_POLICY_TO_WIRE = {
    "local": "local",
    "text_only": "textOnly",
    "approved_crops": "approvedCrops",
    "connected": "connected",
}
_STATUS_TO_WIRE = {
    "awaiting_upload": "awaitingUpload",
    "queued": "queued",
    "preprocessing": "preprocessing",
    "investigating": "investigating",
    "needs_review": "needsReview",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}
_POLICY_DECISION_TO_WIRE = {
    "pending": "pending",
    "approved": "approved",
    "rejected": "rejected",
    "not_required": "notRequired",
}
_LEGAL_BASIS_TO_WIRE = {
    "public_domain": "publicDomain",
    "licensed": "licensed",
    "consent": "consent",
    "analyst_authorized": "analystAuthorized",
}
_REDISTRIBUTION_TO_WIRE = {
    "prohibited": "prohibited",
    "metadata_only": "metadataOnly",
    "permitted": "permitted",
}


def _dump(model) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def _without_none(value: dict[str, Any]) -> dict[str, Any]:
    """Omit absent members of strict nested wire objects.

    Top-level nullable fields remain explicit; only optional members inside
    provenance, hypothesis and observation objects are omitted so the Browser
    contract is stable for strict TypeScript validators.
    """
    return {key: item for key, item in value.items() if item is not None}


def summary_body(row: InvestigationRow) -> dict[str, Any]:
    investigation = row.investigation
    return _dump(
        InvestigationSummary(
            investigationId=investigation.id,
            projectId=row.project.id,
            jobId=row.job.id,
            name=row.project.name,
            kind=_KIND_TO_WIRE[investigation.kind],
            connectivityPolicy=_POLICY_TO_WIRE[investigation.connectivity_policy],
            status=_STATUS_TO_WIRE[investigation.status],
            abstained=investigation.abstained,
            calibratedConfidence=(
                float(investigation.calibrated_confidence)
                if investigation.calibrated_confidence is not None
                else None
            ),
            createdAt=investigation.created_at,
            updatedAt=investigation.updated_at,
        )
    )


def detail_body(row: InvestigationRow) -> dict[str, Any]:
    investigation = row.investigation
    body = _dump(
        InvestigationDetail(
            **summary_body(row),
            traceId=investigation.trace_id,
            modelProvenance=investigation.model_provenance,
            runtimeProvenance=investigation.runtime_provenance,
            finalHypothesis=investigation.final_hypothesis,
            abstentionReason=investigation.abstention_reason,
            completedAt=investigation.completed_at,
        )
    )
    body["modelProvenance"] = _without_none(body["modelProvenance"])
    body["runtimeProvenance"] = _without_none(body["runtimeProvenance"])
    if body["finalHypothesis"] is not None:
        body["finalHypothesis"] = _without_none(body["finalHypothesis"])
    return body


def evidence_body(item: EvidenceItem) -> dict[str, Any]:
    body = _dump(
        EvidenceItemResponse(
            evidenceId=item.id,
            kind=item.kind,
            observation={"summary": item.observation.get("summary")},
            frameTimeMs=item.frame_time_ms,
            bbox=item.bbox,
            polarity=item.polarity,
            reliability=float(item.reliability),
            verificationState=item.verification_state,
            correlationGroup=item.correlation_group,
            createdAt=item.created_at,
        )
    )
    return body


def keyframe_body(item: EvidenceItem) -> dict[str, Any]:
    if item.kind != "keyframe" or item.frame_time_ms is None:
        raise ValueError("keyframe evidence requires a frame time")
    body = _dump(
        KeyframeResponse(
            evidenceId=item.id,
            frameTimeMs=item.frame_time_ms,
            observation={"summary": item.observation.get("summary")},
            bbox=item.bbox,
            createdAt=item.created_at,
        )
    )
    return body


def source_body(source) -> dict[str, Any]:
    body = _dump(
        InvestigationSourceResponse(
            sourceRecordId=source.id,
            publisherUrl=source.publisher_url,
            publishedAt=source.published_at,
            collectedAt=source.collected_at,
            legalBasis=_LEGAL_BASIS_TO_WIRE[source.legal_basis],
            license=source.license_name,
            mediaSha256=source.media_sha256,
            redistributionPolicy=_REDISTRIBUTION_TO_WIRE[source.redistribution_policy],
            retentionDays=source.retention_days,
            purgeAfter=source.purge_after,
        )
    )
    return _without_none(body)


def step_body(step: InvestigationStep) -> dict[str, Any]:
    return _dump(
        InvestigationStepResponse(
            stepId=step.id,
            sequence=step.sequence,
            kind=step.kind,
            tool=step.tool,
            state=step.state,
            inputEvidenceIds=step.input_evidence_ids,
            outputEvidenceIds=step.output_evidence_ids,
            modelDigest=step.model_digest,
            promptDigest=step.prompt_digest,
            latencyMs=step.latency_ms,
            peakMemoryMb=step.peak_memory_mb,
            costMicrounits=step.cost_microunits,
            policyDecision={
                "decision": _POLICY_DECISION_TO_WIRE[step.policy_decision],
                "decidedByPrincipalId": step.policy_decided_by_principal_id,
                "decidedAt": step.policy_decided_at,
            },
            entropyBefore=(float(step.entropy_before) if step.entropy_before is not None else None),
            entropyAfter=(float(step.entropy_after) if step.entropy_after is not None else None),
            startedAt=step.started_at,
            completedAt=step.completed_at,
        )
    )


def belief_body(snapshot: BeliefSnapshot) -> dict[str, Any]:
    body = _dump(
        BeliefSnapshotResponse(
            beliefSnapshotId=snapshot.id,
            sequence=snapshot.sequence,
            candidates=snapshot.candidates,
            entropy=float(snapshot.entropy),
            abstained=snapshot.abstained,
            createdAt=snapshot.created_at,
        )
    )
    body["candidates"] = [_without_none(candidate) for candidate in body["candidates"]]
    return body


def decision_body(decision: AnalystDecision) -> dict[str, Any]:
    body = _dump(
        AnalystDecisionResponse(
            decisionId=decision.id,
            status="final",
            evidenceDecisions=decision.evidence_decisions,
            finalHypothesis=decision.final_hypothesis,
            abstained=decision.abstained,
            abstentionReason=decision.abstention_reason,
            notes=decision.notes,
            decidedByPrincipalId=decision.decided_by_principal_id,
            createdAt=decision.created_at,
        )
    )
    if body["finalHypothesis"] is not None:
        body["finalHypothesis"] = _without_none(body["finalHypothesis"])
    return body
