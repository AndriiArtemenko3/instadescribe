"""The evidence_items.kind allowlist, including the visualMatch kind.

Two layers are covered. The dialect-independent tests exercise the constraint
expression exactly as shipped on the mapped table, so they run everywhere and
fail fast if the allowlist text drifts. The database tests prove the migration
actually widened the deployed schema and that a visualMatch row round-trips.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.domain.states import JobState
from app.models import EvidenceItem, Investigation, Job, Project
from conftest import requires_db
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

LEGACY_KINDS = (
    "keyframe",
    "visual",
    "ocr",
    "audio",
    "metadata",
    "web",
    "geospatial",
    "change",
)
NEW_KIND = "visualMatch"


def _kind_check_expression() -> str:
    """The shipped kind_valid expression, read off the mapped table.

    The metadata naming convention expands the declared ``kind_valid`` to
    ``ck_evidence_items_kind_valid``, so match on the suffix rather than
    restating the resolved name.
    """

    for constraint in EvidenceItem.__table__.constraints:
        if isinstance(constraint, sa.CheckConstraint) and constraint.name.endswith("kind_valid"):
            return str(constraint.sqltext)
    raise AssertionError("evidence_items has no kind_valid constraint")


# --- dialect-independent: the allowlist itself ----------------------------


def test_allowlist_adds_visual_match_without_dropping_any_existing_kind() -> None:
    expression = _kind_check_expression()
    for kind in (*LEGACY_KINDS, NEW_KIND):
        assert f"'{kind}'" in expression


@pytest.fixture()
def kind_gate():
    """A minimal table carrying only the real kind column and its CHECK.

    Isolating the constraint keeps this runnable without PostgreSQL: the rest
    of evidence_items depends on JSONB and jsonb_typeof, but the allowlist is
    plain SQL and is verified here exactly as the mapped table declares it.
    """

    metadata = sa.MetaData()
    table = sa.Table(
        "kind_gate",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.CheckConstraint(_kind_check_expression(), name="kind_valid"),
    )
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata.create_all(connection)
    yield engine, table
    engine.dispose()


@pytest.mark.parametrize("kind", [*LEGACY_KINDS, NEW_KIND])
def test_allowed_kinds_are_accepted(kind_gate, kind: str) -> None:
    engine, table = kind_gate
    with engine.begin() as connection:
        connection.execute(table.insert().values(kind=kind))
        stored = connection.execute(sa.select(table.c.kind)).scalar_one()
    assert stored == kind


@pytest.mark.parametrize(
    "kind",
    [
        "visualmatch",  # the allowlist is case-sensitive
        "visual_match",
        "visualMatches",
        "match",
        "",
    ],
)
def test_unknown_kinds_remain_rejected(kind_gate, kind: str) -> None:
    engine, table = kind_gate
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(table.insert().values(kind=kind))


# --- database: the migrated schema ----------------------------------------


def _investigation(session: Session) -> Investigation:
    project = Project(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        name=f"evidence-kind-{uuid.uuid4()}",
    )
    session.add(project)
    session.flush()
    job = Job(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        workflow_kind="video_investigation",
        project_id=project.id,
        pipeline_revision="evidence-kind-test",
        status=JobState.PROCESSING.value,
        settings={},
        created_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    investigation = Investigation(
        organization_id=job.organization_id,
        job_id=job.id,
        kind="geolocate_provenance",
        connectivity_policy="local",
        status="investigating",
        model_provenance={"executedLocally": True},
        runtime_provenance={},
    )
    session.add(investigation)
    session.flush()
    return investigation


def _evidence(investigation: Investigation, kind: str) -> EvidenceItem:
    return EvidenceItem(
        organization_id=investigation.organization_id,
        job_id=investigation.job_id,
        investigation_id=investigation.id,
        kind=kind,
        observation={
            "summary": "Geometric verification matched this frame to a reference image.",
            "details": {"featureMatchCount": 83, "ransacInlierCount": 71},
        },
        frame_time_ms=4_000,
        bbox=None,
        polarity="supports",
        reliability=1.0,
        verification_state="verified",
        correlation_group="visual:shot-01:capture-01",
    )


@requires_db
def test_visual_match_evidence_round_trips(db_engine) -> None:
    with Session(db_engine) as session:
        investigation = _investigation(session)
        item = _evidence(investigation, NEW_KIND)
        session.add(item)
        session.commit()
        evidence_id = item.id

    with Session(db_engine) as session:
        stored = session.get(EvidenceItem, evidence_id)
        assert stored is not None
        assert stored.kind == NEW_KIND
        assert stored.verification_state == "verified"
        assert stored.correlation_group == "visual:shot-01:capture-01"
        assert stored.observation["details"]["ransacInlierCount"] == 71


@requires_db
def test_migrated_schema_still_rejects_an_unknown_kind(db_engine) -> None:
    with Session(db_engine) as session:
        investigation = _investigation(session)
        session.add(_evidence(investigation, "visualmatch"))
        with pytest.raises(IntegrityError):
            session.flush()


@requires_db
def test_migrated_schema_keeps_accepting_the_existing_kinds(db_engine) -> None:
    with Session(db_engine) as session:
        investigation = _investigation(session)
        session.add_all(_evidence(investigation, kind) for kind in LEGACY_KINDS)
        session.commit()
        stored = session.execute(
            sa.select(EvidenceItem.kind).where(
                EvidenceItem.investigation_id == investigation.id,
            )
        ).scalars()
        assert set(stored) == set(LEGACY_KINDS)
