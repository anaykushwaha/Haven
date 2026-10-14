"""Haven DynamoDB table definitions and provisioning helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from aws.dynamodb.client import create_dynamodb_resource
from aws.dynamodb.config import (
    DynamoDBConfig,
    load_dynamodb_config,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TableDefinition:
    name: str
    key_schema: tuple[dict[str, str], ...]
    attribute_definitions: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class HavenTables:
    preferences: Any
    activities: Any
    plans: Any


def table_definitions(
    config: DynamoDBConfig,
) -> tuple[TableDefinition, ...]:
    """Return Haven's three Phase 3 table schemas."""

    return (
        TableDefinition(
            name=config.preferences_table,
            key_schema=(
                {
                    "AttributeName": "household_id",
                    "KeyType": "HASH",
                },
                {
                    "AttributeName": "preference_id",
                    "KeyType": "RANGE",
                },
            ),
            attribute_definitions=(
                {
                    "AttributeName": "household_id",
                    "AttributeType": "S",
                },
                {
                    "AttributeName": "preference_id",
                    "AttributeType": "S",
                },
            ),
        ),
        TableDefinition(
            name=config.activities_table,
            key_schema=(
                {
                    "AttributeName": "household_id",
                    "KeyType": "HASH",
                },
                {
                    "AttributeName": "activity_key",
                    "KeyType": "RANGE",
                },
            ),
            attribute_definitions=(
                {
                    "AttributeName": "household_id",
                    "AttributeType": "S",
                },
                {
                    "AttributeName": "activity_key",
                    "AttributeType": "S",
                },
            ),
        ),
        TableDefinition(
            name=config.plans_table,
            key_schema=(
                {
                    "AttributeName": "plan_id",
                    "KeyType": "HASH",
                },
            ),
            attribute_definitions=(
                {
                    "AttributeName": "plan_id",
                    "AttributeType": "S",
                },
            ),
        ),
    )


def ensure_tables(
    config: DynamoDBConfig | None = None,
    *,
    dynamodb: Any | None = None,
) -> HavenTables:
    """Create missing Haven tables and wait until they exist.

    Existing tables are left untouched.
    """

    resolved = config or load_dynamodb_config()

    resource = dynamodb or create_dynamodb_resource(resolved)

    for definition in table_definitions(resolved):
        table = resource.Table(definition.name)

        if _table_exists(table):
            logger.info(
                "DynamoDB table already exists: %s",
                definition.name,
            )
            continue

        logger.info(
            "Creating DynamoDB table: %s",
            definition.name,
        )

        table = resource.create_table(
            TableName=definition.name,
            KeySchema=list(definition.key_schema),
            AttributeDefinitions=list(definition.attribute_definitions),
            BillingMode="PAY_PER_REQUEST",
        )

        table.wait_until_exists()
        table.load()

        logger.info(
            "DynamoDB table is active: %s",
            definition.name,
        )

    return HavenTables(
        preferences=resource.Table(resolved.preferences_table),
        activities=resource.Table(resolved.activities_table),
        plans=resource.Table(resolved.plans_table),
    )


def get_tables(
    config: DynamoDBConfig | None = None,
    *,
    dynamodb: Any | None = None,
) -> HavenTables:
    """Return Haven table handles without creating anything."""

    resolved = config or load_dynamodb_config()

    resource = dynamodb or create_dynamodb_resource(resolved)

    return HavenTables(
        preferences=resource.Table(resolved.preferences_table),
        activities=resource.Table(resolved.activities_table),
        plans=resource.Table(resolved.plans_table),
    )


def _table_exists(table: Any) -> bool:
    try:
        table.load()

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code == "ResourceNotFoundException":
            return False

        raise

    return True
