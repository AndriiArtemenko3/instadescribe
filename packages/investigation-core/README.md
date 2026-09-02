# InstaDescribe Investigation Core

`instadescribe-investigation-core` is the open, local-only baseline behind
InstaDescribe's observable video-investigation work. It provides deterministic,
auditable building blocks rather than a hosted model or an automated source collector.

The package includes:

- typed investigation, source, evidence, step, belief and trace contracts;
- correlation-aware evidence fusion, temperature-scaled baseline posteriors, entropy,
  abstention and action-utility calculations;
- deterministic heuristic keyframe ranking with exact, pHash and temporal dedupe, plus
  optional embedding-based semantic novelty;
- SHA-256, optional image pHash and local `ffprobe` media inspection;
- exact in-memory visual candidate retrieval over embedding vectors (top-K by cosine);
- protocols for local observation, action-selection and visual-matching adapters;
- an offline deterministic runner and atomic JSONL trace export;
- strict canonical-JSON IPC encoding/decoding without `pickle`;
- small evaluation metrics for ranking, geolocation and calibration.

It deliberately does **not** include web collectors, a production visual index,
proprietary ranking/fusion logic, model weights, or application/backend imports.

## Install for development

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check src tests
```

The runtime has no required third-party dependency. Pillow is optional and is used
only when computing a perceptual image hash. `ffprobe`, when available on `PATH`, is
given a resolved local file path and invoked without a shell.

## Perceptual vs semantic keyframe redundancy

Keyframe selection detects redundant frames in two independent layers:

- **Perceptual redundancy**: `pHash + Hamming distance`. A DCT perceptual hash
  summarises the pixel layout; a small Hamming distance between two hashes means the
  frames are visually near-identical.
- **Semantic redundancy**: `embedding vectors + cosine similarity`. A
  `FrameEmbeddingProvider` (local model or fixture) attaches an embedding to each
  `FrameDescriptor`; the selector compares candidates with the keyframes already chosen.

Cosine similarity is implemented in `vectors.py` without external dependencies:

$$
\cos(\theta) = \frac{a \cdot b}{\|a\|_2 \, \|b\|_2}
$$

The dot product measures vector alignment, while dividing by the vectors' L2 norms
removes magnitude, making the comparison primarily about direction. Two frames can
have different pixels but embeddings pointing in nearly the same direction, indicating
that they contain similar semantic information.

For a candidate $x$ and the selected set $K$:

$$
S_{\max}(x) = \max_{s \in K} \cos(x, s), \qquad
N_{\text{semantic}}(x) = 1 - \max(0, S_{\max}(x))
$$

Conventions: an empty $K$ yields $S_{\max} = \text{None}$ and novelty $1$; negative
cosine (embeddings pointing away from each other) saturates novelty at $1$ rather than
exceeding it; the raw signed $S_{\max}$ is exposed as `Keyframe.embedding_similarity_max`
alongside `Keyframe.semantic_novelty`. Cosine values are clamped to $[-1, 1]$ only to
absorb floating-point overshoot.

Semantic novelty enters the explicit weighted score through
`SelectionWeights.semantic_novelty` and can additionally reject candidates through
`KeyframeSelectionConfig.semantic_similarity_threshold` (`FrameRejectionReason.SEMANTIC_DUPLICATE`).
Both default to off, embeddings default to `None`, and no model, network access or
vector database is required; without embeddings the selector behaves exactly as before.

## Visual candidate retrieval

`retrieval.py` answers "which images might match?" with exact cosine search and
nothing more. A query vector $q$ is scored against every candidate $x_i$:

$$
s_i = \frac{q \cdot x_i}{\|q\|_2 \, \|x_i\|_2}
$$

and the `limit` highest scores are returned as `VisualRetrievalCandidate` values
(candidate id, exact signed cosine in $[-1, 1]$, rank, identifying metadata; never
the vector). Ties are broken by candidate id after rounding the score to twelve
decimals, so input order never decides the output. Candidates are validated when
they enter `InMemoryVisualCandidateRetriever` (finite, non-empty, positive norm, one
shared dimension); the query is validated on every call. Any dimension works; the
CLIP provider in the worker happens to produce 512.

Cost is $O(ND)$ per query for $N$ candidates of width $D$. The per-candidate loop
is the row-wise form of $s = Xq$ over unit-normalized rows, which is the natural
vectorized implementation if measured latency ever needs it. There is no ANN index
or vector database; add one only when candidate scale or measured latency requires
it. Retrieval output is not evidence: `VisualMatch` is reserved for the later
geometric-verification stage, which takes the query image and the candidate's
`image_ref`, performs local feature matching and RANSAC, and copies
`embedding_similarity` into the verified result.

## Minimal offline run

```python
from pathlib import Path

from instadescribe_investigation_core import (
    BeliefConfig,
    CandidatePrior,
    ConnectivityPolicy,
    DeterministicLocalRunner,
    EvidenceContribution,
    EvidenceItem,
    InvestigationKind,
    LocalRunExpectation,
    SourceRecord,
    StaticObservationAdapter,
    VerificationState,
    local_run_result_from_primitive,
    local_run_result_to_primitive,
)

evidence = EvidenceItem(
    evidence_id="road-sign",
    observation="Road sign uses the Polish place name Krakow.",
    source_id="source-1",
    artifact_id="frame-1",
    correlation_group="sign-frame-1",
    reliability=0.9,
    verification_state=VerificationState.OBSERVED,
    contributions=(EvidenceContribution("pl", 0.9),),
)

candidates = (
    CandidatePrior("pl", "Poland", 0.5),
    CandidatePrior("sk", "Slovakia", 0.5),
)
runner = DeterministicLocalRunner(
    observer=StaticObservationAdapter((evidence,)),
    candidates=candidates,
)

result = runner.run(
    Path("public-video.mp4"),
    connectivity_policy=ConnectivityPolicy.LOCAL,
    license_basis="CC-BY-4.0",
)
result.export_trace(Path("trace.jsonl"))
```

The fake runner refuses every non-local connectivity policy. Connected retrieval is
an application concern and must pass through an explicit, audited egress boundary.
For isolated child processes, use `local_run_result_to_primitive(result)`, JSON-encode
that object, and construct the expectation from the durable parent record **before**
launching the child:

```python
# Load this complete record from parent-owned durable state. It includes the
# immutable media digest, collection/publication times, license/consent basis,
# publisher/URL, redistribution policy and retention policy.
source: SourceRecord = parent_job.source_record

expected = LocalRunExpectation(
    source=source,
    investigation_id=parent_job.investigation_id,
    trace_id=parent_job.trace_id,
    candidates=candidates,
    model_provenance=parent_job.approved_model_provenance,
    belief_config=BeliefConfig(),
    kind=InvestigationKind.GEOLOCATE_PROVENANCE,
    connectivity_policy=ConnectivityPolicy.LOCAL,
)

# The parent sends these immutable request fields to the isolated child. The
# child's runner uses them instead of inventing identities that the parent can
# only learn from an untrusted response.
result = runner.run(
    Path(parent_job.local_media_path),
    source=expected.source,
    investigation_id=expected.investigation_id,
    trace_id=expected.trace_id,
    connectivity_policy=expected.connectivity_policy,
    kind=expected.kind,
)
payload = local_run_result_to_primitive(result)
decoded = local_run_result_from_primitive(payload, expected=expected)
```

Never derive `expected` from `payload`: mutually consistent child-controlled IDs are
not authoritative. The complete `SourceRecord` is compared exactly, so a child cannot
rewrite license, consent, publisher, URL, timestamps, redistribution or retention
metadata. The parent must also cap the encoded response size before calling
`json.loads`; the decoder then bounds the materialized structure, recomputes the
posterior and fails on unknown fields, inconsistent evidence/trace content, or any
source, job, policy, model or identity mismatch.

## License

Copyright 2026 Andrii Artemenko. Licensed under the Apache License, Version 2.0.
The repository's root BUSL-1.1 license does not apply inside this directory.
