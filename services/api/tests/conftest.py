import hashlib
import os
import sys
from pathlib import Path

import pytest

# Make `app` and the shared contracts importable when pytest runs from the
# repository root (the repo-root pyproject.toml wins rootdir detection).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(1, str(Path(__file__).resolve().parents[3] / "packages" / "contracts"))

from instadescribe_contracts.environment import getenv_compat  # noqa: E402

# CI/test tooling participates in the same one-release namespace bridge. Make
# the resolved canonical values visible to modules that declare skip markers
# during import; conflicting old/new values still fail before collection.
for _test_env_name in ("INSTADESCRIBE_TEST_DATABASE_URL", "INSTADESCRIBE_TEST_S3"):
    if (_test_env_value := getenv_compat(_test_env_name)) is not None:
        os.environ.setdefault(_test_env_name, _test_env_value)

# Test-environment defaults — documented placeholders only, set BEFORE any
# test module imports app.main. DATABASE_URL points at a closed port so unit
# tests never touch a real database (engines are lazy; 422 paths don't connect).
TEST_TOKEN = "test-token"
os.environ.setdefault("PORTFOLIO_TOKEN_SHA256", hashlib.sha256(TEST_TOKEN.encode()).hexdigest())
os.environ.setdefault("INSTADESCRIBE_PIPELINE_REVISION", "test")
os.environ.setdefault(
    "INSTADESCRIBE_API_KEY_PEPPER",
    "test-only-integration-api-key-pepper",
)
os.environ.setdefault("INSTADESCRIBE_S3_ENDPOINT_PUBLIC", "http://localhost:4566")
os.environ.setdefault("INSTADESCRIBE_S3_ENDPOINT_INTERNAL", "http://localhost:4566")
os.environ.setdefault("INSTADESCRIBE_SQS_ENDPOINT_INTERNAL", "http://localhost:4566")
os.environ.setdefault("INSTADESCRIBE_S3_FORCE_PATH_STYLE", "1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-2")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://placeholder:placeholder@127.0.0.1:59998/placeholder"
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DB_URL = os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DB_URL,
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)
requires_s3 = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_S3"),
    reason="INSTADESCRIBE_TEST_S3 not set (LocalStack required; use `make cloud-test` or CI)",
)

AUTH = {"X-Portfolio-Token": TEST_TOKEN}


@pytest.fixture(scope="session")
def safe_test_db_url():
    """Isolation gate + run-scoped disposable database.

    The guard refuses missing/ambiguous/retargeting/app-colliding URLs BEFORE
    any engine/Alembic/cleanup operation (db_isolation.py). Each suite run
    then creates its own uniquely named database so concurrent invocations
    cannot downgrade or delete each other's target; it is dropped in finally.
    """
    import sqlalchemy as sa
    from db_isolation import assert_safe_test_url, run_scoped_test_url
    from sqlalchemy.engine import make_url

    app_url = os.environ.get("DATABASE_URL")
    base = assert_safe_test_url(TEST_DB_URL, app_url)
    run_url = assert_safe_test_url(run_scoped_test_url(base), app_url)
    run_name = make_url(run_url).database

    admin = sa.create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{run_name}"'))
    admin.dispose()
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


@pytest.fixture(scope="session")
def alembic_config(safe_test_db_url):
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # The URL travels inside the Alembic config — the suite never redirects
    # the process-global DATABASE_URL at the application database's expense.
    cfg.set_main_option("sqlalchemy.url", safe_test_db_url)
    return cfg


@pytest.fixture(scope="session")
def migrated_db(alembic_config, safe_test_db_url):
    """Session-scoped: upgrade the DISPOSABLE test database to head; tear back
    to base. Destructive operations are scoped to the guarded test target only."""
    from alembic import command

    command.downgrade(alembic_config, "base")  # clean slate even after aborts
    command.upgrade(alembic_config, "head")
    yield safe_test_db_url
    command.downgrade(alembic_config, "base")


@pytest.fixture()
def db_engine(migrated_db):
    import sqlalchemy as sa

    engine = sa.create_engine(migrated_db)
    # Per-test isolation: children first, then jobs, then projects.
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM organization_invitations"))
        conn.execute(sa.text("DELETE FROM analyst_decisions"))
        conn.execute(sa.text("DELETE FROM belief_snapshots"))
        conn.execute(sa.text("DELETE FROM investigation_steps"))
        conn.execute(sa.text("DELETE FROM evidence_items"))
        conn.execute(sa.text("DELETE FROM source_records"))
        conn.execute(sa.text("DELETE FROM investigations"))
        conn.execute(sa.text("DELETE FROM webhook_deliveries"))
        conn.execute(sa.text("DELETE FROM tts_preview_artifacts"))
        conn.execute(sa.text("DELETE FROM tts_previews"))
        conn.execute(sa.text("DELETE FROM render_attempt_artifacts"))
        conn.execute(sa.text("DELETE FROM deliverables"))
        conn.execute(sa.text("DELETE FROM renders"))
        conn.execute(sa.text("DELETE FROM reviews"))
        conn.execute(sa.text("DELETE FROM assets"))
        conn.execute(sa.text("DELETE FROM quota_reservations"))
        conn.execute(sa.text("DELETE FROM organization_usage_periods"))
        conn.execute(sa.text("DELETE FROM job_events"))
        conn.execute(sa.text("DELETE FROM audit_events"))
        conn.execute(sa.text("DELETE FROM webhook_endpoints"))
        conn.execute(sa.text("DELETE FROM idempotency_records"))
        conn.execute(sa.text("DELETE FROM scene_overrides"))
        conn.execute(sa.text("DELETE FROM artifacts"))
        conn.execute(sa.text("DELETE FROM jobs"))
        conn.execute(sa.text("DELETE FROM projects"))
        conn.execute(sa.text("DELETE FROM api_keys"))
        conn.execute(sa.text("DELETE FROM service_accounts"))
        conn.execute(
            sa.text(
                "DELETE FROM organization_memberships "
                "WHERE principal_id <> '00000000-0000-4000-8000-000000000002'::uuid"
            )
        )
        conn.execute(
            sa.text(
                "DELETE FROM principals WHERE id <> '00000000-0000-4000-8000-000000000002'::uuid"
            )
        )
        conn.execute(
            sa.text(
                "DELETE FROM organizations WHERE id <> '00000000-0000-4000-8000-000000000001'::uuid"
            )
        )
        conn.execute(
            sa.text(
                "DELETE FROM organization_quotas "
                "WHERE organization_id <> '00000000-0000-4000-8000-000000000001'::uuid"
            )
        )
        conn.execute(
            sa.text(
                "DELETE FROM organization_job_capacity "
                "WHERE organization_id <> '00000000-0000-4000-8000-000000000001'::uuid"
            )
        )
        conn.execute(
            sa.text(
                "UPDATE organization_job_capacity SET awaiting_upload_jobs = 0, "
                "queued_jobs = 0, processing_jobs = 0, version = 1 "
                "WHERE organization_id = '00000000-0000-4000-8000-000000000001'::uuid"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture()
def api_db_client(db_engine, migrated_db, monkeypatch):
    """TestClient whose app engine points at the migrated test database."""
    from app.core.config import get_settings
    from app.db.session import reset_engine_caches
    from app.main import app
    from app.services.cognito_jwt import reset_cognito_jwt_cache
    from app.services.s3 import reset_s3_caches
    from app.services.sqs import reset_sqs_caches
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", migrated_db)
    get_settings.cache_clear()
    reset_engine_caches()
    reset_s3_caches()
    reset_sqs_caches()
    reset_cognito_jwt_cache()
    yield TestClient(app)
    get_settings.cache_clear()
    reset_engine_caches()
    reset_s3_caches()
    reset_sqs_caches()
    reset_cognito_jwt_cache()


def _sqs_client():
    import boto3

    return boto3.client(
        "sqs",
        region_name=os.environ["AWS_DEFAULT_REGION"],
        endpoint_url=os.environ["INSTADESCRIBE_SQS_ENDPOINT_INTERNAL"],
    )


from queue_support import make_queue_pair  # noqa: E402  (shared with the worker suite)


@pytest.fixture(scope="session")
def run_queue_pair():
    """Session-scoped RUN-OWNED queue pair (namespaced per invocation) —
    tests never create, configure, drain, or delete the development queue.
    Dropped in finally so failed runs still clean up."""
    import secrets

    client = _sqs_client()
    base_name = f"instadescribe-test-{os.getpid()}-{secrets.token_hex(3)}-work"
    queue_url, dlq_url = make_queue_pair(client, base_name)
    try:
        yield queue_url, dlq_url, client
    finally:
        for url in (queue_url, dlq_url):
            try:
                client.delete_queue(QueueUrl=url)
            except Exception:
                pass  # preserve evidence; stale run-queues are namespaced anyway


@pytest.fixture()
def work_queue(run_queue_pair, monkeypatch):
    """Point the API-under-test at the run-owned queue and drain ONLY it."""
    from app.services.sqs import reset_sqs_caches

    queue_url, _dlq_url, client = run_queue_pair
    monkeypatch.setenv("INSTADESCRIBE_WORK_QUEUE_URL", queue_url)
    from app.core.config import get_settings

    get_settings.cache_clear()
    reset_sqs_caches()
    while True:
        messages = client.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0
        ).get("Messages", [])
        if not messages:
            break
        for message in messages:
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
    yield queue_url, client
    get_settings.cache_clear()
    reset_sqs_caches()


@pytest.fixture()
def media_bucket():
    """Ensure the media bucket exists with BPA + default encryption (idempotent;
    lets CI run without the compose ready.d bootstrap)."""
    import boto3

    client = boto3.client(
        "s3",
        region_name=os.environ["AWS_DEFAULT_REGION"],
        endpoint_url=os.environ["INSTADESCRIBE_S3_ENDPOINT_INTERNAL"],
    )
    bucket = os.environ.get("INSTADESCRIBE_MEDIA_BUCKET", "instascribe-media")
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": os.environ["AWS_DEFAULT_REGION"]},
        )
        client.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        client.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
    # Versioning is REQUIRED (G5.1 C1) — idempotent, applied even when the
    # bucket pre-exists so older local stacks converge.
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    return bucket, client
