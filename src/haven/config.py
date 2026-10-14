"""Runtime configuration for Haven planning and persistence.

Backend selection is explicit and deterministic:

    HAVEN_PLANNER_BACKEND=mock
        Offline deterministic reasoner (default).

    HAVEN_PLANNER_BACKEND=bedrock
        Strands Agent -> Amazon Bedrock.

Persistence selection is also explicit:

    HAVEN_PERSISTENCE_BACKEND=memory
        Local in-memory persistence (default).

    HAVEN_PERSISTENCE_BACKEND=dynamodb
        Amazon DynamoDB-backed persistence.

Haven never switches to Bedrock merely because AWS credentials exist, and it
never silently falls back to the mock planner when Bedrock is selected and
fails.

Likewise, Haven does not automatically use DynamoDB merely because AWS
credentials are available.

Bedrock-specific connection settings live in ``aws.bedrock.config``.
DynamoDB-specific connection settings live in ``aws.dynamodb.config``.

This module contains no AWS credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Planner backends
# ---------------------------------------------------------------------------

MOCK_PLANNER_BACKEND = "mock"
BEDROCK_PLANNER_BACKEND = "bedrock"

SUPPORTED_PLANNER_BACKENDS: frozenset[str] = frozenset(
    {
        MOCK_PLANNER_BACKEND,
        BEDROCK_PLANNER_BACKEND,
    }
)


# ---------------------------------------------------------------------------
# Persistence backends
# ---------------------------------------------------------------------------

MEMORY_PERSISTENCE_BACKEND = "memory"
DYNAMODB_PERSISTENCE_BACKEND = "dynamodb"

SUPPORTED_PERSISTENCE_BACKENDS: frozenset[str] = frozenset(
    {
        MEMORY_PERSISTENCE_BACKEND,
        DYNAMODB_PERSISTENCE_BACKEND,
    }
)


# ---------------------------------------------------------------------------
# Environment variable names
# ---------------------------------------------------------------------------

ENV_PLANNER_BACKEND = "HAVEN_PLANNER_BACKEND"
ENV_PERSISTENCE_BACKEND = "HAVEN_PERSISTENCE_BACKEND"
ENV_MAX_PLAN_STEPS = "HAVEN_MAX_PLAN_STEPS"
ENV_LOG_LEVEL = "HAVEN_LOG_LEVEL"


# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HavenConfig:
    """Process-level runtime configuration for Haven.

    This configuration controls application behavior only.

    AWS service-specific settings such as Bedrock model IDs, DynamoDB table
    names, regions, and endpoints are owned by their respective AWS config
    modules.

    No cloud credentials are stored here.
    """

    planner_backend: str = MOCK_PLANNER_BACKEND
    persistence_backend: str = MEMORY_PERSISTENCE_BACKEND
    max_plan_steps: int = 12
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "planner_backend",
            normalize_backend(self.planner_backend),
        )

        object.__setattr__(
            self,
            "persistence_backend",
            normalize_persistence_backend(self.persistence_backend),
        )

        if self.max_plan_steps < 1:
            raise ValueError(f"{ENV_MAX_PLAN_STEPS} must be a positive integer")

        normalized_log_level = self.log_level.strip().upper() if self.log_level else "INFO"

        object.__setattr__(
            self,
            "log_level",
            normalized_log_level,
        )


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def normalize_backend(value: str) -> str:
    """Return a canonical planner backend name.

    Raises:
        ValueError: If the requested backend is unsupported.
    """

    backend = (value or "").strip().lower()

    if backend not in SUPPORTED_PLANNER_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_PLANNER_BACKENDS))

        raise ValueError(f"unsupported planner backend {value!r}; expected one of: {supported}")

    return backend


def normalize_persistence_backend(
    value: str,
) -> str:
    """Return a canonical persistence backend name.

    Raises:
        ValueError: If the requested backend is unsupported.
    """

    backend = (value or "").strip().lower()

    if backend not in SUPPORTED_PERSISTENCE_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_PERSISTENCE_BACKENDS))

        raise ValueError(f"unsupported persistence backend {value!r}; expected one of: {supported}")

    return backend


# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------


def get_config() -> HavenConfig:
    """Load Haven runtime configuration from environment variables."""

    planner_backend = (
        os.getenv(
            ENV_PLANNER_BACKEND,
            MOCK_PLANNER_BACKEND,
        ).strip()
        or MOCK_PLANNER_BACKEND
    )

    persistence_backend = (
        os.getenv(
            ENV_PERSISTENCE_BACKEND,
            MEMORY_PERSISTENCE_BACKEND,
        ).strip()
        or MEMORY_PERSISTENCE_BACKEND
    )

    raw_max_steps = os.getenv(
        ENV_MAX_PLAN_STEPS,
        "",
    ).strip()

    raw_log_level = os.getenv(
        ENV_LOG_LEVEL,
        "",
    ).strip()

    if raw_max_steps:
        try:
            max_plan_steps = int(raw_max_steps)

        except ValueError as exc:
            raise ValueError(f"{ENV_MAX_PLAN_STEPS} must be an integer") from exc

    else:
        max_plan_steps = 12

    return HavenConfig(
        planner_backend=planner_backend,
        persistence_backend=persistence_backend,
        max_plan_steps=max_plan_steps,
        log_level=raw_log_level or "INFO",
    )
