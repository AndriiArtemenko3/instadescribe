"""The retrieval-to-verification bridge.

Retrieval answers "which images might match?" (``VisualRetrievalCandidate``);
verification answers "do the query and candidate share geometrically
consistent local structure?" (``VisualMatch``). This module only connects the
two stages: it hands each retrieved candidate's ``image_ref`` and retrieval
cosine to a ``VisualMatcher`` and collects the results. It knows nothing
about how retrieval ranked candidates and nothing about how the matcher
verifies — and it deliberately verifies EVERY requested candidate rather
than stopping at the first success, so evaluation can observe false
positives and multiple verified candidates.

Evidence creation and belief updates are later stages and are absent here.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .adapters import VisualMatcher
from .models import VisualMatch
from .retrieval import VisualRetrievalCandidate

__all__ = ["verify_retrieval_candidates"]


def verify_retrieval_candidates(
    query_path: Path,
    query_artifact_id: str,
    candidates: Sequence[VisualRetrievalCandidate],
    matcher: VisualMatcher,
    *,
    limit: int | None = None,
) -> tuple[VisualMatch, ...]:
    """Verify the first ``limit`` retrieved candidates against the query image.

    Every candidate must carry an ``image_ref`` (the path retrieval stored for
    it); a candidate without one cannot be verified and raising is more honest
    than silently skipping it. The matcher receives the candidate's retrieval
    cosine so ``VisualMatch.embedding_similarity`` preserves it verbatim.
    """

    if limit is not None and limit < 1:
        raise ValueError("limit must be at least one when provided")
    selected = candidates if limit is None else candidates[:limit]
    matches: list[VisualMatch] = []
    for candidate in selected:
        if candidate.image_ref is None:
            raise ValueError(
                f"candidate {candidate.candidate_id!r} has no image_ref; "
                "verification needs the stored image path"
            )
        matches.append(
            matcher.verify(
                query_path,
                Path(candidate.image_ref),
                embedding_similarity=candidate.embedding_similarity,
                query_artifact_id=query_artifact_id,
                candidate_artifact_id=candidate.candidate_id,
            )
        )
    return tuple(matches)
