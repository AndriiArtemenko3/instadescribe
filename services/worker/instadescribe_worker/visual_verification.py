"""SIFT + RANSAC geometric verification of retrieved visual candidates.

Retrieval (cosine over CLIP embeddings) answers "which images might match?";
this module answers "do the query and candidate actually share geometrically
consistent local structure?". For matched keypoints ``p_i`` (query) and
``p'_i`` (candidate) in homogeneous coordinates, verification estimates a
homography ``H`` with ``p' ~ H p`` and asks RANSAC how many correspondences
reproject within a threshold::

    inlier_i = 1[ d(H p_i, p'_i) < tau ]

The decision uses only geometric quantities — good-match count, RANSAC inlier
count and inlier ratio. The retrieval cosine is carried through as provenance
and can never flip ``verified``. Scores here are counts and ratios, not
calibrated probabilities, and no combined "confidence" is synthesized.

OpenCV stays behind this module: the core protocol sees ``pathlib.Path`` in
and ``VisualMatch`` out, never ``cv2`` types. Heavy imports are lazy so
importing the module stays cheap for workers that never verify. Infrastructure
failures (missing or undecodable images) raise ``JobFailure``; only a
verification that actually ran may return ``verified=False``, with a named
``rejection_reason``.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from instadescribe_investigation_core import ModelProvenance, VisualMatch

from instadescribe_worker.failures import FailureCode, JobFailure

# A homography has eight degrees of freedom; each correspondence supplies two
# equations, so four point pairs are the mathematical minimum. The configured
# verification minimum below is deliberately stricter than this floor.
HOMOGRAPHY_MINIMUM_CORRESPONDENCES = 4

MATCHER_NAME = "sift-ransac-homography"
RUNTIME_NAME = "opencv-cpu"

REASON_INSUFFICIENT_FEATURES = "insufficientFeatures"
REASON_INSUFFICIENT_MATCHES = "insufficientMatches"
REASON_BELOW_MATCH_MINIMUM = "belowConfiguredMatchMinimum"
REASON_HOMOGRAPHY_NOT_FOUND = "homographyNotFound"
REASON_INSUFFICIENT_INLIERS = "insufficientInliers"
REASON_LOW_INLIER_RATIO = "lowInlierRatio"


@dataclass(frozen=True, slots=True)
class VerificationConfig:
    """Decision-critical thresholds for geometric verification.

    ``descriptor_ratio_threshold`` is Lowe's ratio test bound (nearest over
    second-nearest descriptor distance). The three minimums below are the
    explicit verification rule; they are inspectable defaults, not
    production-calibrated values.
    """

    descriptor_ratio_threshold: float = 0.75
    minimum_feature_matches: int = 10
    ransac_reprojection_threshold: float = 5.0
    minimum_ransac_inliers: int = 8
    minimum_ransac_inlier_ratio: float = 0.5
    rng_seed: int = 7
    max_features: int = 2000
    feature_cache_size: int = 8

    def __post_init__(self) -> None:
        if not 0 < self.descriptor_ratio_threshold < 1:
            raise ValueError("descriptor_ratio_threshold must be strictly between 0 and 1")
        if self.minimum_feature_matches < HOMOGRAPHY_MINIMUM_CORRESPONDENCES:
            raise ValueError(
                "minimum_feature_matches must be at least the homography minimum of "
                f"{HOMOGRAPHY_MINIMUM_CORRESPONDENCES}"
            )
        if self.ransac_reprojection_threshold <= 0:
            raise ValueError("ransac_reprojection_threshold must be positive")
        if self.minimum_ransac_inliers < HOMOGRAPHY_MINIMUM_CORRESPONDENCES:
            raise ValueError(
                "minimum_ransac_inliers must be at least the homography minimum of "
                f"{HOMOGRAPHY_MINIMUM_CORRESPONDENCES}"
            )
        if not 0 < self.minimum_ransac_inlier_ratio <= 1:
            raise ValueError("minimum_ransac_inlier_ratio must be in (0, 1]")
        if self.rng_seed < 0:
            raise ValueError("rng_seed must not be negative")
        if self.max_features < HOMOGRAPHY_MINIMUM_CORRESPONDENCES:
            raise ValueError("max_features is too small to ever verify")
        if self.feature_cache_size < 1:
            raise ValueError("feature_cache_size must be at least one")

    def digest_material(self) -> str:
        """The decision-critical values, serialized for provenance digesting."""

        return (
            f"ratio={self.descriptor_ratio_threshold};matches={self.minimum_feature_matches};"
            f"reproj={self.ransac_reprojection_threshold};inliers={self.minimum_ransac_inliers};"
            f"inlier_ratio={self.minimum_ransac_inlier_ratio};seed={self.rng_seed};"
            f"max_features={self.max_features}"
        )


@dataclass(frozen=True, slots=True)
class VerificationDiagnostics:
    """Per-pair diagnostics the benchmark inspects; never investigation output."""

    query_keypoint_count: int
    candidate_keypoint_count: int
    raw_match_count: int
    good_match_count: int
    ransac_inlier_count: int
    feature_seconds: float
    matching_seconds: float
    ransac_seconds: float

    @property
    def total_seconds(self) -> float:
        return self.feature_seconds + self.matching_seconds + self.ransac_seconds


class SiftRansacVisualMatcher:
    """``VisualMatcher`` backed by OpenCV SIFT descriptors and RANSAC homography.

    Local features per image are cached (keyed by resolved path, size and
    mtime) so verifying one query against Top-K candidates extracts the query
    features exactly once. The cache is bounded and process-local; no
    persistent feature storage exists.
    """

    def __init__(self, config: VerificationConfig | None = None) -> None:
        self._config = config or VerificationConfig()
        self._features: OrderedDict[tuple[str, int, int], tuple[Any, Any]] = OrderedDict()
        self._provenance: ModelProvenance | None = None
        self._sift: Any = None

    @property
    def config(self) -> VerificationConfig:
        return self._config

    @property
    def network_access(self) -> bool:
        return False

    @property
    def provenance(self) -> ModelProvenance | None:
        """Matcher identity; reading it imports OpenCV so the version is real."""

        if self._provenance is None:
            cv2 = self._cv2()
            digest = hashlib.sha256(self._config.digest_material().encode()).hexdigest()
            self._provenance = ModelProvenance(
                name=MATCHER_NAME,
                version=str(cv2.__version__),
                digest=digest,
                runtime=RUNTIME_NAME,
            )
        return self._provenance

    @staticmethod
    def _cv2() -> Any:
        # Lazy on purpose: importing this module must stay cheap and OpenCV
        # must only load when a pair is actually verified.
        import cv2

        return cv2

    def _detector(self) -> Any:
        if self._sift is None:
            self._sift = self._cv2().SIFT_create(nfeatures=self._config.max_features)
        return self._sift

    def _decode_grayscale(self, path: Path, label: str) -> Any:
        """Decode an image for feature extraction; failures are loud, never silent."""

        if not path.is_file():
            raise JobFailure(FailureCode.INVALID_MEDIA, f"{label} image file is unavailable")
        cv2 = self._cv2()
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise JobFailure(FailureCode.INVALID_MEDIA, f"{label} image could not be decoded")
        if image.size == 0 or image.shape[0] < 1 or image.shape[1] < 1:
            raise JobFailure(FailureCode.INVALID_MEDIA, f"{label} image has no pixels")
        return image

    def _extract_features(self, path: Path, label: str) -> tuple[Any, Any]:
        """Keypoint coordinates (float32 [N, 2]) and descriptors, cached per file."""

        import numpy as np

        resolved = path.resolve()
        stat = None
        if resolved.is_file():
            stat = resolved.stat()
            key = (str(resolved), stat.st_size, stat.st_mtime_ns)
            cached = self._features.get(key)
            if cached is not None:
                self._features.move_to_end(key)
                return cached
        image = self._decode_grayscale(resolved, label)
        keypoints, descriptors = self._detector().detectAndCompute(image, None)
        points = np.asarray([keypoint.pt for keypoint in keypoints], dtype=np.float32).reshape(
            -1, 2
        )
        if descriptors is None:
            descriptors = np.empty((0, 128), dtype=np.float32)
        entry = (points, descriptors)
        if stat is not None:
            self._features[(str(resolved), stat.st_size, stat.st_mtime_ns)] = entry
            while len(self._features) > self._config.feature_cache_size:
                self._features.popitem(last=False)
        return entry

    def _ratio_filtered_matches(
        self, query_desc: Any, candidate_desc: Any
    ) -> tuple[int, list[Any]]:
        """KNN match (k=2) then Lowe's ratio test; returns (raw pairs, survivors)."""

        cv2 = self._cv2()
        matcher = cv2.BFMatcher(cv2.NORM_L2)
        pairs = matcher.knnMatch(query_desc, candidate_desc, k=2)
        good = []
        for pair in pairs:
            if len(pair) < 2:
                continue  # no second neighbour: ambiguity is undecidable, reject
            best, second = pair
            if second.distance > 0 and best.distance / second.distance < (
                self._config.descriptor_ratio_threshold
            ):
                good.append(best)
        return len(pairs), good

    def verify(
        self,
        query_path: Path,
        candidate_path: Path,
        *,
        embedding_similarity: float,
        query_artifact_id: str,
        candidate_artifact_id: str,
    ) -> VisualMatch:
        match, _ = self.verify_detailed(
            query_path,
            candidate_path,
            embedding_similarity=embedding_similarity,
            query_artifact_id=query_artifact_id,
            candidate_artifact_id=candidate_artifact_id,
        )
        return match

    def verify_detailed(
        self,
        query_path: Path,
        candidate_path: Path,
        *,
        embedding_similarity: float,
        query_artifact_id: str,
        candidate_artifact_id: str,
    ) -> tuple[VisualMatch, VerificationDiagnostics]:
        """Verification plus benchmark diagnostics (counts and stage timings)."""

        import numpy as np

        config = self._config
        match_id = f"visual-match:{query_artifact_id}:{candidate_artifact_id}"

        def unverified(
            reason: str,
            *,
            good: int = 0,
            inliers: int = 0,
            error: float | None = None,
        ) -> VisualMatch:
            return VisualMatch(
                match_id=match_id,
                query_artifact_id=query_artifact_id,
                candidate_artifact_id=candidate_artifact_id,
                embedding_similarity=embedding_similarity,
                feature_matches=good,
                ransac_inliers=inliers,
                reprojection_error=error,
                verified=False,
                rejection_reason=reason,
            )

        started = time.perf_counter()
        query_points, query_desc = self._extract_features(query_path, "query")
        candidate_points, candidate_desc = self._extract_features(candidate_path, "candidate")
        feature_seconds = time.perf_counter() - started

        def diagnostics(
            raw: int, good: int, inliers: int, matching: float, ransac: float
        ) -> VerificationDiagnostics:
            return VerificationDiagnostics(
                query_keypoint_count=len(query_points),
                candidate_keypoint_count=len(candidate_points),
                raw_match_count=raw,
                good_match_count=good,
                ransac_inlier_count=inliers,
                feature_seconds=feature_seconds,
                matching_seconds=matching,
                ransac_seconds=ransac,
            )

        # KNN with k=2 needs two candidate descriptors; below the homography
        # minimum on either side no geometry is estimable at all.
        if len(query_desc) < HOMOGRAPHY_MINIMUM_CORRESPONDENCES or len(candidate_desc) < max(
            HOMOGRAPHY_MINIMUM_CORRESPONDENCES, 2
        ):
            return (
                unverified(REASON_INSUFFICIENT_FEATURES),
                diagnostics(0, 0, 0, 0.0, 0.0),
            )

        started = time.perf_counter()
        raw_count, good_matches = self._ratio_filtered_matches(query_desc, candidate_desc)
        matching_seconds = time.perf_counter() - started
        good = len(good_matches)

        if good < HOMOGRAPHY_MINIMUM_CORRESPONDENCES:
            # Mathematical minimum: a homography is not even estimable.
            return (
                unverified(REASON_INSUFFICIENT_MATCHES, good=good),
                diagnostics(raw_count, good, 0, matching_seconds, 0.0),
            )

        cv2 = self._cv2()
        source = np.float32([query_points[match.queryIdx] for match in good_matches]).reshape(
            -1, 1, 2
        )
        destination = np.float32(
            [candidate_points[match.trainIdx] for match in good_matches]
        ).reshape(-1, 1, 2)

        started = time.perf_counter()
        cv2.setRNGSeed(config.rng_seed)  # reproducible RANSAC sampling per call
        homography, inlier_mask = cv2.findHomography(
            source, destination, cv2.RANSAC, config.ransac_reprojection_threshold
        )
        ransac_seconds = time.perf_counter() - started

        if homography is None or inlier_mask is None:
            return (
                unverified(REASON_HOMOGRAPHY_NOT_FOUND, good=good),
                diagnostics(raw_count, good, 0, matching_seconds, ransac_seconds),
            )

        mask = inlier_mask.ravel().astype(bool)
        inliers = int(mask.sum())
        reprojection_error: float | None = None
        if inliers > 0:
            projected = cv2.perspectiveTransform(source[mask], homography)
            residuals = np.linalg.norm(
                projected.reshape(-1, 2) - destination[mask].reshape(-1, 2), axis=1
            )
            reprojection_error = float(residuals.mean())

        inlier_ratio = inliers / good
        verified = (
            good >= config.minimum_feature_matches
            and inliers >= config.minimum_ransac_inliers
            and inlier_ratio >= config.minimum_ransac_inlier_ratio
        )
        if verified:
            match = VisualMatch(
                match_id=match_id,
                query_artifact_id=query_artifact_id,
                candidate_artifact_id=candidate_artifact_id,
                embedding_similarity=embedding_similarity,
                feature_matches=good,
                ransac_inliers=inliers,
                reprojection_error=reprojection_error,
                verified=True,
            )
        else:
            if good < config.minimum_feature_matches:
                reason = REASON_BELOW_MATCH_MINIMUM
            elif inliers < config.minimum_ransac_inliers:
                reason = REASON_INSUFFICIENT_INLIERS
            else:
                reason = REASON_LOW_INLIER_RATIO
            match = unverified(reason, good=good, inliers=inliers, error=reprojection_error)
        return match, diagnostics(raw_count, good, inliers, matching_seconds, ransac_seconds)
