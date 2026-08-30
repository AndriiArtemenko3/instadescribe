# syntax=docker/dockerfile:1
# InstaDescribe cloud API — production image (G8.1 F).
# Build context is the repository root:
#   docker build -f services/api/Dockerfile .
#
# Base pinned by the SAME python:3.12-slim manifest-list digest the worker
# uses (validity for linux/amd64 proven via `docker buildx imagetools
# inspect` — the index carries an amd64 manifest, 3.12.13-slim-trixie).
FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b

# G8.1 B1/F: deterministic production-input digest (g8_source_digest.py api)
# binds the image to the exact source; runtime gates recompute and compare
# it before use. The base digest is exposed as a label too.
ARG SOURCE_DIGEST=unset
LABEL io.instadescribe.source-digest=${SOURCE_DIGEST}
LABEL io.instadescribe.base-digest="sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b"

RUN useradd --create-home --uid 10001 api

# Deterministic dependency set compiled by `uv pip compile`; resolver
# consistency is asserted at build time.
COPY services/api/requirements.txt /tmp/api-requirements.txt
RUN pip install --no-cache-dir -r /tmp/api-requirements.txt && pip check

WORKDIR /srv
COPY --chown=api:api services/api/app /srv/app
# Operational entrypoints use the same immutable API image.  The webhook
# dispatcher is a separate ECS process; it never runs in the request server.
COPY --chown=api:api services/api/scripts /srv/scripts
# Shared cross-service contracts (queue message schema) — one package, no
# API-local duplicate.
COPY --chown=api:api packages/contracts/instadescribe_contracts /srv/instadescribe_contracts
# Migration tree ships in the image: G9 runs `alembic upgrade head` as an
# explicit one-off task before service rollout (locally: the compose
# `migrate` one-shot service).
COPY --chown=api:api alembic.ini /srv/alembic.ini
COPY --chown=api:api migrations /srv/migrations
# Deterministic imports for uvicorn AND alembic regardless of invocation cwd.
ENV PYTHONPATH=/srv

# Fail the BUILD if a forbidden asset ever reaches the image, and prove the
# app, contracts and migration tree resolve inside it.
RUN set -e; \
    if find /srv \( -name 'test_*' -o -name '*.mp4' -o -name '.env*' \
        -o -name '*HANDOFF*' -o -name '*.tfstate*' -o -iname '*.pem' \
        -o -iname '*.key' -o -iname '*.p12' -o -iname '*.pfx' \) -print | grep .; then \
      echo "forbidden assets baked into the API image"; exit 1; fi; \
    test -d /srv/migrations/versions
RUN python -c "import app.main, app.models, app.domain.states, \
instadescribe_contracts.queue"

USER api
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
