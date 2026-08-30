"""Sentinel proof that `make cloud-test` cannot mutate the application DB or
the development SQS queue.

Procedure: migrate the application database to head; plant a durable sentinel
project + processing job AND an unrelated sentinel message on the development
`instascribe-work` queue (recording its attributes); run the full cloud suite
(destructive fixtures target only run-scoped guarded databases and run-owned
namespaced queues); then prove schema revision, tables, DB sentinel, queue
sentinel and queue attributes are all untouched. Cleanup runs in `finally` —
including on suite failure, assertion failure or interruption.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import boto3
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "api"))
sys.path.insert(0, str(REPO / "packages" / "contracts"))
from instadescribe_contracts.environment import getenv_compat  # noqa: E402

APP_URL = os.environ["DATABASE_URL"]
SQS_ENDPOINT = getenv_compat("INSTADESCRIBE_SQS_ENDPOINT_INTERNAL") or "http://localhost:4566"
REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-west-2")

cfg = Config(str(REPO / "alembic.ini"))
cfg.set_main_option("script_location", str(REPO / "migrations"))
cfg.set_main_option("sqlalchemy.url", APP_URL)
command.upgrade(cfg, "head")

engine = sa.create_engine(APP_URL)
sqs = boto3.client("sqs", region_name=REGION, endpoint_url=SQS_ENDPOINT)
dev_queue_url = sqs.get_queue_url(QueueName="instascribe-work")["QueueUrl"]

project_id, job_id = uuid.uuid4(), uuid.uuid4()
sentinel = f"isolation-sentinel-{uuid.uuid4().hex[:8]}"
queue_sentinel_body = json.dumps({"isolation-proof-sentinel": sentinel})
queue_receipt = None
ok = False

try:
    with engine.begin() as conn:
        head_before = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:id, :name)"),
            {"id": str(project_id), "name": sentinel},
        )
        # READY_FOR_REVIEW keeps the sentinel outside the compute-active slot.
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                "VALUES (:id, :pid, 'isolation-proof', 'READY_FOR_REVIEW', '{}'::jsonb)"
            ),
            {"id": str(job_id), "pid": str(project_id)},
        )
    attrs_before = sqs.get_queue_attributes(
        QueueUrl=dev_queue_url, AttributeNames=["VisibilityTimeout", "RedrivePolicy"]
    )["Attributes"]
    sqs.send_message(QueueUrl=dev_queue_url, MessageBody=queue_sentinel_body)

    print(f"[proof] sentinels planted: db={sentinel}, dev-queue message (head {head_before})")
    result = subprocess.run(["make", "cloud-test"], cwd=REPO)
    if result.returncode != 0:
        raise RuntimeError("cloud-test itself failed")

    with engine.begin() as conn:
        head_after = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = {
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                )
            )
        }
        survived = conn.execute(
            sa.text(
                "SELECT count(*) FROM projects p JOIN jobs j ON j.project_id = p.id "
                "WHERE p.id = :pid AND j.id = :jid AND p.name = :name"
            ),
            {"pid": str(project_id), "jid": str(job_id), "name": sentinel},
        ).scalar_one()

    attrs_after = sqs.get_queue_attributes(
        QueueUrl=dev_queue_url, AttributeNames=["VisibilityTimeout", "RedrivePolicy"]
    )["Attributes"]
    queue_survived = False
    for _ in range(5):
        for message in sqs.receive_message(
            QueueUrl=dev_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1
        ).get("Messages", []):
            if message["Body"] == queue_sentinel_body:
                queue_survived = True
                queue_receipt = message["ReceiptHandle"]
                break
            # Not ours: return it to the queue untouched.
            sqs.change_message_visibility(
                QueueUrl=dev_queue_url,
                ReceiptHandle=message["ReceiptHandle"],
                VisibilityTimeout=0,
            )
        if queue_survived:
            break

    ok = (
        head_after == head_before
        and {"projects", "jobs"} <= tables
        and survived == 1
        and queue_survived
        and attrs_before == attrs_after
    )
    print(
        f"[proof] head unchanged: {head_after == head_before} "
        f"({head_before} -> {head_after}); tables intact: "
        f"{ ({'projects', 'jobs'} <= tables) }; db sentinel: {survived == 1}; "
        f"dev-queue message survived: {queue_survived}; "
        f"dev-queue attrs unchanged: {attrs_before == attrs_after}"
    )
finally:
    # Always remove the exact sentinels — suite failure, assertion failure
    # and interruption included.
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM projects WHERE id = :pid"), {"pid": str(project_id)})
    except Exception:
        print("[proof] WARNING: db sentinel cleanup failed")
    try:
        if queue_receipt:
            sqs.delete_message(QueueUrl=dev_queue_url, ReceiptHandle=queue_receipt)
        else:
            for message in sqs.receive_message(
                QueueUrl=dev_queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1
            ).get("Messages", []):
                if message["Body"] == queue_sentinel_body:
                    sqs.delete_message(
                        QueueUrl=dev_queue_url, ReceiptHandle=message["ReceiptHandle"]
                    )
                else:
                    sqs.change_message_visibility(
                        QueueUrl=dev_queue_url,
                        ReceiptHandle=message["ReceiptHandle"],
                        VisibilityTimeout=0,
                    )
    except Exception:
        print("[proof] WARNING: queue sentinel cleanup failed")
    engine.dispose()

print("[proof] ISOLATION PROOF OK" if ok else "[proof] ISOLATION PROOF FAILED")
sys.exit(0 if ok else 1)
