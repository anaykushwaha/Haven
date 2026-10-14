"""boto3 DynamoDB client/resource construction for Haven."""

from __future__ import annotations

from typing import Any

import boto3

from aws.dynamodb.config import (
    DynamoDBConfig,
    load_dynamodb_config,
)


def create_dynamodb_resource(
    config: DynamoDBConfig | None = None,
) -> Any:
    """Create the DynamoDB resource used by Haven repositories.

    Credentials are NOT passed explicitly. boto3 resolves them through the
    normal AWS credential chain.
    """

    resolved = config or load_dynamodb_config()

    kwargs: dict[str, Any] = {
        "region_name": resolved.region,
    }

    if resolved.endpoint_url:
        kwargs["endpoint_url"] = resolved.endpoint_url

    return boto3.resource(
        "dynamodb",
        **kwargs,
    )


def create_dynamodb_client(
    config: DynamoDBConfig | None = None,
) -> Any:
    """Create the low-level DynamoDB client."""

    resolved = config or load_dynamodb_config()

    kwargs: dict[str, Any] = {
        "region_name": resolved.region,
    }

    if resolved.endpoint_url:
        kwargs["endpoint_url"] = resolved.endpoint_url

    return boto3.client(
        "dynamodb",
        **kwargs,
    )
