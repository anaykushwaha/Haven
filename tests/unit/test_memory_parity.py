"""Privacy-aware memory behaves identically on the in-memory and DynamoDB backends.

The DynamoDB backend runs against `tests.fakes.dynamodb.FakeTable`, so these
tests are offline but exercise the real repository item mapping.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from aws.dynamodb.config import DynamoDBConfig
from aws.dynamodb.repositories import create_dynamodb_repositories
from haven.agents.planner import create_plan
from haven.memory.activity import ActivityType
from haven.memory.preferences import TRANSIENT_KEYS, PreferenceKey
from haven.memory.service import MemoryService
from haven.models.context import HouseholdContext
from tests.fakes.dynamodb import FakeDynamoDBResource, FakeTable, haven_tables

HH = "household-parity"


def _memory_backend() -> tuple[MemoryService, None]:
    return MemoryService(default_household_id=HH), None


def _dynamodb_backend() -> tuple[MemoryService, tuple[FakeTable, FakeTable, FakeTable]]:
    tables = haven_tables()
    repos = create_dynamodb_repositories(
        DynamoDBConfig(
            region="us-west-2",
            preferences_table=tables[0].name,
            activities_table=tables[1].name,
            plans_table=tables[2].name,
        ),
        dynamodb=FakeDynamoDBResource(tables),
    )
    service = MemoryService(
        preferences=repos.preferences, activity=repos.activity, default_household_id=HH
    )
    return service, tables


BACKENDS: dict[str, Callable[[], tuple[MemoryService, object]]] = {
    "memory": _memory_backend,
    "dynamodb": _dynamodb_backend,
}


@pytest.fixture(params=sorted(BACKENDS))
def backend(request: pytest.FixtureRequest) -> tuple[str, MemoryService, object]:
    service, extra = BACKENDS[request.param]()
    return request.param, service, extra


def test_explicit_preferences_round_trip(backend: tuple[str, MemoryService, object]) -> None:
    _, memory, _ = backend
    memory.save_preference(HH, PreferenceKey.PREFERRED_GENRES, ["comedy", "sci-fi"])
    memory.save_preference(HH, PreferenceKey.PREFERRED_ROOM, "bedroom", person_id="p1")

    assert memory.get_preference(HH, PreferenceKey.PREFERRED_GENRES).value == ["comedy", "sci-fi"]
    assert memory.get_preference(HH, PreferenceKey.PREFERRED_ROOM) is None
    assert memory.get_preference(HH, PreferenceKey.PREFERRED_ROOM, person_id="p1").value == (
        "bedroom"
    )
    assert memory.lookup_fact("preference:preferred_genres") == ["comedy", "sci-fi"]


def test_overwrite_is_intentional_and_audited(backend: tuple[str, MemoryService, object]) -> None:
    _, memory, _ = backend
    memory.save_preference(HH, PreferenceKey.TEMPERATURE_CELSIUS, 21)
    memory.save_preference(HH, PreferenceKey.TEMPERATURE_CELSIUS, 19)
    assert memory.get_preference(HH, PreferenceKey.TEMPERATURE_CELSIUS).value == 19
    assert len(memory.list_activity(HH, activity_type=ActivityType.PREFERENCE_CHANGED)) == 2


@pytest.mark.parametrize("transient", sorted(TRANSIENT_KEYS))
def test_transient_state_is_never_persisted_as_preference(
    backend: tuple[str, MemoryService, object], transient: str
) -> None:
    name, memory, tables = backend
    with pytest.raises(ValueError, match="temporary state"):
        memory.save_preference(HH, transient, True)
    with pytest.raises(ValueError, match="temporary state"):
        memory.save_preference(HH, transient.upper(), "x", person_id="p1")

    assert memory.get_preferences(HH) == []
    assert memory.list_activity(HH) == []
    if name == "dynamodb":
        # The rejection happens before any write reaches the table.
        assert all(not table.items for table in tables)
        assert all("put_item" not in table.ops() for table in tables)


@pytest.mark.parametrize("transient", ["presence", "room_occupied", "door_state", "people_home"])
def test_transient_state_is_never_persisted_in_activity_details(
    backend: tuple[str, MemoryService, object], transient: str
) -> None:
    name, memory, tables = backend
    with pytest.raises(ValueError, match="temporary state"):
        memory.record_activity(
            HH, ActivityType.MOVIE_NIGHT_PREPARED, "prepared", details={transient: True}
        )
    assert memory.list_activity(HH) == []
    if name == "dynamodb":
        assert not tables[1].items


def test_plan_history_stores_metadata_not_context(
    backend: tuple[str, MemoryService, object], movie_night_context: HouseholdContext
) -> None:
    name, memory, tables = backend
    plan = create_plan("Get movie night ready", movie_night_context)
    record = memory.record_plan_proposed(HH, plan)

    assert record is not None
    assert record.details == {
        "plan_id": plan.id,
        "workflow": "movie_night",
        "step_count": 3,
        "requires_confirmation": False,
    }
    history = memory.lookup_fact("history:plan_proposed")
    assert history and history[0].details["plan_id"] == plan.id

    if name == "dynamodb":
        stored = next(iter(tables[1].items.values()))
        blob = str(stored)
        # No people, presence, device state or step text leaks into the row.
        for forbidden in ("Ari", "PRESENT", "Living Room", "tv-1", "lamp-1", "dim_lights"):
            assert forbidden not in blob


def test_clarification_plans_are_not_history(
    backend: tuple[str, MemoryService, object], movie_night_context: HouseholdContext
) -> None:
    _, memory, _ = backend
    ctx = movie_night_context.model_copy(update={"devices": []})
    plan = create_plan("Get movie night ready", ctx)
    assert plan.clarification_needed
    assert memory.record_plan_proposed(HH, plan) is None
    assert memory.list_activity(HH) == []


def test_lookup_fact_ignores_transient_fact_names(
    backend: tuple[str, MemoryService, object],
) -> None:
    _, memory, _ = backend
    for transient in sorted(TRANSIENT_KEYS):
        assert memory.lookup_fact(transient, room_id="living-room") is None
        assert memory.lookup_fact(f"preference:{transient}") is None
