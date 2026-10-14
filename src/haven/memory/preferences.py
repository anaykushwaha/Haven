"""Durable household and person preferences.

Preferences are information a person explicitly wants Haven to remember
(preferred genres, preferred room, temperature). Temporary observations
(presence, occupancy, door state) are *context*, not memory, and are
rejected here so they can never become long-lived records.

Storage is behind `PreferenceStore`; Phase 2 ships an in-memory store and
Phase 3 adds a DynamoDB-backed one with the same interface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

PreferenceValue = str | int | float | bool | list[str]


class PreferenceKey(StrEnum):
    """Well-known preference keys used by planning and context."""

    PREFERRED_GENRES = "preferred_genres"
    PREFERRED_ROOM = "preferred_room"
    TEMPERATURE_CELSIUS = "temperature_celsius"
    PREFERRED_MEDIA_DEVICE = "preferred_media_device"
    QUIET_HOURS = "quiet_hours"


# Fact names describing the household's *current* state. These describe a
# moment, not a preference, and must never be stored as durable memory.
TRANSIENT_KEYS: frozenset[str] = frozenset(
    {
        "presence",
        "room_occupied",
        "occupied",
        "motion",
        "motion_detected",
        "door_open",
        "door_state",
        "lock_state",
        "device_state",
        "tv_state",
        "security_armed",
        "people_home",
        "occupancy",
        "at_home",
        "is_home",
        "location",
        "last_seen",
        "arrived",
        "left_home",
        "window_open",
        "window_state",
        "alarm_state",
        "context_snapshot",
        "transcript",
    }
)


def is_durable_preference_key(key: str) -> bool:
    """Return True when ``key`` may be stored as a durable preference."""

    normalized = key.strip().lower()
    if not normalized:
        return False
    return normalized not in TRANSIENT_KEYS


class Preference(BaseModel):
    """A single remembered preference scoped to a household or a person."""

    model_config = ConfigDict(extra="forbid")

    household_id: str
    key: str
    value: PreferenceValue
    person_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def scope(self) -> str:
        return "person" if self.person_id else "household"


class PreferenceStore(Protocol):
    """Storage contract for preferences (in-memory now, DynamoDB later)."""

    def save(self, preference: Preference) -> Preference: ...

    def get(
        self, household_id: str, key: str, person_id: str | None = None
    ) -> Preference | None: ...

    def list(self, household_id: str, person_id: str | None = None) -> list[Preference]: ...

    def delete(self, household_id: str, key: str, person_id: str | None = None) -> bool: ...


class InMemoryPreferenceStore:
    """Process-local `PreferenceStore`. Not shared across processes."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str | None, str], Preference] = {}

    @staticmethod
    def _key(household_id: str, person_id: str | None, key: str) -> tuple[str, str | None, str]:
        return (household_id, person_id, key)

    def save(self, preference: Preference) -> Preference:
        self._items[self._key(preference.household_id, preference.person_id, preference.key)] = (
            preference
        )
        return preference

    def get(self, household_id: str, key: str, person_id: str | None = None) -> Preference | None:
        return self._items.get(self._key(household_id, person_id, key))

    def list(self, household_id: str, person_id: str | None = None) -> list[Preference]:
        return sorted(
            (
                item
                for (hid, pid, _), item in self._items.items()
                if hid == household_id and pid == person_id
            ),
            key=lambda item: item.key,
        )

    def delete(self, household_id: str, key: str, person_id: str | None = None) -> bool:
        return self._items.pop(self._key(household_id, person_id, key), None) is not None
