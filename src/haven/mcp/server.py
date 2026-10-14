"""Haven MCP server.

    Alexa+  --MCP (Streamable HTTP)-->  Haven MCP Server  --calls-->  Haven
        (context resolution / planning / execution / verification / memory)

This module wires the transport and shared runtime state together. The
actual tool logic lives in `haven.mcp.tools.*` as plain,
dependency-injected functions that don't import the `mcp` package at all
(see the module docstrings there) — this file's `register(mcp)` calls are
the only place those functions get wrapped as `@mcp.tool()`.

The server runs Haven's own simulators for household/media state. Durable
memory comes from `haven.memory.factory.create_memory_service`, which picks
in-memory or DynamoDB storage from ``HAVEN_PERSISTENCE_BACKEND``; the
planner backend is likewise chosen by ``HAVEN_PLANNER_BACKEND``. With the
defaults (``memory`` / ``mock``) nothing here touches AWS.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mcp.server.fastmcp import Context, FastMCP

from haven.config import HavenConfig, get_config
from haven.execution.results import ExecutionResult
from haven.integrations.calendar import CalendarService, demo_calendar
from haven.integrations.media import MediaService
from haven.memory.factory import create_memory_service
from haven.memory.service import MemoryService
from haven.models.plan import Plan
from haven.verification.verifier import VerificationResult
from simulator.household.service import HouseholdSimulatorService
from simulator.media.service import MediaSimulatorService

logger = logging.getLogger(__name__)

SERVER_NAME = "haven"
SERVER_INSTRUCTIONS = (
    "Haven is a household agent. Ask for the current household context, "
    "propose a plan for a goal like 'get movie night ready', execute a "
    "previously proposed plan (confirming when it includes a high-impact "
    "step), save a durable household preference, or ask what to do "
    "tonight for a quick recommendation."
)


@dataclass
class StoredPlan:
    """A previously proposed plan, plus whatever has happened to it since."""

    plan: Plan
    household_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    execution: ExecutionResult | None = None
    verification: VerificationResult | None = None


class PlanStore:
    """Process-local registry of plans proposed through this MCP server.

    Scoped to the server process — a real deployment would back this with
    `aws.dynamodb`, the same way `MemoryService` will, so a plan survives
    a restart and can be looked up across server instances. That's future
    work; nothing in `haven.mcp.tools` depends on this being in-memory.
    """

    def __init__(self) -> None:
        self._plans: dict[str, StoredPlan] = {}

    def save(self, plan: Plan, household_id: str) -> StoredPlan:
        stored = StoredPlan(plan=plan, household_id=household_id)
        self._plans[plan.id] = stored
        return stored

    def get(self, plan_id: str) -> StoredPlan | None:
        return self._plans.get(plan_id)

    def record_execution(
        self,
        plan_id: str,
        execution: ExecutionResult,
        verification: VerificationResult,
    ) -> StoredPlan:
        stored = self._plans.get(plan_id)
        if stored is None:
            raise KeyError(f"Unknown plan_id: {plan_id}")
        stored.execution = execution
        stored.verification = verification
        return stored


@dataclass
class AppContext:
    """Shared runtime state every MCP tool call can reach.

    `household_id` is fixed for this Phase 3 server — Haven runs against
    one demo household per process, matching the simulators. Routing a
    real Alexa+ account/session to a specific household is future work
    (Phase 4+), once there's an actual account to route from.
    """

    household: HouseholdSimulatorService
    media: MediaService
    memory: MemoryService
    calendar: CalendarService
    plans: PlanStore
    config: HavenConfig
    household_id: str


def get_app(ctx: Context) -> AppContext:
    """Fetch the shared `AppContext` from an MCP tool call's request context."""

    return ctx.request_context.lifespan_context


def build_app_context(config: HavenConfig | None = None) -> AppContext:
    """Construct Haven's runtime dependencies for one server process."""

    config = config or get_config()
    logging.basicConfig(level=config.log_level, format="%(levelname)s %(name)s: %(message)s")

    household_service = HouseholdSimulatorService()
    household_id = household_service.get_snapshot().household_id

    media_service = MediaService(MediaSimulatorService())
    # Backend chosen by HAVEN_PERSISTENCE_BACKEND (memory | dynamodb); the
    # MemoryService API is identical either way.
    memory_service = create_memory_service(config, default_household_id=household_id)
    calendar_service = demo_calendar()

    logger.info(
        "Haven MCP server starting: household_id=%s planner_backend=%s persistence_backend=%s",
        household_id,
        config.planner_backend,
        config.persistence_backend,
    )

    return AppContext(
        household=household_service,
        media=media_service,
        memory=memory_service,
        calendar=calendar_service,
        plans=PlanStore(),
        config=config,
        household_id=household_id,
    )


# One AppContext per server process. In stateless Streamable HTTP mode (the
# AgentCore Runtime contract) FastMCP re-enters the lifespan for *every*
# request, so without this cache a plan proposed in one request would be
# unknown to the next. Sharing the context keeps the in-process PlanStore
# and simulators alive across requests of the same runtime instance; durable
# state still belongs to MemoryService (DynamoDB in the cloud).
_shared_app_context: AppContext | None = None


def reset_app_context() -> None:
    """Drop the process-wide AppContext (tests, or an explicit restart)."""

    global _shared_app_context
    _shared_app_context = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Yield the process-wide AppContext, building it on first use."""

    global _shared_app_context
    if _shared_app_context is None:
        _shared_app_context = build_app_context()
    yield _shared_app_context


def create_server(
    host: str | None = None,
    port: int | None = None,
    *,
    stateless_http: bool | None = None,
) -> FastMCP:
    """Build and fully wire the Haven MCP server.

    ``stateless_http=True`` is what the AgentCore Runtime MCP contract expects
    (see ``aws/agentcore/runtime.py``); local tooling can leave it unset.
    """

    kwargs: dict[str, object] = {}

    if host is not None:
        kwargs["host"] = host

    if port is not None:
        kwargs["port"] = port

    if stateless_http is not None:
        kwargs["stateless_http"] = stateless_http

    mcp = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        lifespan=app_lifespan,
        **kwargs,
    )

    from haven.mcp.tools import context as context_tool
    from haven.mcp.tools import execution as execution_tool
    from haven.mcp.tools import planning as planning_tool
    from haven.mcp.tools import preferences as preferences_tool
    from haven.mcp.tools import recommendations as recommendations_tool

    context_tool.register(mcp)
    planning_tool.register(mcp)
    execution_tool.register(mcp)
    preferences_tool.register(mcp)
    recommendations_tool.register(mcp)

    return mcp


__all__ = [
    "AppContext",
    "PlanStore",
    "StoredPlan",
    "get_app",
    "app_lifespan",
    "build_app_context",
    "reset_app_context",
    "create_server",
]
