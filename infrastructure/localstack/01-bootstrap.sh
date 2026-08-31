#!/bin/bash
# Idempotent LocalStack bootstrap for the InstaDescribe G1 local stack.
# Mounted at /etc/localstack/init/ready.d/ so it runs on every container
# start; every operation below is safe to re-run (probe-then-create or
# last-write-wins put/set). Owns: private media bucket + browser CORS,
# legacy work queue plus the isolated investigation queue, their DLQs, and
# redrive policies (implementation-plan G1 + observable-video pivot).
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-eu-west-2}"
BUCKET="instascribe-media"
QUEUE="instascribe-work"
DLQ="instascribe-work-dlq"
INVESTIGATION_QUEUE="instadescribe-investigation"
INVESTIGATION_DLQ="instadescribe-investigation-dlq"

# Private media bucket (probe-then-create).
if ! awslocal s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  awslocal s3api create-bucket --bucket "$BUCKET" \
    --create-bucket-configuration "LocationConstraint=$REGION"
fi

# Private bucket posture: block all public access + default encryption
# (mirrors the production contract; asserted by 02-verify.sh).
awslocal s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
awslocal s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
# Versioning (G5.1 C1): the reusable presigned POST can write a NEWER object
# at the same key; the exact processed source must therefore be pinned by
# VersionId. put-bucket-versioning is last-write-wins — idempotent.
awslocal s3api put-bucket-versioning --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

# Browser CORS for the local Vite origin: presigned POST uploads (POST),
# artifact/manifest GETs and probes (GET/HEAD), permissive request headers
# for the presigned-POST form fields, and Range/Accept-Ranges exposure so
# <video> seeking works from the browser. put-bucket-cors overwrites — idempotent.
awslocal s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration '{
  "CORSRules": [{
    "AllowedOrigins": ["http://localhost:5173"],
    "AllowedMethods": ["POST", "GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag", "Accept-Ranges", "Content-Range", "Content-Length"],
    "MaxAgeSeconds": 3000
  }]
}'

# Versioned Browser investigation POSTs carry one signed
# instadescribe-retention-days=1..30 object tag. The broad 30-day rule is the
# fallback for untagged/legacy abandoned uploads; the tier rules cover every
# tagged current or noncurrent version, including versions created by reusing
# one presigned form before it expires. S3 lifecycle timing is eventual; the
# database janitor still deletes every known pinned version exactly.
LIFECYCLE_CONFIGURATION=$(python3 <<'PY'
import json

tag_key = "instadescribe-retention-days"
rules = [
    {
        "ID": "expire-abandoned-uploads",
        "Status": "Enabled",
        "Filter": {"Prefix": "uploads/"},
        "Expiration": {"Days": 30},
        "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
    }
]
for days in range(1, 31):
    rules.append(
        {
            "ID": f"expire-investigation-source-{days}d",
            "Status": "Enabled",
            "Filter": {
                "And": {
                    "Prefix": "uploads/orgs/",
                    "Tags": [{"Key": tag_key, "Value": str(days)}],
                }
            },
            "Expiration": {"Days": days},
            "NoncurrentVersionExpiration": {"NoncurrentDays": days},
        }
    )
print(json.dumps({"Rules": rules}, separators=(",", ":")))
PY
)
awslocal s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
  --lifecycle-configuration "$LIFECYCLE_CONFIGURATION"

# DLQ first, then the work queue pointing its redrive policy at the DLQ.
awslocal sqs create-queue --queue-name "$DLQ" >/dev/null
DLQ_URL=$(awslocal sqs get-queue-url --queue-name "$DLQ" --output text --query QueueUrl)
DLQ_ARN=$(awslocal sqs get-queue-attributes --queue-url "$DLQ_URL" \
  --attribute-names QueueArn --output text --query "Attributes.QueueArn")

awslocal sqs create-queue --queue-name "$QUEUE" >/dev/null
QUEUE_URL=$(awslocal sqs get-queue-url --queue-name "$QUEUE" --output text --query QueueUrl)

# set-queue-attributes is last-write-wins — idempotent across re-runs.
# VisibilityTimeout 1800s = the 30-minute v0.1 plan value (implementation-plan §6).
awslocal sqs set-queue-attributes --queue-url "$QUEUE_URL" --attributes \
  "{\"VisibilityTimeout\":\"1800\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"

# The investigation workflow gets a new queue pair.  The legacy pair above
# deliberately remains byte-for-byte named and configured for rollback.
awslocal sqs create-queue --queue-name "$INVESTIGATION_DLQ" >/dev/null
INVESTIGATION_DLQ_URL=$(awslocal sqs get-queue-url \
  --queue-name "$INVESTIGATION_DLQ" --output text --query QueueUrl)
INVESTIGATION_DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url "$INVESTIGATION_DLQ_URL" --attribute-names QueueArn \
  --output text --query "Attributes.QueueArn")
awslocal sqs set-queue-attributes --queue-url "$INVESTIGATION_DLQ_URL" --attributes \
  "{\"MessageRetentionPeriod\":\"1209600\",\"SqsManagedSseEnabled\":\"true\"}"

awslocal sqs create-queue --queue-name "$INVESTIGATION_QUEUE" >/dev/null
INVESTIGATION_QUEUE_URL=$(awslocal sqs get-queue-url \
  --queue-name "$INVESTIGATION_QUEUE" --output text --query QueueUrl)
INVESTIGATION_QUEUE_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url "$INVESTIGATION_QUEUE_URL" --attribute-names QueueArn \
  --output text --query "Attributes.QueueArn")
awslocal sqs set-queue-attributes --queue-url "$INVESTIGATION_QUEUE_URL" --attributes \
  "{\"VisibilityTimeout\":\"1800\",\"MessageRetentionPeriod\":\"345600\",\"ReceiveMessageWaitTimeSeconds\":\"20\",\"SqsManagedSseEnabled\":\"true\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$INVESTIGATION_DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"
awslocal sqs set-queue-attributes --queue-url "$INVESTIGATION_DLQ_URL" --attributes \
  "{\"RedriveAllowPolicy\":\"{\\\"redrivePermission\\\":\\\"byQueue\\\",\\\"sourceQueueArns\\\":[\\\"$INVESTIGATION_QUEUE_ARN\\\"]}\"}"

echo "[g1-bootstrap] region=$REGION bucket=$BUCKET legacy_queue=$QUEUE investigation_queue=$INVESTIGATION_QUEUE redrive=maxReceiveCount:3 cors_origin=http://localhost:5173 retention_tiers=1..30"
