"""Small, dependency-free vector primitives for embedding comparison.

Cosine similarity is implemented directly so the arithmetic stays inspectable:

    cos(a, b) = (a . b) / (||a||_2 * ||b||_2)

The dot product measures how far two vectors point the same way; dividing by both
L2 norms removes magnitude, so the result depends on direction only. Two frame
embeddings can differ in scale yet still point in nearly the same direction, which
is exactly the signal used for semantic keyframe novelty.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def _require_vector(vector: Sequence[float], name: str) -> None:
    if len(vector) == 0:
        raise ValueError(f"{name} must not be empty")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError(f"{name} must contain only finite values")


def _require_same_dimension(a: Sequence[float], b: Sequence[float]) -> None:
    _require_vector(a, "a")
    _require_vector(b, "b")
    if len(a) != len(b):
        raise ValueError(f"vectors must share one dimension, got {len(a)} and {len(b)}")


def dot_product(a: Sequence[float], b: Sequence[float]) -> float:
    """Return ``sum(a_i * b_i)`` for two vectors of the same dimension."""

    _require_same_dimension(a, b)
    return float(math.sumprod(a, b))


def l2_norm(vector: Sequence[float]) -> float:
    """Return the Euclidean length ``sqrt(sum(v_i ** 2))``."""

    _require_vector(vector, "vector")
    return math.hypot(*vector)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the cosine of the angle between ``a`` and ``b`` in ``[-1, 1]``.

    Raises ``ValueError`` for empty vectors, mismatched dimensions, non-finite values
    or a zero-norm vector, for which the cosine is undefined. The result is clamped to
    ``[-1, 1]`` purely to absorb floating-point overshoot such as ``1.0000000002``;
    mathematically the ratio never leaves that interval.
    """

    _require_same_dimension(a, b)
    norm_a = l2_norm(a)
    norm_b = l2_norm(b)
    if norm_a == 0 or norm_b == 0:
        raise ValueError("cosine similarity is undefined for a zero-norm vector")
    ratio = dot_product(a, b) / (norm_a * norm_b)
    return max(-1.0, min(1.0, ratio))
