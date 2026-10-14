from __future__ import annotations

from datetime import UTC

import pytest

from haven.agents.planner import create_plan
from haven.memory.activity import ActivityType
from haven.memory.preferences import PreferenceKey, is_durable_preference_key
from haven.memory.service import MemoryService
from haven.models.context import HouseholdContext

HH = "household-1"


def test_save_and_read_preference() -> None:
    memory = MemoryService()
    saved = memory.save_preference(HH, PreferenceKey.PREFERRED_GENRES, ["comedy", "sci-fi"])
    assert saved.scope == "household"
    assert saved.updated_at.tzinfo is UTC

    read = memory.get_preference(HH, PreferenceKey.PREFERRED_GENRES)
    assert read is not None
    assert read.value == ["comedy", "sci-fi"]


def test_person_scoped_preference_is_separate_from_household() -> None:
    memory = MemoryService()
    memory.save_preference(HH, PreferenceKey.PREFERRED_ROOM, "living-room")
    memory.save_preference(HH, PreferenceKey.PREFERRED_ROOM, "bedroom", person_id="p1")

    assert memory.get_preference(HH, PreferenceKey.PREFERRED_ROOM).value == "living-room"
    assert (
        memory.get_preference(HH, PreferenceKey.PREFERRED_ROOM, person_id="p1").value == "bedroom"
    )
    assert [p.key for p in memory.get_preferences(HH)] == [PreferenceKey.PREFERRED_ROOM]


def test_overwrite_preference_intentionally() -> None:
    memory = MemoryService()
    memory.save_preference(HH, PreferenceKey.TEMPERATURE_CELSIUS, 21)
    memory.save_preference(HH, PreferenceKey.TEMPERATURE_CELSIUS, 19.5)

    assert memory.get_preference(HH, PreferenceKey.TEMPERATURE_CELSIUS).value == 19.5
    changes = memory.list_activity(HH, activity_type=ActivityType.PREFERENCE_CHANGED)
    assert len(changes) == 2


def test_delete_preference() -> None:
    memory = MemoryService()
    memory.save_preference(HH, "preferred_room", "kitchen")
    assert memory.delete_preference(HH, "preferred_room") is True
    assert memory.get_preference(HH, "preferred_room") is None
    assert memory.delete_preference(HH, "preferred_room") is False


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_ids_and_keys_rejected(bad: str) -> None:
    memory = MemoryService()
    with pytest.raises(ValueError):
        memory.save_preference(bad, "preferred_room", "x")
    with pytest.raises(ValueError):
        memory.save_preference(HH, bad, "x")
    with pytest.raises(ValueError):
        memory.get_preferences(bad)


def test_record_and_read_activity_newest_first() -> None:
    memory = MemoryService()
    memory.record_activity(HH, ActivityType.PLAN_PROPOSED, "first")
    memory.record_activity(HH, ActivityType.MOVIE_NIGHT_PREPARED, "second")
    memory.record_activity("other-household", ActivityType.PLAN_PROPOSED, "elsewhere")

    records = memory.list_activity(HH)
    assert [r.summary for r in records] == ["second", "first"]
    assert all(r.occurred_at.tzinfo is UTC for r in records)
    assert [r.summary for r in memory.list_activity(HH, limit=1)] == ["second"]


def test_record_plan_proposed_stores_metadata_only(movie_night_context: HouseholdContext) -> None:
    memory = MemoryService()
    plan = create_plan("Get movie night ready", movie_night_context)
    record = memory.record_plan_proposed(HH, plan)

    assert record.activity_type is ActivityType.PLAN_PROPOSED
    assert record.details["plan_id"] == plan.id
    assert record.details["step_count"] == 3
    # No household context or step descriptions are stored.
    assert "Living Room" not in record.model_dump_json()


# --- Person B MemorySource callback shape ------------------------------------ #


def test_lookup_fact_matches_memory_source_conventions() -> None:
    memory = MemoryService(default_household_id=HH)
    memory.save_preference(HH, PreferenceKey.PREFERRED_GENRES, ["comedy"])
    memory.record_activity(HH, ActivityType.MOVIE_NIGHT_PREPARED, "Movie night prepared")

    assert memory.lookup_fact("preference:preferred_genres") == ["comedy"]
    assert memory.lookup_fact("preference:preferred_genres", household_id=HH) == ["comedy"]
    assert memory.lookup_fact("preference:unknown_key") is None

    history = memory.lookup_fact("history:movie_night_prepared")
    assert history is not None and history[0].summary == "Movie night prepared"
    assert memory.lookup_fact("history:plan_proposed") is None
    assert memory.lookup_fact("history:not_a_type") is None


def test_lookup_fact_without_household_returns_none() -> None:
    memory = MemoryService()
    assert memory.lookup_fact("preference:preferred_genres") is None


def test_lookup_fact_is_callable_like_person_b_expects() -> None:
    # MemorySource(memory_lookup=...) simply calls memory_lookup(fact_name, **kwargs).
    memory = MemoryService(default_household_id=HH)
    memory.save_preference(HH, "preferred_room", "living-room")
    lookup = memory.lookup_fact
    assert lookup("preference:preferred_room", room_id="ignored") == "living-room"


# --- Privacy: temporary state is not durable memory --------------------------- #


@pytest.mark.parametrize(
    "transient", ["room_occupied", "presence", "motion_detected", "door_open", "lock_state"]
)
def test_transient_state_cannot_be_saved_as_preference(transient: str) -> None:
    memory = MemoryService()
    assert not is_durable_preference_key(transient)
    with pytest.raises(ValueError, match="temporary state"):
        memory.save_preference(HH, transient, True)
    assert memory.get_preferences(HH) == []
    assert memory.list_activity(HH) == []


def test_lookup_fact_ignores_transient_fact_names() -> None:
    memory = MemoryService(default_household_id=HH)
    assert memory.lookup_fact("room_occupied", room_id="living-room") is None
    assert memory.lookup_fact("device_state", device_id="tv-1") is None
