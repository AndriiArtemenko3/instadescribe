#!/bin/bash
# Machine-asserting verification of the G1 bootstrap — exits nonzero on any
# mismatch (printing JSON is not verification). Runs in ready.d after
# 01-bootstrap.sh on every container start, and by hand via `make g1-verify`.
set -euo pipefail

BUCKET="instascribe-media"
QUEUE="instascribe-work"
DLQ="instascribe-work-dlq"

# Bucket exists.
awslocal s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1

# Block-public-access: all four flags must be true.
BPA_JSON=$(awslocal s3api get-public-access-block --bucket "$BUCKET")
export BPA_JSON
python3 <<'PY'
import json
import os

cfg = json.loads(os.environ["BPA_JSON"])["PublicAccessBlockConfiguration"]
missing = [k for k, v in cfg.items() if v is not True]
assert not missing, f"public access block not fully enabled: {cfg}"
PY

# Default encryption: AES256.
ENC_JSON=$(awslocal s3api get-bucket-encryption --bucket "$BUCKET")
export ENC_JSON
python3 <<'PY'
import json
import os

rules = json.loads(os.environ["ENC_JSON"])["ServerSideEncryptionConfiguration"]["Rules"]
algos = [r["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] for r in rules]
assert "AES256" in algos, f"default encryption missing: {rules}"
PY

# Versioning must be Enabled (G5.1 C1): source identity is pinned by
# VersionId; an unversioned bucket cannot satisfy upload verification.
VERSIONING_STATUS=$(awslocal s3api get-bucket-versioning --bucket "$BUCKET" \
  --output text --query "Status")
if [ "$VERSIONING_STATUS" != "Enabled" ]; then
  echo "[g1-verify] FAIL: bucket versioning is '$VERSIONING_STATUS', expected 'Enabled'" >&2
  exit 1
fi

# CORS matches the exact expected configuration.
CORS_JSON=$(awslocal s3api get-bucket-cors --bucket "$BUCKET")
export CORS_JSON
python3 <<'PY'
import json
import os

rules = json.loads(os.environ["CORS_JSON"])["CORSRules"]
expected = [
    {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["POST", "GET", "HEAD"],
        "AllowedOrigins": ["http://localhost:5173"],
        "ExposeHeaders": ["ETag", "Accept-Ranges", "Content-Range", "Content-Length"],
        "MaxAgeSeconds": 3000,
    }
]
assert rules == expected, f"CORS mismatch:\nactual:   {rules}\nexpected: {expected}"
PY

# Queues exist; work queue carries the exact visibility + redrive contract.
DLQ_URL=$(awslocal sqs get-queue-url --queue-name "$DLQ" --output text --query QueueUrl)
DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "$DLQ_URL" \
  --attribute-names QueueArn --output text --query "Attributes.QueueArn")
QUEUE_URL=$(awslocal sqs get-queue-url --queue-name "$QUEUE" --output text --query QueueUrl)
ATTRS_JSON=$(awslocal sqs get-queue-attributes --queue-url "$QUEUE_URL" \
  --attribute-names VisibilityTimeout RedrivePolicy)
export ATTRS_JSON
export EXPECTED_DLQ_ARN="$DLQ_ARN"
python3 <<'PY'
import json
import os

attrs = json.loads(os.environ["ATTRS_JSON"])["Attributes"]
assert attrs["VisibilityTimeout"] == "1800", f"VisibilityTimeout: {attrs['VisibilityTimeout']}"
redrive = json.loads(attrs["RedrivePolicy"])
assert redrive["maxReceiveCount"] == "3", f"maxReceiveCount: {redrive['maxReceiveCount']}"
expected_arn = os.environ["EXPECTED_DLQ_ARN"]
assert redrive["deadLetterTargetArn"] == expected_arn, (
    f"DLQ ARN: {redrive['deadLetterTargetArn']} != {expected_arn}"
)
PY

echo "[g1-verify] ASSERTED OK: bucket=$BUCKET versioning=Enabled cors=exact queue=$QUEUE dlq=$DLQ visibility=1800 redrive=maxReceiveCount:3"
