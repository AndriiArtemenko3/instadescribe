#!/bin/bash
# Machine-asserting verification of the G1 bootstrap — exits nonzero on any
# mismatch (printing JSON is not verification). Runs in ready.d after
# 01-bootstrap.sh on every container start, and by hand via `make g1-verify`.
set -euo pipefail

BUCKET="instascribe-media"
QUEUE="instascribe-work"
DLQ="instascribe-work-dlq"
INVESTIGATION_QUEUE="instadescribe-investigation"
INVESTIGATION_DLQ="instadescribe-investigation-dlq"

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

# Lifecycle must retain the 30-day untagged fallback and every exact
# investigation tag tier for both current and noncurrent versions.
LIFECYCLE_JSON=$(awslocal s3api get-bucket-lifecycle-configuration --bucket "$BUCKET")
export LIFECYCLE_JSON
python3 <<'PY'
import json
import os

tag_key = "instadescribe-retention-days"
rules = json.loads(os.environ["LIFECYCLE_JSON"])["Rules"]
by_id = {rule["ID"]: rule for rule in rules}
expected_ids = {"expire-abandoned-uploads"} | {
    f"expire-investigation-source-{days}d" for days in range(1, 31)
}
assert set(by_id) == expected_ids, (
    f"lifecycle IDs: {sorted(by_id)} != {sorted(expected_ids)}"
)

fallback = by_id["expire-abandoned-uploads"]
assert fallback["Status"] == "Enabled", fallback
assert fallback["Filter"] == {"Prefix": "uploads/"}, fallback
assert fallback["Expiration"]["Days"] == 30, fallback
assert fallback["NoncurrentVersionExpiration"]["NoncurrentDays"] == 30, fallback
assert fallback["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1, fallback

for days in range(1, 31):
    rule = by_id[f"expire-investigation-source-{days}d"]
    assert rule["Status"] == "Enabled", rule
    assert rule["Filter"] == {
        "And": {
            "Prefix": "uploads/orgs/",
            "Tags": [{"Key": tag_key, "Value": str(days)}],
        }
    }, rule
    assert rule["Expiration"]["Days"] == days, rule
    assert rule["NoncurrentVersionExpiration"]["NoncurrentDays"] == days, rule
    assert "AbortIncompleteMultipartUpload" not in rule, rule
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

# The new investigation queue must be isolated yet carry the same bounded
# at-least-once retry policy.  This check never mutates the legacy pair.
INVESTIGATION_DLQ_URL=$(awslocal sqs get-queue-url \
  --queue-name "$INVESTIGATION_DLQ" --output text --query QueueUrl)
INVESTIGATION_DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url "$INVESTIGATION_DLQ_URL" --attribute-names QueueArn \
  --output text --query "Attributes.QueueArn")
INVESTIGATION_DLQ_ATTRS_JSON=$(awslocal sqs get-queue-attributes \
  --queue-url "$INVESTIGATION_DLQ_URL" \
  --attribute-names MessageRetentionPeriod SqsManagedSseEnabled RedriveAllowPolicy)
INVESTIGATION_QUEUE_URL=$(awslocal sqs get-queue-url \
  --queue-name "$INVESTIGATION_QUEUE" --output text --query QueueUrl)
INVESTIGATION_QUEUE_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url "$INVESTIGATION_QUEUE_URL" --attribute-names QueueArn \
  --output text --query "Attributes.QueueArn")
INVESTIGATION_ATTRS_JSON=$(awslocal sqs get-queue-attributes \
  --queue-url "$INVESTIGATION_QUEUE_URL" \
  --attribute-names VisibilityTimeout MessageRetentionPeriod ReceiveMessageWaitTimeSeconds SqsManagedSseEnabled RedrivePolicy)
export INVESTIGATION_ATTRS_JSON
export INVESTIGATION_DLQ_ATTRS_JSON
export EXPECTED_INVESTIGATION_DLQ_ARN="$INVESTIGATION_DLQ_ARN"
export EXPECTED_INVESTIGATION_QUEUE_ARN="$INVESTIGATION_QUEUE_ARN"
python3 <<'PY'
import json
import os

attrs = json.loads(os.environ["INVESTIGATION_ATTRS_JSON"])["Attributes"]
assert attrs["VisibilityTimeout"] == "1800", (
    f"investigation VisibilityTimeout: {attrs['VisibilityTimeout']}"
)
assert attrs["MessageRetentionPeriod"] == "345600", attrs
assert attrs["ReceiveMessageWaitTimeSeconds"] == "20", attrs
assert attrs["SqsManagedSseEnabled"] == "true", attrs
redrive = json.loads(attrs["RedrivePolicy"])
assert redrive["maxReceiveCount"] == "3", (
    f"investigation maxReceiveCount: {redrive['maxReceiveCount']}"
)
expected_arn = os.environ["EXPECTED_INVESTIGATION_DLQ_ARN"]
assert redrive["deadLetterTargetArn"] == expected_arn, (
    f"investigation DLQ ARN: {redrive['deadLetterTargetArn']} != {expected_arn}"
)

dlq_attrs = json.loads(os.environ["INVESTIGATION_DLQ_ATTRS_JSON"])["Attributes"]
assert dlq_attrs["MessageRetentionPeriod"] == "1209600", dlq_attrs
assert dlq_attrs["SqsManagedSseEnabled"] == "true", dlq_attrs
allow = json.loads(dlq_attrs["RedriveAllowPolicy"])
assert allow == {
    "redrivePermission": "byQueue",
    "sourceQueueArns": [os.environ["EXPECTED_INVESTIGATION_QUEUE_ARN"]],
}, allow
PY

if [ "$QUEUE_URL" = "$INVESTIGATION_QUEUE_URL" ]; then
  echo "[g1-verify] FAIL: workflow queues unexpectedly resolve to one URL" >&2
  exit 1
fi

echo "[g1-verify] ASSERTED OK: bucket=$BUCKET versioning=Enabled cors=exact retention_tiers=1..30 legacy_queue=$QUEUE investigation_queue=$INVESTIGATION_QUEUE visibility=1800 redrive=maxReceiveCount:3"
