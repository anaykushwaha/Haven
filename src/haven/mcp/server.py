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
from typing import Protocol

from mcp.server.fastmcp import Context, FastMCP

from haven.config import DYNAMODB_PERSISTENCE_BACKEND, HavenConfig, get_config
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


MAX_STORED_PLANS = 200


class PlanRepository(Protocol):
    """The slice of `aws.dynamodb.repositories.DynamoDBPlanRepository` the
    plan store uses to survive restarts and to share plans between server
    instances.
    """

    def save_plan(self, plan: Plan, household_id: str) -> Plan: ...

    def get_plan(self, plan_id: str) -> Plan | None: ...


class PlanStore:
    """Registry of plans proposed through this MCP server.

    Plans are always held in the server process (newest `max_plans` kept, so
    a long-running server cannot grow without limit). With a `repository`
    they are *also* written through to durable storage and re-loaded on a
    miss, so a plan proposed by one server instance — or one AgentCore
    session, in the stateless runtime — can be confirmed and executed by
    another. That is what the deployed runtime needs: Alexa+ makes
    "propose" and "execute" as separate requests.

    The repository is best-effort by design. If it is unreachable the tool
    still works on this instance (a warning is logged), and a plan the
    repository cannot find is simply unknown, which the tools report with a
    "propose it again" message. Only the *plan* is persisted: execution and
    verification results stay with the instance that ran it, which is safe
    because every step is idempotent.
    """

    def __init__(
        self,
        max_plans: int = MAX_STORED_PLANS,
        *,
        repository: PlanRepository | None = None,
        household_id: str | None = None,
    ) -> None:
        if max_plans < 1:
            raise ValueError("max_plans must be at least 1")
        if repository is not None and not household_id:
            raise ValueError("household_id is required when a repository is given")
        self._max_plans = max_plans
        self._repository = repository
        self._household_id = household_id
        self._plans: dict[str, StoredPlan] = {}

    def save(self, plan: Plan, household_id: str) -> StoredPlan:
        stored = self._remember(plan, household_id)
        if self._repository is not None:
            try:
                self._repository.save_plan(plan, household_id)
            except Exception:  # noqa: BLE001 - persistence must never fail the tool
                logger.warning(
                    "Could not persist plan %s; it is only available on this server instance.",
                    plan.id,
                    exc_info=True,
                )
        return stored

    def get(self, plan_id: str) -> StoredPlan | None:
        stored = self._plans.get(plan_id)
        if stored is not None or self._repository is None:
            return stored
        try:
            plan = self._repository.get_plan(plan_id)
        except Exception:  # noqa: BLE001 - a lookup failure reads as "unknown plan"
            logger.warning("Could not load plan %s from durable storage.", plan_id, exc_info=True)
            return None
        if plan is None:
            return None
        return self._remember(plan, self._household_id or "")

    def _remember(self, plan: Plan, household_id: str) -> StoredPlan:
        stored = StoredPlan(plan=plan, household_id=household_id)
        self._plans[plan.id] = stored
        while len(self._plans) > self._max_plans:
            del self._plans[next(iter(self._plans))]
        return stored

    def all(self) -> list[StoredPlan]:
        """Every plan held by this instance, most recently proposed first."""

        return list(reversed(self._plans.values()))

    def latest(self) -> StoredPlan | None:
        """The most recently proposed plan held by this instance, if any."""

        return next(iter(reversed(self._plans.values())), None)

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


def _create_plan_repository(config: HavenConfig) -> PlanRepository | None:
    """The durable plan repository for this configuration, if there is one.

    `None` for the in-memory backend (local runs). If DynamoDB is selected
    but cannot be reached, plans fall back to this instance's memory rather
    than stopping the server — the same degradation `PlanStore` applies on
    every call.
    """

    if config.persistence_backend != DYNAMODB_PERSISTENCE_BACKEND:
        return None
    try:
        from aws.dynamodb.repositories import create_dynamodb_repositories

        return create_dynamodb_repositories().plans
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("Durable plan storage is unavailable; plans stay in memory.", exc_info=True)
        return None


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
        plans=PlanStore(
            repository=_create_plan_repository(config),
            household_id=household_id,
        ),
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


def get_shared_app_context() -> AppContext:
    """Return the process-wide AppContext, building it on first use.

    Used by the MCP lifespan below and by the optional dashboard API
    (`haven.mcp.dashboard`), which must read the *same* state the MCP tools
    mutate rather than build a second household of its own.
    """

    global _shared_app_context
    if _shared_app_context is None:
        _shared_app_context = build_app_context()
    return _shared_app_context


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Yield the process-wide AppContext, building it on first use."""

    yield get_shared_app_context()


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
    "MAX_STORED_PLANS",
    "PlanRepository",
    "AppContext",
    "PlanStore",
    "StoredPlan",
    "get_app",
    "app_lifespan",
    "build_app_context",
    "get_shared_app_context",
    "reset_app_context",
    "create_server",
]
