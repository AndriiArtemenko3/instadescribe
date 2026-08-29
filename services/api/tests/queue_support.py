"""Shared test helper: run-owned namespaced queue pairs with the canonical
redrive shape. Used by both the API and worker suites; never touches the
development `instascribe-work` queue."""

import json


def make_queue_pair(client, base_name: str, visibility: str = "1800"):
    dlq_url = client.create_queue(QueueName=f"{base_name}-dlq")["QueueUrl"]
    dlq_arn = client.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    queue_url = client.create_queue(QueueName=base_name)["QueueUrl"]
    client.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={
            "VisibilityTimeout": visibility,
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}),
        },
    )
    attrs = client.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["VisibilityTimeout", "RedrivePolicy"]
    )["Attributes"]
    assert attrs["VisibilityTimeout"] == visibility
    redrive = json.loads(attrs["RedrivePolicy"])
    assert redrive["maxReceiveCount"] == "3"
    assert redrive["deadLetterTargetArn"] == dlq_arn
    return queue_url, dlq_url
