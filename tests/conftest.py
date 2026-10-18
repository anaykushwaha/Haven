from __future__ import annotations

import pytest

from haven.models.context import (
    DeviceCapability,
    DeviceContext,
    HouseholdContext,
    MediaItem,
    PersonContext,
    PresenceState,
    RoomContext,
)

# Shared simulator-backed fixtures (tests/fixtures/simulators.py), available
# to every test in the suite.
pytest_plugins = ["tests.fixtures.simulators"]


@pytest.fixture
def movie_night_context() -> HouseholdContext:
    """A hand-built HouseholdContext conforming to the shared contract."""

    return HouseholdContext(
        household_id="household-1",
        people=[
            PersonContext(id="p1", display_name="Ari", presence=PresenceState.PRESENT),
        ],
        rooms=[
            RoomContext(id="living-room", name="Living Room"),
            RoomContext(id="bedroom", name="Bedroom"),
        ],
        devices=[
            DeviceContext(
                id="tv-1",
                name="Living Room TV",
                room_id="living-room",
                capabilities=[DeviceCapability.MEDIA_PLAYBACK, DeviceCapability.DISPLAY],
            ),
            DeviceContext(
                id="lamp-1",
                name="Living Room Lamp",
                room_id="living-room",
                capabilities=[DeviceCapability.LIGHTING],
            ),
            DeviceContext(
                id="lock-1",
                name="Front Door Lock",
                room_id=None,
                capabilities=[DeviceCapability.LOCK],
            ),
        ],
        media_options=[
            MediaItem(id="m-long", title="Long Epic", duration_minutes=145),
            MediaItem(id="m-short", title="Short Feature", duration_minutes=95),
            MediaItem(
                id="m-gone", title="Unavailable Film", duration_minutes=80, is_available=False
            ),
        ],
        available_minutes=120,
        preferred_room_id="living-room",
    )
