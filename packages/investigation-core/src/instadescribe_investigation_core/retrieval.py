"""Exact, in-memory visual candidate retrieval over embedding vectors.

Retrieval answers "which images might match?" and nothing more. For a query
vector ``q`` and candidates ``x_1 .. x_n`` it scores every candidate with the
package's own ``cosine_similarity``::

    s_i = (q . x_i) / (||q||_2 ||x_i||_2)

and returns the ``limit`` highest scores. The score is the raw cosine in
``[-1, 1]``: it is a similarity, not a calibrated probability, and a high value
is not investigation evidence. Verification (local feature matching, RANSAC) and
evidence creation are separate later stages; ``VisualMatch`` is reserved for the
output of that verification.

Complexity is ``O(N * D)`` per query for ``N`` candidates of dimension ``D``.
The loop is deliberately written as one cosine per candidate: it is the row-wise
reading of ``s = X q`` for unit-normalized rows, which is the natural vectorized
form if measured latency ever justifies it. An ANN index is intentionally absent
and should only appear once candidate scale or measured latency demands it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .models import JsonValue
from .vectors import cosine_similarity, validate_embedding

# Scores are compared after rounding so that mathematically equal cosines that
# differ only in the last floating-point bits (for example a vector and a
# scaled copy of it) tie deterministically on candidate_id. The stored
# embedding_similarity keeps the exact value.
_TIE_DIGITS = 12


def _require_identifier(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    """One image available for retrieval, identified independently of storage."""

    candidate_id: str
    embedding: tuple[float, ...]
    source: str | None = None
    image_ref: str | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", deepcopy(self.attributes))
        _require_identifier(self.candidate_id, "candidate_id")
        validate_embedding(self.embedding, "embedding")
        for name in ("source", "image_ref"):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)


@dataclass(frozen=True, slots=True)
class VisualRetrievalCandidate:
    """A ranked retrieval hit. Carries the exact cosine, never the vector."""

    candidate_id: str
    embedding_similarity: float
    rank: int
    source: str | None = None
    image_ref: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.candidate_id, "candidate_id")
        if not -1 <= self.embedding_similarity <= 1:
            raise ValueError("embedding_similarity must be between -1 and 1")
        if self.rank < 1:
            raise ValueError("rank starts at one")


@runtime_checkable
class VisualCandidateRetriever(Protocol):
    """Seam for exact in-memory search now and an indexed store later."""

    def retrieve(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
        minimum_similarity: float | None = None,
    ) -> tuple[VisualRetrievalCandidate, ...]: ...


class InMemoryVisualCandidateRetriever:
    """Exact cosine search over a validated, fixed-dimension candidate set."""

    def __init__(self, candidates: Iterable[VisualCandidate] = ()) -> None:
        by_id: dict[str, VisualCandidate] = {}
        dimension: int | None = None
        for candidate in candidates:
            if not isinstance(candidate, VisualCandidate):
                raise TypeError("candidates must be VisualCandidate instances")
            if candidate.candidate_id in by_id:
                raise ValueError(f"duplicate candidate_id {candidate.candidate_id!r}")
            if dimension is None:
                dimension = len(candidate.embedding)
            elif len(candidate.embedding) != dimension:
                raise ValueError("candidate embeddings must share one dimension")
            by_id[candidate.candidate_id] = candidate
        # Sorted by id so iteration order never depends on insertion order.
        self._candidates = tuple(by_id[key] for key in sorted(by_id))
        self._dimension = dimension

    def __len__(self) -> int:
        return len(self._candidates)

    @property
    def dimension(self) -> int | None:
        """Embedding width shared by every candidate; None while empty."""

        return self._dimension

    @property
    def candidates(self) -> tuple[VisualCandidate, ...]:
        return self._candidates

    def retrieve(
        self,
        query_embedding: Sequence[float],
        *,
        limit: int,
        minimum_similarity: float | None = None,
    ) -> tuple[VisualRetrievalCandidate, ...]:
        if limit < 1:
            raise ValueError("limit must be at least one")
        if minimum_similarity is not None and not -1 <= minimum_similarity <= 1:
            raise ValueError("minimum_similarity must be between -1 and 1")
        query = tuple(float(value) for value in query_embedding)
        validate_embedding(query, "query_embedding")
        if self._dimension is not None and len(query) != self._dimension:
            raise ValueError(
                f"query dimension {len(query)} does not match candidate dimension {self._dimension}"
            )
        scored = [
            (cosine_similarity(query, candidate.embedding), candidate)
            for candidate in self._candidates
        ]
        if minimum_similarity is not None:
            # Retrieval filter only: it bounds what is returned, it is not an
            # evidence or confidence threshold.
            scored = [item for item in scored if item[0] >= minimum_similarity]
        scored.sort(key=lambda item: (-round(item[0], _TIE_DIGITS), item[1].candidate_id))
        return tuple(
            VisualRetrievalCandidate(
                candidate_id=candidate.candidate_id,
                embedding_similarity=similarity,
                rank=rank,
                source=candidate.source,
                image_ref=candidate.image_ref,
            )
            for rank, (similarity, candidate) in enumerate(scored[:limit], start=1)
        )
