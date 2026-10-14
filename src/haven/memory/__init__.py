"""Haven durable memory: explicit preferences and activity history.

Storage implementations are swappable via the `PreferenceStore` and
`ActivityStore` protocols; `create_memory_service` picks the backend from
``HAVEN_PERSISTENCE_BACKEND`` (``memory`` or ``dynamodb``).
"""

from haven.memory.activity import (
    ActivityRecord,
    ActivityStore,
    ActivityType,
    InMemoryActivityStore,
)
from haven.memory.factory import create_memory_service
from haven.memory.preferences import (
    InMemoryPreferenceStore,
    Preference,
    PreferenceKey,
    PreferenceStore,
    is_durable_preference_key,
)
from haven.memory.service import MemoryService

__all__ = [
    "ActivityRecord",
    "ActivityStore",
    "ActivityType",
    "InMemoryActivityStore",
    "InMemoryPreferenceStore",
    "MemoryService",
    "Preference",
    "PreferenceKey",
    "PreferenceStore",
    "create_memory_service",
    "is_durable_preference_key",
]
