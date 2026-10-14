"""DynamoDb configration
I'm leaving the credentials out for now
boto3'll resolve them via aws credential chain"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_REGION = "us-west-2"

DEFAULT_PREFERENCES_TABLE = "HavenPreferences"
DEFAULT_ACTIVITIES_TABLE = "HavenActivities"
DEFAULT_PLANS_TABLE = "HavenPlans"

ENV_REGION = "AWS_REGION"
ENV_REGION_FALLBACK = "AWS_DEFAULT_REGION"

ENV_ENDPOINT_URL = "HAVEN_DYNAMODB_ENDPOINT_URL"

ENV_PREFERENCES_TABLE = "HAVEN_DYNAMODB_PREFERENCES_TABLE"
ENV_ACTIVITIES_TABLE = "HAVEN_DYNAMODB_ACTIVITIES_TABLE"
ENV_PLANS_TABLE = "HAVEN_DYNAMODB_PLANS_TABLE"

_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")


@dataclass(frozen=True, slots=True)
class DynamoDBConfig:
    """Connection and table-name configuration the storage"""

    region: str = DEFAULT_REGION

    preferences_table: str = DEFAULT_PREFERENCES_TABLE
    activities_table: str = DEFAULT_ACTIVITIES_TABLE
    plans_table: str = DEFAULT_PLANS_TABLE

    endpoint_url: str | None = None

    def __post_init__(self) -> None:
        region = self.region.strip()

        if not region:
            raise ValueError("DynamoDB region must not be empty")

        object.__setattr__(self, "region", region)

        for field_name in (
            "preferences_table",
            "activities_table",
            "plans_table",
        ):
            value = getattr(self, field_name).strip()

            if not _TABLE_NAME_RE.fullmatch(value):
                raise ValueError(
                    f"{field_name} must be 3-255 characters using only letters, numbers, _, -, or ."
                )

            object.__setattr__(self, field_name, value)

        endpoint = self.endpoint_url.strip() if self.endpoint_url else None
        object.__setattr__(self, "endpoint_url", endpoint or None)


def load_dynamodb_config(
    environ: Mapping[str, str] | None = None,
) -> DynamoDBConfig:
    """Build validated DynamoDB configuration from environment variables."""

    env = os.environ if environ is None else environ

    region = _text(env, ENV_REGION) or _text(env, ENV_REGION_FALLBACK) or DEFAULT_REGION

    return DynamoDBConfig(
        region=region,
        preferences_table=(_text(env, ENV_PREFERENCES_TABLE) or DEFAULT_PREFERENCES_TABLE),
        activities_table=(_text(env, ENV_ACTIVITIES_TABLE) or DEFAULT_ACTIVITIES_TABLE),
        plans_table=(_text(env, ENV_PLANS_TABLE) or DEFAULT_PLANS_TABLE),
        endpoint_url=_text(env, ENV_ENDPOINT_URL) or None,
    )


def _text(
    env: Mapping[str, str],
    name: str,
) -> str:
    return (env.get(name) or "").strip()
