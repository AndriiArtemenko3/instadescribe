from __future__ import annotations

import math

import pytest

from instadescribe_investigation_core import cosine_similarity, dot_product, l2_norm, vectors


def test_dot_product_and_l2_norm_are_transparent() -> None:
    assert dot_product((1, 2, 3), (4, 5, 6)) == 32
    assert l2_norm((3, 4)) == 5
    assert l2_norm((0, 0)) == 0


def test_cosine_of_identical_vectors_is_one() -> None:
    assert cosine_similarity((1, 2, 3), (1, 2, 3)) == pytest.approx(1)


def test_cosine_ignores_magnitude() -> None:
    assert cosine_similarity((1, 2), (2, 4)) == pytest.approx(1)


def test_cosine_of_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity((1, 0), (0, 1)) == pytest.approx(0)


def test_cosine_of_opposite_vectors_is_minus_one() -> None:
    assert cosine_similarity((1, 0), (-1, 0)) == pytest.approx(-1)


def test_cosine_rejects_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="share one dimension"):
        cosine_similarity((1, 0), (1, 0, 0))


def test_cosine_rejects_zero_vector() -> None:
    with pytest.raises(ValueError, match="zero-norm"):
        cosine_similarity((0, 0), (1, 0))


def test_cosine_rejects_empty_and_non_finite_vectors() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        cosine_similarity((), ())
    with pytest.raises(ValueError, match="finite"):
        cosine_similarity((math.nan, 1), (1, 1))


def test_cosine_clamps_floating_point_overshoot(monkeypatch) -> None:
    monkeypatch.setattr(vectors, "dot_product", lambda a, b: 1.0000000002)
    assert cosine_similarity((1, 0), (1, 0)) == 1.0
    monkeypatch.setattr(vectors, "dot_product", lambda a, b: -1.0000000002)
    assert cosine_similarity((1, 0), (-1, 0)) == -1.0


def test_cosine_stays_within_range_for_near_parallel_vectors() -> None:
    a = tuple(0.1 * (index + 1) for index in range(64))
    b = tuple(value * 3.7 for value in a)
    assert -1 <= cosine_similarity(a, b) <= 1
