"""Workflow-isolated SQS publishers.

Uses the container-internal endpoint; queue URLs and credentials never appear
in responses or logs. The legacy audio-description queue remains unchanged;
video investigations publish to a separate queue consumed only by a local
provider worker.
"""

from functools import lru_cache

import boto3
from instadescribe_contracts.queue import QueueMessage

from app.core.config import get_settings


@lru_cache
def _sqs_client():
    settings = get_settings()
    return boto3.client(
        "sqs",
        region_name=settings.aws_region,
        endpoint_url=settings.sqs_endpoint_internal,
    )


@lru_cache
def work_queue_url() -> str:
    settings = get_settings()
    if settings.work_queue_url:
        return settings.work_queue_url
    return _sqs_client().get_queue_url(QueueName=settings.work_queue_name)["QueueUrl"]


@lru_cache
def investigation_queue_url() -> str:
    settings = get_settings()
    if settings.investigation_queue_url:
        return settings.investigation_queue_url
    return _sqs_client().get_queue_url(QueueName=settings.investigation_queue_name)["QueueUrl"]


def reset_sqs_caches() -> None:
    """Test hook: drop cached client/URL after env changes."""
    _sqs_client.cache_clear()
    work_queue_url.cache_clear()
    investigation_queue_url.cache_clear()


def send_task_message(message: QueueMessage) -> None:
    """Publish an audio-description task to the unchanged legacy queue."""

    _sqs_client().send_message(QueueUrl=work_queue_url(), MessageBody=message.to_body())


def send_investigation_task_message(message: QueueMessage) -> None:
    """Publish a video-investigation task to its isolated local-worker queue."""

    _sqs_client().send_message(
        QueueUrl=investigation_queue_url(),
        MessageBody=message.to_body(),
    )
