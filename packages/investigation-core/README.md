# InstaDescribe Investigation Core

`instadescribe-investigation-core` is the open, local-only baseline behind
InstaDescribe's observable video-investigation work. It provides deterministic,
auditable building blocks rather than a hosted model or an automated source collector.

The package includes:

- typed investigation, source, evidence, step, belief and trace contracts;
- correlation-aware evidence fusion, temperature-scaled baseline posteriors, entropy,
  abstention and action-utility calculations;
- deterministic heuristic keyframe ranking with exact, pHash and temporal dedupe;
- SHA-256, optional image pHash and local `ffprobe` media inspection;
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
