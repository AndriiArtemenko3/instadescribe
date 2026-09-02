"""Protocols keeping heavyweight or connected implementations outside the baseline."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from .media import MediaMetadata
from .models import (
    ActionCandidate,
    ActionDecision,
    BeliefSnapshot,
    EvidenceBatch,
    EvidenceItem,
    ModelProvenance,
    SourceRecord,
)


@runtime_checkable
class ObservationAdapter(Protocol):
    """A local VLM, OCR, ASR or deterministic fixture observer."""

    @property
    def network_access(self) -> bool: ...

    @property
    def provenance(self) -> ModelProvenance | None: ...

    def observe(
        self,
        media_path: Path,
        *,
        source: SourceRecord,
        media: MediaMetadata,
    ) -> EvidenceBatch: ...


@runtime_checkable
class ActionSelector(Protocol):
    """Select one bounded action from options; it never executes the action."""

    def choose_action(
        self,
        belief: BeliefSnapshot,
        actions: tuple[ActionCandidate, ...],
    ) -> ActionDecision: ...


@runtime_checkable
class VisualMatcher(Protocol):
    """Verify an already-retrieved candidate locally."""

    @property
    def provenance(self) -> ModelProvenance | None: ...

    @property
    def network_access(self) -> bool: ...

    def verify(self, query_path: Path, candidate_path: Path) -> EvidenceItem: ...


@runtime_checkable
class FrameEmbeddingProvider(Protocol):
    """Produce one embedding vector per frame image for semantic comparison.

    Implementations may wrap a local CLIP-style model or a fixture table; the
    selector only ever consumes the returned vector through cosine similarity.
    """

    @property
    def provenance(self) -> ModelProvenance | None: ...

    @property
    def network_access(self) -> bool: ...

    def embed_frame(self, frame_path: Path) -> tuple[float, ...]: ...
