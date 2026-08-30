# InstaDescribe developer tasks. Run `make help` to list them.
.DEFAULT_GOAL := help
.PHONY: help demo dev server install install-web install-py test lint \
	g1-up g1-down g1-check g1-verify g1-test \
	cloud-venv migrate g2-verify cloud-test \
	g5-test g5-build g5-smoke \
	g8-build g8-image-proof g8-memtest smoke-local \
	g8-api-build g8-api-image-proof g8-acceptance

# The CURRENT (G8-rebuilt) production worker image tag — G8 acceptance
# targets run exactly this image; plain compose keeps the pinned G5 tag.
G8_WORKER_IMAGE ?= instadescribe-worker:g8
G8_API_IMAGE ?= instadescribe-api:g8

# Local compose DSN (placeholder credential, loopback-bound — never production).
LOCAL_DATABASE_URL ?= postgresql+psycopg://instascribe:local-dev-only@127.0.0.1:5432/instascribe
# Disposable integration-test database: a SEPARATE logical database on the
# same pinned service. Destructive test fixtures are guarded to this target.
TEST_DATABASE_URL ?= postgresql+psycopg://instascribe:local-dev-only@127.0.0.1:5432/instascribe_test

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

demo: install-web  ## Zero-key demo: build the committed-fixture web app and serve it (no API key, no backend)
	cd App && npm run demo

dev: install-web  ## Run the web app in dev mode against a local backend (start `make server` in another shell)
	cd App && npm run dev

server:  ## Run the Flask API + single-origin server on :8765
	python modular_pipeline/server.py

install: install-web install-py  ## Install both the web and Python dependencies

install-web:  ## Install frontend dependencies
	cd App && npm install

install-py:  ## Create .venv and install the full pipeline
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

test:  ## Run the Python and web test suites
	pytest -q
	cd App && npm test

lint:  ## Ruff (Python) + ESLint (web)
	ruff check .
	cd App && npm run lint

g1-up:  ## Start the G1 local cloud stack (PostgreSQL + LocalStack + health-only API)
	docker compose up --build -d --wait

g1-down:  ## Stop the G1 stack (keeps the postgres volume; `docker compose down -v` drops it)
	docker compose down

g1-check:  ## Validate compose config and hit both health routes
	docker compose config -q
	curl -sf http://localhost:8000/healthz >/dev/null
	curl -sf http://localhost:8000/api/healthz >/dev/null
	@echo "g1-check OK"

g1-verify:  ## Re-run the bootstrap, then machine-assert bucket/CORS/queues/redrive (nonzero on mismatch)
	docker compose exec localstack /etc/localstack/init/ready.d/01-bootstrap.sh
	docker compose exec localstack /etc/localstack/init/ready.d/02-verify.sh

g1-test:  ## Run the focused API health tests under Python 3.12 in a uv-managed venv
	cd services/api && uv venv .venv --clear --quiet --python 3.12 \
		&& uv pip install --python .venv/bin/python -q -r requirements-dev.txt \
		&& .venv/bin/python --version \
		&& .venv/bin/pytest tests/test_health.py -q

cloud-venv: services/api/.venv/.stamp  ## Create/refresh the Python 3.12 venv for cloud API + database work

# Stamp-based: parallel suite invocations skip an up-to-date venv instead of
# racing to recreate it; a requirements change rebuilds it.
services/api/.venv/.stamp: services/api/requirements-dev.txt
	cd services/api && uv venv .venv --clear --quiet --python 3.12 \
		&& uv pip install --python .venv/bin/python -q -r requirements-dev.txt \
		&& .venv/bin/python --version
	touch services/api/.venv/.stamp

migrate: cloud-venv  ## Apply Alembic migrations to the local compose database
	DATABASE_URL=$(LOCAL_DATABASE_URL) services/api/.venv/bin/alembic upgrade head

g2-verify: cloud-venv  ## Assert ORM metadata and migrations are drift-free against the local DB
	DATABASE_URL=$(LOCAL_DATABASE_URL) services/api/.venv/bin/alembic check

test-db:  ## Create the disposable integration-test database (idempotent; app volume untouched)
	docker compose up -d --wait postgres
	docker compose exec -T postgres sh -c \
		"psql -U instascribe -d instascribe -tAc \"SELECT 1 FROM pg_database WHERE datname='instascribe_test'\" | grep -q 1 \
		|| createdb -U instascribe instascribe_test"

cloud-test: cloud-venv test-db  ## Full cloud API test suite (runs against the DISPOSABLE test database only)
	DATABASE_URL=$(LOCAL_DATABASE_URL) \
	INSTADESCRIBE_TEST_DATABASE_URL=$(TEST_DATABASE_URL) INSTADESCRIBE_TEST_S3=1 \
		services/api/.venv/bin/pytest services/api/tests -q

isolation-proof: cloud-venv test-db  ## Sentinel proof: cloud-test cannot mutate the app database OR the dev queue
	DATABASE_URL=$(LOCAL_DATABASE_URL) \
	AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-west-2 \
	INSTADESCRIBE_SQS_ENDPOINT_INTERNAL=http://localhost:4566 \
		services/api/.venv/bin/python services/api/scripts/prove_isolation.py

g5-test: cloud-venv test-db  ## Worker unit + integration suites (run-scoped DB + run-owned queues)
	DATABASE_URL=$(LOCAL_DATABASE_URL) \
	INSTADESCRIBE_TEST_DATABASE_URL=$(TEST_DATABASE_URL) INSTADESCRIBE_TEST_S3=1 \
		services/api/.venv/bin/pytest services/worker/tests -q

g5-build:  ## Build the PRODUCTION worker image (linux/amd64, no fixture/tests/secrets)
	docker buildx build --platform linux/amd64 --target production \
		-t instadescribe-worker:g5 -f services/worker/Dockerfile --load .

g5-smoke: cloud-venv g5-build  ## Full production-image vertical slice (fake provider, real S3/SQS/PG/pipeline)
	services/api/.venv/bin/python services/worker/scripts/g5_smoke.py

g8-build: cloud-venv  ## Rebuild the CURRENT production worker image (linux/amd64), source-digest-bound; prints build duration
	time docker buildx build --platform linux/amd64 --target production \
		--build-arg SOURCE_DIGEST=$$(services/api/.venv/bin/python services/worker/scripts/g8_source_digest.py worker) \
		-t $(G8_WORKER_IMAGE) -f services/worker/Dockerfile --load .

g8-image-proof: cloud-venv  ## Prove provenance/contents of the fresh G8 image (pins, offline weights, UID, forbidden assets)
	INSTADESCRIBE_WORKER_IMAGE=$(G8_WORKER_IMAGE) \
		services/api/.venv/bin/python services/worker/scripts/g8_image_proof.py

g8-api-build: cloud-venv  ## Build the CURRENT production API image (linux/amd64, digest-pinned base, source-digest-bound)
	time docker buildx build --platform linux/amd64 \
		--build-arg SOURCE_DIGEST=$$(services/api/.venv/bin/python services/worker/scripts/g8_source_digest.py api) \
		-t $(G8_API_IMAGE) -f services/api/Dockerfile --load .

g8-api-image-proof: cloud-venv  ## Prove the fresh API image: pins, binding, UID/CMD, deps, migrations, live health + readiness 503/200 cycle (private network; no dev ports)
	INSTADESCRIBE_API_IMAGE=$(G8_API_IMAGE) \
		services/api/.venv/bin/python services/worker/scripts/g8_api_image_proof.py

g8-memtest: cloud-venv  ## Mandatory R2 five-minute 8 GiB memory test on compose project instadescribe-g8-memtest. WARNING: uses `down -v` on THAT project only (dev volume untouched); temp video generated at runtime, never committed
	INSTADESCRIBE_WORKER_IMAGE=$(G8_WORKER_IMAGE) INSTADESCRIBE_API_IMAGE=$(G8_API_IMAGE) \
		services/api/.venv/bin/python services/worker/scripts/g8_memory_test.py

smoke-local: cloud-venv  ## One-command local acceptance smoke on compose project instadescribe-g8-smoke. WARNING: its final `down -v` destroys THAT project's volume (the dev project/volume is untouched; the dev stack must be stopped first)
	INSTADESCRIBE_WORKER_IMAGE=$(G8_WORKER_IMAGE) INSTADESCRIBE_API_IMAGE=$(G8_API_IMAGE) \
		services/api/.venv/bin/python services/worker/scripts/smoke_local.py

g8-acceptance:  ## The FULL G8 acceptance sequence, strictly ordered (worker build -> worker proof -> API build -> API proof -> five-minute memory test -> smoke-local). No parallel prerequisites; each runtime step re-verifies the current-source image binding.
	$(MAKE) g8-build
	$(MAKE) g8-image-proof
	$(MAKE) g8-api-build
	$(MAKE) g8-api-image-proof
	$(MAKE) g8-memtest
	$(MAKE) smoke-local
