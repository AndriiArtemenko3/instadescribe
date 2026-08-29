import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
# Worker package + the canonical API models/domain + shared contracts.
sys.path.insert(0, str(REPO / "services" / "worker"))
sys.path.insert(1, str(REPO / "services" / "api"))
sys.path.insert(2, str(REPO / "packages" / "contracts"))
sys.path.insert(3, str(REPO / "services" / "api" / "tests"))  # db_isolation guard
# The production image exposes /app through PYTHONPATH, which is what makes
# the dependency-free ``modular_pipeline.timed_transcript`` package importable
# in the database-owning worker process. Mirror that root explicitly so both
# ``pytest`` (console script) and ``python -m pytest`` exercise the same graph.
sys.path.insert(4, str(REPO))

from instadescribe_contracts.environment import getenv_compat  # noqa: E402

# Keep the v0.1 CI aliases operational for one transition window while all
# test modules consume only the canonical names during collection.
for _test_env_name in ("INSTADESCRIBE_TEST_DATABASE_URL", "INSTADESCRIBE_TEST_S3"):
    if (_test_env_value := getenv_compat(_test_env_name)) is not None:
        os.environ.setdefault(_test_env_name, _test_env_value)

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-2")
os.environ.setdefault("INSTADESCRIBE_S3_ENDPOINT_INTERNAL", "http://localhost:4566")
os.environ.setdefault("INSTADESCRIBE_SQS_ENDPOINT_INTERNAL", "http://localhost:4566")
os.environ.setdefault("INSTADESCRIBE_WORKER_ID", "worker-test")  # log label only
os.environ.setdefault("INSTADESCRIBE_PIPELINE_REVISION", "test")
os.environ.setdefault("INSTADESCRIBE_LONG_POLL_SECS", "0")
os.environ.setdefault("INSTADESCRIBE_PIPELINE_SOURCE", str(REPO / "modular_pipeline"))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:x@127.0.0.1:59998/none")

TEST_DB_URL = os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL")
requires_db = pytest.mark.skipif(
    not TEST_DB_URL, reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make g5-test`)"
)
requires_aws = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_S3"),
    reason="INSTADESCRIBE_TEST_S3 not set (LocalStack required; use `make g5-test`)",
)


@pytest.fixture(autouse=True)
def reset_worker_shutdown_state():
    """Keep the process-global SIGTERM latch isolated between unit tests."""

    from instadescribe_worker import executor

    executor.reset_shutdown_state()
    yield
    executor.reset_shutdown_state()


@pytest.fixture(scope="session")
def worker_db_url():
    """Run-scoped disposable database (same fail-closed guards as the API
    suite), migrated to head and dropped in finally."""
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
    from db_isolation import assert_safe_test_url, run_scoped_test_url
    from sqlalchemy.engine import make_url

    base = assert_safe_test_url(TEST_DB_URL, os.environ.get("DATABASE_URL"))
    run_url = assert_safe_test_url(run_scoped_test_url(base), os.environ.get("DATABASE_URL"))
    run_name = make_url(run_url).database
    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{run_name}"'))
    admin.dispose()

    cfg = Config(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    cfg.set_main_option("sqlalchemy.url", run_url)
    command.upgrade(cfg, "head")
    try:
        yield run_url
    finally:
        admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": run_name},
            )
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{run_name}"'))
        admin.dispose()


@pytest.fixture()
def worker_env(worker_db_url, monkeypatch):
    """Point the worker at the run-scoped database with fresh caches."""
    from instadescribe_worker.config import get_worker_settings, reset_worker_settings
    from instadescribe_worker.consumer import reset_worker_caches
    from instadescribe_worker.db import reset_db_caches
    from instadescribe_worker.preview import reset_preview_caches
    from instadescribe_worker.render import reset_render_caches

    monkeypatch.setenv("DATABASE_URL", worker_db_url)
    reset_worker_settings()
    reset_db_caches()
    reset_worker_caches()
    reset_preview_caches()
    reset_render_caches()
    yield get_worker_settings()
    reset_worker_settings()
    reset_db_caches()
    reset_worker_caches()
    reset_preview_caches()
    reset_render_caches()


@pytest.fixture()
def db_session(worker_env):
    import sqlalchemy as sa
    from instadescribe_worker.db import get_engine, get_sessionmaker

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM webhook_deliveries"))
        conn.execute(sa.text("DELETE FROM tts_preview_artifacts"))
        conn.execute(sa.text("DELETE FROM tts_previews"))
        conn.execute(sa.text("DELETE FROM render_attempt_artifacts"))
        conn.execute(sa.text("DELETE FROM deliverables"))
        conn.execute(sa.text("DELETE FROM renders"))
        conn.execute(sa.text("DELETE FROM reviews"))
        conn.execute(sa.text("DELETE FROM assets"))
        conn.execute(sa.text("DELETE FROM job_events"))
        conn.execute(sa.text("DELETE FROM audit_events"))
        conn.execute(sa.text("DELETE FROM webhook_endpoints"))
        conn.execute(sa.text("DELETE FROM artifacts"))
        conn.execute(sa.text("DELETE FROM scene_overrides"))
        conn.execute(sa.text("DELETE FROM quota_reservations"))
        conn.execute(sa.text("DELETE FROM organization_usage_periods"))
        conn.execute(sa.text("DELETE FROM jobs"))
        conn.execute(sa.text("DELETE FROM projects"))
        conn.execute(
            sa.text(
                "UPDATE organization_job_capacity SET awaiting_upload_jobs = 0, "
                "queued_jobs = 0, processing_jobs = 0, version = version + 1"
            )
        )
    session = get_sessionmaker()()
    yield session
    session.close()


@pytest.fixture()
def aws_resources(monkeypatch):
    """Run-owned namespaced queue pair + the media bucket; never touches the
    development queue. Queues are deleted in finally."""
    import secrets

    import boto3
    from instadescribe_worker.config import reset_worker_settings
    from instadescribe_worker.consumer import reset_worker_caches
    from queue_support import make_queue_pair  # shared with the API suite

    sqs = boto3.client(
        "sqs",
        region_name=os.environ["AWS_DEFAULT_REGION"],
        endpoint_url=os.environ["INSTADESCRIBE_SQS_ENDPOINT_INTERNAL"],
    )
    s3 = boto3.client(
        "s3",
        region_name=os.environ["AWS_DEFAULT_REGION"],
        endpoint_url=os.environ["INSTADESCRIBE_S3_ENDPOINT_INTERNAL"],
    )
    base = f"instadescribe-worker-test-{os.getpid()}-{secrets.token_hex(3)}-work"
    queue_url, dlq_url = make_queue_pair(sqs, base, visibility="5")
    bucket = "instascribe-media"
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": os.environ["AWS_DEFAULT_REGION"]},
        )
    # Versioning is REQUIRED (G5.1 C1) — idempotent even when it pre-exists.
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    monkeypatch.setenv("INSTADESCRIBE_WORK_QUEUE_URL", queue_url)
    monkeypatch.setenv("INSTADESCRIBE_MEDIA_BUCKET", bucket)
    reset_worker_settings()
    reset_worker_caches()
    try:
        yield {"sqs": sqs, "s3": s3, "queue_url": queue_url, "dlq_url": dlq_url, "bucket": bucket}
    finally:
        for url in (queue_url, dlq_url):
            try:
                sqs.delete_queue(QueueUrl=url)
            except Exception:
                pass
        reset_worker_settings()
        reset_worker_caches()
