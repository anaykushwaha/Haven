"""Storage backend selection for Haven durable memory."""

from __future__ import annotations

from haven.config import (
    DYNAMODB_PERSISTENCE_BACKEND,
    MEMORY_PERSISTENCE_BACKEND,
    HavenConfig,
    get_config,
)
from haven.memory.service import MemoryService


def create_memory_service(
    config: HavenConfig | None = None,
    *,
    default_household_id: str | None = None,
) -> MemoryService:
    """Create MemoryService using the selected persistence backend."""

    resolved = config or get_config()

    if resolved.persistence_backend == MEMORY_PERSISTENCE_BACKEND:
        return MemoryService(default_household_id=(default_household_id))

    if resolved.persistence_backend == DYNAMODB_PERSISTENCE_BACKEND:
        # Lazy import intentionally keeps
        # local mode independent of AWS.
        from aws.dynamodb.repositories import (
            create_dynamodb_repositories,
        )

        repositories = create_dynamodb_repositories()

        return MemoryService(
            preferences=(repositories.preferences),
            activity=(repositories.activity),
            default_household_id=(default_household_id),
        )

    raise ValueError(f"unsupported persistence backend {resolved.persistence_backend!r}")
