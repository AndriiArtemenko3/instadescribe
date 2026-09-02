from __future__ import annotations

import math

import pytest
from conftest import LANDMARK_A, LANDMARK_A_VARIANT, OPPOSITE_SCENE, UNRELATED_SCENE

from instadescribe_investigation_core import (
    InMemoryVisualCandidateRetriever,
    VisualCandidate,
    VisualCandidateRetriever,
    VisualRetrievalCandidate,
    cosine_similarity,
)


def candidate(candidate_id: str, embedding: tuple[float, ...]) -> VisualCandidate:
    return VisualCandidate(
        candidate_id, embedding, source="fixture", image_ref=f"{candidate_id}.jpg"
    )


def ids(results: tuple[VisualRetrievalCandidate, ...]) -> list[str]:
    return [item.candidate_id for item in results]


def test_exact_ranking_orders_by_cosine_descending() -> None:
    retriever = InMemoryVisualCandidateRetriever(
        [candidate("C", (0.0, 1.0)), candidate("A", (1.0, 0.0)), candidate("B", (0.8, 0.2))]
    )

    results = retriever.retrieve((1.0, 0.0), limit=3)

    assert ids(results) == ["A", "B", "C"]
    assert [item.rank for item in results] == [1, 2, 3]
    assert results[0].embedding_similarity == pytest.approx(1)
    assert results[1].embedding_similarity == pytest.approx(cosine_similarity((1, 0), (0.8, 0.2)))
    assert results[2].embedding_similarity == pytest.approx(0)
    assert (results[0].source, results[0].image_ref) == ("fixture", "A.jpg")
    assert isinstance(retriever, VisualCandidateRetriever)


def test_magnitude_independence_and_stable_tie_breaking() -> None:
    forward = InMemoryVisualCandidateRetriever(
        [candidate("big", (10.0, 10.0)), candidate("small", (1.0, 1.0))]
    ).retrieve((1.0, 1.0), limit=2)
    reversed_input = InMemoryVisualCandidateRetriever(
        [candidate("small", (1.0, 1.0)), candidate("big", (10.0, 10.0))]
    ).retrieve((1.0, 1.0), limit=2)

    assert ids(forward) == ids(reversed_input) == ["big", "small"]
    assert forward[0].embedding_similarity == pytest.approx(forward[1].embedding_similarity)


def test_orthogonal_and_opposite_candidates_keep_signed_scores() -> None:
    retriever = InMemoryVisualCandidateRetriever(
        [candidate("orthogonal", (0.0, 1.0)), candidate("opposite", (-1.0, 0.0))]
    )

    results = retriever.retrieve((1.0, 0.0), limit=2)

    assert ids(results) == ["orthogonal", "opposite"]
    assert results[0].embedding_similarity == pytest.approx(0)
    assert results[1].embedding_similarity == pytest.approx(-1)


def test_top_k_limits_and_empty_corpus() -> None:
    retriever = InMemoryVisualCandidateRetriever(
        [candidate("a", (1.0, 0.0)), candidate("b", (0.9, 0.1)), candidate("c", (0.0, 1.0))]
    )

    assert ids(retriever.retrieve((1.0, 0.0), limit=1)) == ["a"]
    assert ids(retriever.retrieve((1.0, 0.0), limit=2)) == ["a", "b"]
    assert ids(retriever.retrieve((1.0, 0.0), limit=10)) == ["a", "b", "c"]
    assert InMemoryVisualCandidateRetriever().retrieve((1.0, 0.0), limit=5) == ()
    assert InMemoryVisualCandidateRetriever().dimension is None
    assert len(retriever) == 3 and retriever.dimension == 2
    for bad_limit in (0, -1):
        with pytest.raises(ValueError, match="at least one"):
            retriever.retrieve((1.0, 0.0), limit=bad_limit)


def test_minimum_similarity_is_a_retrieval_filter() -> None:
    retriever = InMemoryVisualCandidateRetriever(
        [
            candidate("landmark", LANDMARK_A),
            candidate("variant", LANDMARK_A_VARIANT),
            candidate("street", UNRELATED_SCENE),
            candidate("negative", OPPOSITE_SCENE),
        ]
    )

    results = retriever.retrieve(LANDMARK_A, limit=10, minimum_similarity=0.7)

    assert ids(results) == ["landmark", "variant"]
    assert all(item.embedding_similarity >= 0.7 for item in results)
    with pytest.raises(ValueError, match="minimum_similarity"):
        retriever.retrieve(LANDMARK_A, limit=1, minimum_similarity=1.5)


def test_invalid_query_embeddings_fail_explicitly() -> None:
    retriever = InMemoryVisualCandidateRetriever([candidate("a", (1.0, 0.0))])

    with pytest.raises(ValueError, match="between 1 and"):
        retriever.retrieve((), limit=1)
    with pytest.raises(ValueError, match="positive L2 norm"):
        retriever.retrieve((0.0, 0.0), limit=1)
    with pytest.raises(ValueError, match="finite"):
        retriever.retrieve((math.nan, 1.0), limit=1)
    with pytest.raises(ValueError, match="does not match candidate dimension"):
        retriever.retrieve((1.0, 0.0, 0.0), limit=1)


def test_invalid_candidates_are_rejected_when_added() -> None:
    with pytest.raises(ValueError, match="positive L2 norm"):
        VisualCandidate("zero", (0.0, 0.0))
    with pytest.raises(ValueError, match="finite"):
        VisualCandidate("inf", (math.inf, 1.0))
    with pytest.raises(ValueError, match="between 1 and"):
        VisualCandidate("empty", ())
    with pytest.raises(ValueError, match="tuple"):
        VisualCandidate("list", [1.0, 0.0])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="candidate_id"):
        VisualCandidate(" ", (1.0, 0.0))
    with pytest.raises(ValueError, match="share one dimension"):
        InMemoryVisualCandidateRetriever(
            [candidate("a", (1.0, 0.0)), candidate("b", (1.0, 0.0, 0.0))]
        )
    with pytest.raises(ValueError, match="duplicate candidate_id"):
        InMemoryVisualCandidateRetriever([candidate("a", (1.0, 0.0)), candidate("a", (0.0, 1.0))])
    with pytest.raises(TypeError):
        InMemoryVisualCandidateRetriever([("a", (1.0, 0.0))])  # type: ignore[list-item]


def test_identical_scores_order_by_candidate_id() -> None:
    twins = [candidate(name, (0.5, 0.5)) for name in ("zeta", "alpha", "mid")]

    results = InMemoryVisualCandidateRetriever(twins).retrieve((1.0, 1.0), limit=3)

    assert ids(results) == ["alpha", "mid", "zeta"]
    assert len({item.embedding_similarity for item in results}) == 1


def test_retrieval_result_validates_its_fields() -> None:
    with pytest.raises(ValueError, match="between -1 and 1"):
        VisualRetrievalCandidate("a", 1.5, 1)
    with pytest.raises(ValueError, match="rank starts at one"):
        VisualRetrievalCandidate("a", 0.5, 0)
