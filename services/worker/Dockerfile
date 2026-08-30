# syntax=docker/dockerfile:1
# InstaDescribe worker image — target platform is linux/amd64 (ADR-0004/G0).
# Context must be the repository root. Two named targets on one proven base:
#
#   g0smoke     retained G0 pipeline probe (Sintel fixture + g0_smoke.py)
#     docker buildx build --platform linux/amd64 --target g0smoke \
#       -f services/worker/Dockerfile .
#
#   production  G5 queue worker: strict consumer + unchanged pipeline +
#               shared queue contract + canonical persistence/domain code.
#               NO fixture, smoke script, tests, .env, media or handoffs.
#     docker buildx build --platform linux/amd64 --target production \
#       -f services/worker/Dockerfile .

# Base image pinned by manifest-list digest (python:3.12-slim resolved
# 2026-08-07); buildx selects the linux/amd64 manifest from the index.
FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b AS base

# ffmpeg + ffprobe: media validation gate, frame extraction, posters, audio.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user with a writable home for model caches.
RUN useradd --create-home --uid 10001 worker

# Deterministic dependency set compiled by `uv pip compile` (CPU-only torch;
# index URLs are embedded in the file). Torch is constrained to the audited
# 2.13 minor; TorchAudio 2.11 uses its stable ABI — see requirements.in.
COPY services/worker/requirements.txt /tmp/worker-requirements.txt
RUN pip install --no-cache-dir -r /tmp/worker-requirements.txt

# Dependency-compatibility gate: resolver consistency, explicit imports of
# every runtime stack, and one EXERCISED audio operation (a real torchaudio
# resample) — imports alone would not catch a torch/torchaudio ABI mismatch.
RUN pip check && python - <<'PYEOF'
import boto3, psycopg, pydantic, pydantic_settings, sqlalchemy  # noqa: E401
import ctranslate2, faster_whisper, numpy, silero_vad  # noqa: E401
import torch, torchaudio  # noqa: E401
import torchaudio.functional as F

wave = torch.sin(torch.linspace(0, 3140.0, 16000)).unsqueeze(0)
out = F.resample(wave, 16000, 8000)
assert out.shape == (1, 8000), out.shape
print(f"audio-op-ok torch={torch.__version__} torchaudio={torchaudio.__version__}")
PYEOF

# Bake the faster-whisper "medium" weights at the PINNED snapshot revision so
# no runtime download can occur and the model content is reproducible.
# audio_whisperx_pipeline.py loads WhisperModel("medium",
# download_root=~/.cache/instadescribe); offline resolution walks
# refs/main -> snapshots/<revision>, so the ref pointer is written to the
# pinned commit and the load is proven against the baked cache before
# HF_HUB_OFFLINE is set.
ARG WHISPER_REVISION=08e178d48790749d25932bbc082711ddcfdfbc4f
RUN python - <<PYEOF
import pathlib

from huggingface_hub import snapshot_download

revision = "${WHISPER_REVISION}"
cache = "/home/worker/.cache/instadescribe"
path = pathlib.Path(
    snapshot_download("Systran/faster-whisper-medium", revision=revision, cache_dir=cache)
)
assert path.name == revision, f"snapshot resolved to {path.name}, not the pinned {revision}"
assert (path / "model.bin").is_file(), "model.bin missing from the pinned snapshot"
refs = path.parents[1] / "refs"
refs.mkdir(exist_ok=True)
(refs / "main").write_text(revision)

import os

os.environ["HF_HUB_OFFLINE"] = "1"
from faster_whisper.utils import download_model

resolved = pathlib.Path(download_model("medium", cache_dir=cache))
assert resolved == path, f"offline resolution returned {resolved}, expected {path}"
print(f"whisper-bake-ok revision={revision}")
PYEOF
RUN chown -R worker:worker /home/worker/.cache
# From here on, Hugging Face lookups must resolve from the baked cache only.
ENV HF_HUB_OFFLINE=1

# ---------------------------------------------------------------------------
# Retained G0 probe: pipeline + ONLY the fixture assets the G0 smoke needs.
FROM base AS g0smoke

WORKDIR /app
COPY --chown=worker:worker modular_pipeline /app/modular_pipeline
COPY --chown=worker:worker App/public/videos/sintel-blender-cc.mp4 /app/fixtures/sintel-blender-cc.mp4
COPY --chown=worker:worker services/worker/app/g0_smoke.py /app/g0_smoke.py

# Bounded writable workspace: run_job.py derives every write path from its own
# location — jobs/ under modular_pipeline/ and the sibling App/public tree.
RUN mkdir -p /app/modular_pipeline/jobs /app/App/public/data /app/App/public/videos \
    && chown -R worker:worker /app/modular_pipeline/jobs /app/App

USER worker
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/g0_smoke.py"]

# ---------------------------------------------------------------------------
# G5 production worker. /app/modular_pipeline is a read-only source template:
# every attempt copies it into an isolated throwaway workspace (workspace.py),
# so nothing under /app is written at runtime.
FROM base AS production

# G8.1 B1: the deterministic production-input digest (g8_source_digest.py)
# binds this image to the exact source it was built from; runtime gates
# recompute and compare it before any database/queue work. The base digest
# and Whisper revision are exposed as runtime-verifiable labels too.
ARG SOURCE_DIGEST=unset
LABEL io.instadescribe.source-digest=${SOURCE_DIGEST}
LABEL io.instadescribe.base-digest="sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b"
LABEL io.instadescribe.whisper-revision="08e178d48790749d25932bbc082711ddcfdfbc4f"

WORKDIR /app
COPY --chown=worker:worker modular_pipeline /app/modular_pipeline
COPY --chown=worker:worker services/worker/instadescribe_worker /app/instadescribe_worker
# Shared cross-service queue contract — one package, no worker-local copy.
COPY --chown=worker:worker packages/contracts/instadescribe_contracts /app/instadescribe_contracts
# Canonical persistence/domain code (the API's models ARE the schema owner —
# ADR-0008): only the model/domain subset, no FastAPI surface.
COPY --chown=worker:worker services/api/app/__init__.py /app/app/__init__.py
COPY --chown=worker:worker services/api/app/db/__init__.py /app/app/db/__init__.py
COPY --chown=worker:worker services/api/app/db/base.py /app/app/db/base.py
COPY --chown=worker:worker services/api/app/core/__init__.py /app/app/core/__init__.py
COPY --chown=worker:worker services/api/app/core/tenancy.py /app/app/core/tenancy.py
COPY --chown=worker:worker services/api/app/domain /app/app/domain
COPY --chown=worker:worker services/api/app/models /app/app/models
COPY --chown=worker:worker services/api/app/repositories/__init__.py /app/app/repositories/__init__.py
COPY --chown=worker:worker services/api/app/repositories/lifecycle.py /app/app/repositories/lifecycle.py
COPY --chown=worker:worker services/api/app/services/__init__.py /app/app/services/__init__.py
COPY --chown=worker:worker services/api/app/services/lifecycle.py /app/app/services/lifecycle.py
COPY --chown=worker:worker services/api/app/services/quota.py /app/app/services/quota.py
COPY --chown=worker:worker services/api/app/services/tts_previews.py /app/app/services/tts_previews.py

# Fail the BUILD if a forbidden asset ever reaches this target, and prove the
# worker import graph (queue/DB/pipeline seams) resolves inside the image.
RUN set -e; \
    for p in /app/fixtures /app/g0_smoke.py /app/App \
        /app/modular_pipeline/.env /app/modular_pipeline/jobs \
        /app/modular_pipeline/study_logs; do \
      if [ -e "$p" ]; then echo "forbidden asset baked: $p"; exit 1; fi; \
    done; \
    if find /app \( -name 'test_*' -o -name '*.mp4' -o -name '.env*' \
        -o -iname '*.pem' -o -iname '*.key' -o -iname '*.p12' -o -iname '*.pfx' \) -print | grep .; then \
      echo "forbidden assets baked into production target"; exit 1; \
    fi
RUN python -c "import instadescribe_worker.main, instadescribe_worker.consumer, \
instadescribe_worker.render, instadescribe_worker.preview, instadescribe_contracts.queue, app.models, \
app.domain.states, app.services.lifecycle, app.services.tts_previews" \
    && ffprobe -version >/dev/null

USER worker
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
CMD ["python", "-m", "instadescribe_worker.main"]
