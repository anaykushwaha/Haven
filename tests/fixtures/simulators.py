"""Simulator-backed pytest fixtures, registered suite-wide by tests/conftest.py."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest

from haven.config import HavenConfig
from haven.context.resolver import resolve_context
from haven.context.sources import (
    ConnectedServiceSource,
    ContextSource,
    DeviceStateSource,
    MemorySource,
    SensorSource,
    UserStatementSource,
)
from haven.integrations.calendar import CalendarService, demo_calendar
from haven.integrations.media import MediaService
from haven.mcp.server import AppContext, build_app_context, reset_app_context
from haven.memory.service import MemoryService
from haven.models.context import HouseholdContext
from haven.models.household import Household
from haven.models.plan import Plan
from haven.planning.plan_service import create_household_plan
from simulator.household.service import HouseholdSimulatorService
from simulator.media.service import MediaSimulatorService

# A fixed "now" so every test sees the same free window (a 120-minute gap
# before the demo calendar's first event) no matter when the suite runs.
FIXED_NOW = datetime(2026, 1, 16, 19, 0, tzinfo=UTC)


@pytest.fixture
def mock_config() -> HavenConfig:
    """Offline configuration: mock planner, in-memory persistence."""

    return HavenConfig(planner_backend="mock", persistence_backend="memory")


@pytest.fixture
def household_sim() -> HouseholdSimulatorService:
    """A fresh household simulator (people, rooms, and every device)."""

    return HouseholdSimulatorService()


@pytest.fixture
def media_sim() -> MediaSimulatorService:
    return MediaSimulatorService()


@pytest.fixture
def media_service(media_sim: MediaSimulatorService) -> MediaService:
    return MediaService(media_sim)


@pytest.fixture
def memory_service(household_sim: HouseholdSimulatorService) -> MemoryService:
    household_id = household_sim.get_snapshot().household_id
    return MemoryService(default_household_id=household_id)


@pytest.fixture
def calendar() -> CalendarService:
    return demo_calendar(FIXED_NOW, clock=lambda: FIXED_NOW)


@pytest.fixture
def household(household_sim: HouseholdSimulatorService) -> Household:
    """The simulator's current domain snapshot."""

    return household_sim.get_snapshot()


@pytest.fixture
def context_sources(
    household_sim: HouseholdSimulatorService,
    memory_service: MemoryService,
    calendar: CalendarService,
) -> list[ContextSource]:
    """The five approved context sources, wired to this test's simulators."""

    return [
        UserStatementSource(),
        DeviceStateSource(household_sim),
        SensorSource(household_sim),
        MemorySource(memory_service.lookup_fact),
        ConnectedServiceSource(calendar.lookup_fact),
    ]


@pytest.fixture
def make_context(
    household_sim: HouseholdSimulatorService,
    media_service: MediaService,
    context_sources: list[ContextSource],
) -> Callable[[str], HouseholdContext]:
    """Resolve a fresh `HouseholdContext` from the simulators for a goal."""

    def _make(goal: str) -> HouseholdContext:
        return resolve_context(
            goal,
            household_sim.get_snapshot(),
            media_service.list_media_items(),
            context_sources,
        )

    return _make


@pytest.fixture
def plan_for(
    make_context: Callable[[str], HouseholdContext], mock_config: HavenConfig
) -> Callable[[str], Plan]:
    """Resolve context and plan a goal with the offline mock planner."""

    def _plan(goal: str) -> Plan:
        return create_household_plan(goal, make_context(goal), config=mock_config)

    return _plan


@pytest.fixture
def app_context(mock_config: HavenConfig) -> Iterator[AppContext]:
    """An isolated MCP `AppContext`, as the server builds it.

    The server caches one process-wide context; this resets it before and
    after so MCP tests never see another test's plans or saved preferences.
    """

    reset_app_context()
    yield build_app_context(mock_config)
    reset_app_context()
