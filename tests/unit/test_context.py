"""Unit tests for the Context Resolver: sources, facts, expiry and resolve_context."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from haven.context.expiry import (
    FactVolatility,
    default_expiry_for,
    is_expired,
    volatility_for,
)
from haven.context.facts import FactStore, make_fact
from haven.context.resolver import (
    ClarificationNeeded,
    FactRequest,
    apply_user_clarification,
    resolve_context,
)
from haven.context.sources import (
    ConnectedServiceSource,
    ContextSourceType,
    DeviceStateSource,
    MemorySource,
    SensorSource,
    UserStatementSource,
)
from haven.models.context import DeviceCapability, PresenceState

LIVING_ROOM = "room-living-room"
TV = "device-tv-living-room"


def _device(context, device_id):
    return next(d for d in context.devices if d.id == device_id)


def _person(context, person_id):
    return next(p for p in context.people if p.id == person_id)


# --------------------------------------------------------------------------- #
# resolve_context: mapping the household snapshot onto the planning contract
# --------------------------------------------------------------------------- #


def test_maps_household_snapshot_onto_context(make_context):
    context = make_context("Get movie night ready")

    assert context.household_id == "household-001"
    assert len(context.people) == 3
    assert len(context.rooms) == 4
    assert len(context.devices) == 8
    assert len(context.media_options) == 5
    assert all(m.is_available for m in context.media_options)


@pytest.mark.parametrize(
    ("device_id", "capabilities"),
    [
        (TV, {DeviceCapability.MEDIA_PLAYBACK, DeviceCapability.DISPLAY}),
        ("device-lights-living-room", {DeviceCapability.LIGHTING}),
        ("device-thermostat-living-room", {DeviceCapability.THERMOSTAT}),
        ("device-lock-front-door", {DeviceCapability.LOCK}),
        ("device-security-system", {DeviceCapability.SECURITY}),
    ],
)
def test_device_types_map_to_capabilities(make_context, device_id, capabilities):
    device = _device(make_context("Get movie night ready"), device_id)

    assert set(device.capabilities) == capabilities


def test_presence_comes_from_the_household_by_default(make_context):
    context = make_context("Get movie night ready")

    assert _person(context, "person-alex").presence is PresenceState.PRESENT
    assert _person(context, "person-jamie").presence is PresenceState.AWAY


def test_unknown_presence_stays_unknown(household_sim, media_service, context_sources):
    household_sim.set_presence("person-alex", "unknown")

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        context_sources,
    )

    assert _person(context, "person-alex").presence is PresenceState.UNKNOWN


def test_user_statement_overrides_presence(household_sim, media_service, memory_service, calendar):
    sources = [
        UserStatementSource({"person_home:person-sam": False, "person_home:person-jamie": True}),
        MemorySource(memory_service.lookup_fact),
        ConnectedServiceSource(calendar.lookup_fact),
    ]

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        sources,
    )

    assert _person(context, "person-sam").presence is PresenceState.AWAY
    assert _person(context, "person-jamie").presence is PresenceState.PRESENT
    assert _person(context, "person-alex").presence is PresenceState.PRESENT


def test_offline_device_is_not_available(household_sim, media_service, context_sources):
    household_sim.set_device_state(TV, online=False)

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        context_sources,
    )

    assert _device(context, TV).is_available is False
    assert _device(context, "device-lights-living-room").is_available is True


# --------------------------------------------------------------------------- #
# resolve_context: facts gathered from approved sources
# --------------------------------------------------------------------------- #


def test_available_minutes_come_from_the_calendar(make_context):
    assert make_context("Get movie night ready").available_minutes == 120


def test_available_minutes_are_unknown_without_a_calendar(household_sim, media_service, memory_service):
    sources = [UserStatementSource(), MemorySource(memory_service.lookup_fact)]

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        sources,
    )

    assert context.available_minutes is None


def test_user_input_beats_the_calendar_by_default(household_sim, media_service, calendar):
    sources = [
        ConnectedServiceSource(calendar.lookup_fact),
        UserStatementSource({"available_time_minutes": 45}),
    ]

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        sources,
    )

    assert context.available_minutes == 45


def test_source_priority_can_be_overridden(household_sim, media_service, calendar):
    sources = [
        UserStatementSource({"available_time_minutes": 45}),
        ConnectedServiceSource(calendar.lookup_fact),
    ]

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        sources,
        source_priority=["connected_service", "user_input"],
    )

    assert context.available_minutes == 120


def test_non_numeric_available_minutes_are_ignored(household_sim, media_service):
    sources = [UserStatementSource({"available_time_minutes": "plenty"})]

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        sources,
    )

    assert context.available_minutes is None


def test_saved_preferences_flow_into_the_context(
    household_sim, media_service, memory_service, context_sources
):
    household_id = household_sim.get_snapshot().household_id
    memory_service.save_preference(household_id, "preferred_genres", ["sci-fi", "comedy"])
    memory_service.save_preference(household_id, "preferred_room", LIVING_ROOM)

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        context_sources,
    )

    assert context.preferred_genres == ["sci-fi", "comedy"]
    assert context.preferred_room_id == LIVING_ROOM


def test_preferred_room_must_exist_in_the_household(
    household_sim, media_service, memory_service, context_sources
):
    household_id = household_sim.get_snapshot().household_id
    memory_service.save_preference(household_id, "preferred_room", "room-that-does-not-exist")

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        context_sources,
    )

    assert context.preferred_room_id is None


def test_malformed_genre_preference_is_ignored(household_sim, media_service):
    sources = [UserStatementSource({"preference:preferred_genres": "sci-fi"})]

    context = resolve_context(
        "Get movie night ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        sources,
    )

    assert context.preferred_genres == []


# --------------------------------------------------------------------------- #
# resolve_context: clarification instead of guessing
# --------------------------------------------------------------------------- #


def test_required_unresolved_fact_asks_the_user(household_sim, media_service, context_sources):
    with pytest.raises(ClarificationNeeded) as raised:
        resolve_context(
            "Get the house ready",
            household_sim.get_snapshot(),
            media_service.list_media_items(),
            context_sources,
            extra_facts=[FactRequest("guest_count", required=True)],
        )

    assert raised.value.fact_name == "guest_count"
    assert "guest count" in raised.value.question


def test_optional_unresolved_fact_does_not_ask(household_sim, media_service, context_sources):
    context = resolve_context(
        "Get the house ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        context_sources,
        extra_facts=[FactRequest("guest_count", required=False)],
    )

    assert context.household_id == "household-001"


def test_required_fact_is_satisfied_by_a_user_statement(household_sim, media_service):
    context = resolve_context(
        "Get the house ready",
        household_sim.get_snapshot(),
        media_service.list_media_items(),
        [UserStatementSource({"guest_count": 3})],
        extra_facts=[FactRequest("guest_count", required=True)],
    )

    assert context.household_id == "household-001"


@pytest.mark.parametrize("goal", ["", "   ", "\n"])
def test_blank_goal_is_rejected(household, goal):
    with pytest.raises(ValueError, match="goal"):
        resolve_context(goal, household, [], [])


def test_non_household_is_rejected():
    with pytest.raises(ValueError, match="Household"):
        resolve_context("Get movie night ready", object(), [], [])  # type: ignore[arg-type]


def test_apply_user_clarification_records_a_clarification_fact():
    store = FactStore()

    apply_user_clarification("guest_count", 4, store)

    fact = store.get("guest_count")
    assert fact is not None
    assert fact.value == 4
    assert fact.source is ContextSourceType.CLARIFICATION
    assert fact.confidence == 1.0
    assert fact.expires_at is not None


# --------------------------------------------------------------------------- #
# Facts
# --------------------------------------------------------------------------- #


def test_make_fact_validates_its_inputs():
    with pytest.raises(ValueError, match="fact_name"):
        make_fact("  ", 1, ContextSourceType.USER_INPUT)
    with pytest.raises(ValueError, match="confidence"):
        make_fact("x", 1, ContextSourceType.USER_INPUT, confidence=1.5)
    with pytest.raises(ValueError, match="confidence"):
        make_fact("x", 1, ContextSourceType.USER_INPUT, confidence=-0.1)


def test_fact_store_tracks_gathered_facts():
    store = FactStore()
    store.add(make_fact("a", 1, ContextSourceType.USER_INPUT))
    store.add(make_fact("b", "two", ContextSourceType.MEMORY))

    assert store.has("a") and not store.has("c")
    assert store.get("b").value == "two"
    assert store.get("c") is None
    assert store.missing(["a", "b", "c"]) == ["c"]
    assert store.values() == {"a": 1, "b": "two"}
    assert [f.fact_name for f in store.as_list()] == ["a", "b"]


def test_adding_a_fact_again_replaces_it():
    store = FactStore()
    store.add(make_fact("a", 1, ContextSourceType.USER_INPUT))
    store.add(make_fact("a", 2, ContextSourceType.USER_INPUT))

    assert store.get("a").value == 2
    assert len(store.as_list()) == 1


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fact_name", "volatility"),
    [
        ("room_occupied", FactVolatility.TRANSIENT),
        ("person_home:person-alex", FactVolatility.TRANSIENT),
        ("available_time_minutes", FactVolatility.SESSION),
        ("preference:preferred_genres", FactVolatility.DURABLE),
        ("history:movie_night_prepared", FactVolatility.DURABLE),
        ("something_new", FactVolatility.TRANSIENT),
    ],
)
def test_volatility_by_fact_name(fact_name, volatility):
    assert volatility_for(fact_name) is volatility


def test_default_expiry_follows_volatility():
    start = datetime(2026, 1, 1, tzinfo=UTC)

    assert default_expiry_for("room_occupied", start) == start + timedelta(minutes=2)
    assert default_expiry_for("available_time_minutes", start) == start + timedelta(minutes=30)
    assert default_expiry_for("preference:x", start) > start + timedelta(days=365)


def test_is_expired():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fresh = make_fact("a", 1, ContextSourceType.SENSOR, expires_at=now + timedelta(minutes=1))
    stale = make_fact("a", 1, ContextSourceType.SENSOR, expires_at=now - timedelta(minutes=1))
    forever = make_fact("a", 1, ContextSourceType.MEMORY, expires_at=None)

    assert is_expired(fresh, now) is False
    assert is_expired(stale, now) is True
    assert is_expired(forever, now) is False


def test_a_fact_expires_exactly_at_its_expiry():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    assert is_expired(make_fact("a", 1, ContextSourceType.SENSOR, expires_at=now), now) is True


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


def test_user_statement_source():
    source = UserStatementSource({"available_time_minutes": 45})

    assert source.source_type is ContextSourceType.USER_INPUT
    assert source.supports("available_time_minutes")
    assert not source.supports("other")
    assert source.fetch("available_time_minutes") == 45
    assert source.fetch("other") is None


def test_device_state_source_needs_a_device_id(household_sim):
    source = DeviceStateSource(household_sim)

    assert source.supports("tv_state")
    assert not source.supports("room_occupied")
    assert source.fetch("tv_state") is None
    assert source.fetch("tv_state", device_id=TV)["device_id"] == TV
    assert source.fetch("tv_state", device_id="device-nope") is None


def test_sensor_source_reports_room_occupancy(household_sim):
    source = SensorSource(household_sim)
    household_sim.set_room_occupied(LIVING_ROOM, True)

    assert source.supports("room_occupied")
    assert source.fetch("room_occupied") is None
    assert source.fetch("room_occupied", room_id=LIVING_ROOM) is True
    assert source.fetch("room_occupied", room_id="room-kitchen") is False
    assert source.fetch("room_occupied", room_id="room-nope") is None


def test_memory_source_only_handles_preferences_and_history():
    lookups = []

    def lookup(name, **kwargs):
        lookups.append((name, kwargs))
        return "value"

    source = MemorySource(lookup)

    assert source.supports("preference:preferred_genres")
    assert source.supports("history:movie_night_prepared")
    assert not source.supports("available_time_minutes")
    assert source.fetch("preference:x", household_id="h") == "value"
    assert lookups == [("preference:x", {"household_id": "h"})]


def test_memory_source_without_a_lookup_answers_nothing():
    assert MemorySource().fetch("preference:x") is None


def test_connected_service_source_without_a_lookup_answers_nothing():
    source = ConnectedServiceSource()

    assert source.supports("available_time_minutes")
    assert source.fetch("available_time_minutes") is None
