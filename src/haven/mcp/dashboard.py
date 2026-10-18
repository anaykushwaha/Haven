"""Read-only dashboard API.

The Haven dashboard (`apps/dashboard`) is a visualization and debugging
layer, not part of the product: it shows what the household looks like and
what Haven is doing about a request, so a judge (or a developer) can watch
Alexa+ drive the agent. This module is its backend:

- `build_dashboard_snapshot(app)` turns the server's live `AppContext`
  into one JSON-friendly `DashboardSnapshot`: household state, simulator
  state, the active plan with each step's execution and verification
  result, and why Haven made that plan.
- `DashboardApiMiddleware` is a tiny pure-ASGI wrapper that serves it at
  ``GET /api/dashboard`` (and ``GET /api/health``) next to the MCP endpoint
  and hands every other request to the wrapped app untouched.

It is deliberately **opt-in and read-only** (`scripts/run_server.py
--dashboard-api`). Household state is private, and the MCP port is often
tunnelled to the public internet for Alexa+, so Haven never exposes this
by default and never accepts a write through it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from haven.mcp.schemas import HouseholdContextSummary
from haven.mcp.server import AppContext, StoredPlan, get_shared_app_context
from haven.mcp.tools.context import get_household_context
from haven.models.plan import ActionRisk, Plan
from haven.planning.workflows import WorkflowType, classify_workflow

logger = logging.getLogger(__name__)

RECENT_PLAN_LIMIT = 8
RECENT_ACTIVITY_LIMIT = 10

_WORKFLOW_LABELS = {
    WorkflowType.MOVIE_NIGHT: "get movie night ready",
    WorkflowType.LEAVING_HOME: "leave the house",
    WorkflowType.TONIGHT_PLANNING: "decide what to do tonight",
    WorkflowType.UNKNOWN: "an unrecognised request",
}

_READ_ONLY_ACTIONS = frozenset(
    {"select_media", "suggest_media", "review_schedule", "suggest_wind_down"}
)


class DashboardRoom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    occupied: bool
    current_activity: str | None = None


class DashboardDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    device_type: str
    room_id: str | None = None
    online: bool
    state: dict[str, Any] = Field(default_factory=dict)


class DashboardLock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    name: str
    locked: bool
    online: bool


class DashboardAlarm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    name: str
    armed: bool
    mode: str
    online: bool


class DashboardSecurity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    armed: bool
    all_locked: bool
    locks: list[DashboardLock] = Field(default_factory=list)
    alarms: list[DashboardAlarm] = Field(default_factory=list)


class DashboardMediaSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    media_id: str
    title: str
    status: str


class DashboardStep(BaseModel):
    """One plan step with whatever has happened to it so far."""

    model_config = ConfigDict(extra="forbid")

    id: str
    order: int
    action: str
    title: str
    description: str
    risk: str
    requires_confirmation: bool
    expected_effect: str | None = None
    device_id: str | None = None
    execution_status: str | None = None
    execution_detail: str | None = None
    attempts: int | None = None
    verification_status: str | None = None
    verification_detail: str | None = None


class DashboardPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    goal: str
    workflow: str
    status: str
    summary: str
    requires_confirmation: bool
    clarification_needed: bool = False
    clarification_prompt: str | None = None
    rejected_candidate_count: int = 0
    created_at: str
    steps: list[DashboardStep] = Field(default_factory=list)
    execution_status: str | None = None
    verification_status: str | None = None
    rationale: list[str] = Field(default_factory=list)


class DashboardPlanListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    goal: str
    plan_status: str
    execution_status: str | None = None
    verification_status: str | None = None
    created_at: str


class DashboardActivity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_type: str
    summary: str
    occurred_at: str


class DashboardSnapshot(BaseModel):
    """Everything the dashboard renders, in one response."""

    model_config = ConfigDict(extra="forbid")

    generated_at: str
    household_id: str
    household_name: str
    planner_backend: str
    persistence_backend: str
    context: HouseholdContextSummary
    rooms: list[DashboardRoom] = Field(default_factory=list)
    devices: list[DashboardDevice] = Field(default_factory=list)
    security: DashboardSecurity
    media_sessions: list[DashboardMediaSession] = Field(default_factory=list)
    active_plan: DashboardPlan | None = None
    recent_plans: list[DashboardPlanListItem] = Field(default_factory=list)
    recent_activity: list[DashboardActivity] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Snapshot building
# --------------------------------------------------------------------------- #


def build_dashboard_snapshot(app: AppContext) -> DashboardSnapshot:
    """Read the live `AppContext` into a `DashboardSnapshot`.

    Read-only: nothing here changes household, media, plan or memory state.
    """

    household_record = app.household.get_household()
    context = get_household_context(app, "dashboard status")

    devices = [
        DashboardDevice(
            id=d["device_id"],
            name=d["name"],
            device_type=str(d.get("device_type", "other")),
            room_id=d.get("room_id"),
            online=bool(d.get("online", True)),
            state=dict(d.get("state", {})),
        )
        for d in app.household.get_devices()
    ]

    security_state = app.household.security.get_security_state()
    security = DashboardSecurity(
        armed=security_state["armed"],
        all_locked=security_state["all_locked"],
        locks=[DashboardLock(**lock) for lock in security_state["locks"]],
        alarms=[DashboardAlarm(**alarm) for alarm in security_state["alarms"]],
    )

    sessions: list[DashboardMediaSession] = []
    for device in devices:
        if device.device_type != "tv":
            continue
        session = app.media.get_session_state(device.id)
        if session is not None:
            sessions.append(
                DashboardMediaSession(
                    device_id=device.id,
                    media_id=session.media_id,
                    title=session.title,
                    status=session.status.value,
                )
            )

    stored_plans = app.plans.all()
    latest = stored_plans[0] if stored_plans else None

    activity = sorted(
        app.memory.list_activity(app.household_id, limit=RECENT_ACTIVITY_LIMIT),
        key=lambda record: record.occurred_at,
        reverse=True,
    )

    return DashboardSnapshot(
        generated_at=datetime.now(UTC).isoformat(),
        household_id=app.household_id,
        household_name=str(household_record.get("name", app.household_id)),
        planner_backend=str(app.config.planner_backend),
        persistence_backend=str(app.config.persistence_backend),
        context=context,
        rooms=[
            DashboardRoom(
                id=r["room_id"],
                name=r["name"],
                occupied=bool(r.get("occupied", False)),
                current_activity=r.get("current_activity"),
            )
            for r in app.household.get_rooms()
        ],
        devices=devices,
        security=security,
        media_sessions=sessions,
        active_plan=_build_plan(latest, context) if latest is not None else None,
        recent_plans=[_plan_list_item(s) for s in stored_plans[:RECENT_PLAN_LIMIT]],
        recent_activity=[
            DashboardActivity(
                activity_type=str(record.activity_type.value),
                summary=record.summary,
                occurred_at=record.occurred_at.isoformat(),
            )
            for record in activity
        ],
    )


def _plan_list_item(stored: StoredPlan) -> DashboardPlanListItem:
    return DashboardPlanListItem(
        plan_id=stored.plan.id,
        goal=stored.plan.goal,
        plan_status=stored.plan.status.value,
        execution_status=stored.execution.status.value if stored.execution else None,
        verification_status=stored.verification.status.value if stored.verification else None,
        created_at=stored.plan.created_at.isoformat(),
    )


def _build_plan(stored: StoredPlan, context: HouseholdContextSummary) -> DashboardPlan:
    plan = stored.plan
    executions = {r.step_id: r for r in stored.execution.step_results} if stored.execution else {}
    verifications = (
        {v.step_id: v for v in stored.verification.step_verifications} if stored.verification else {}
    )

    steps = []
    for step in sorted(plan.steps, key=lambda s: s.order):
        execution = executions.get(step.id)
        verification = verifications.get(step.id)
        steps.append(
            DashboardStep(
                id=step.id,
                order=step.order,
                action=step.action,
                title=step.title,
                description=step.description,
                risk=step.risk.value,
                requires_confirmation=step.requires_confirmation,
                expected_effect=step.expected_effect,
                device_id=step.device_id,
                execution_status=execution.status.value if execution else None,
                execution_detail=execution.detail if execution else None,
                attempts=execution.attempts if execution else None,
                verification_status=verification.status.value if verification else None,
                verification_detail=verification.detail if verification else None,
            )
        )

    return DashboardPlan(
        plan_id=plan.id,
        goal=plan.goal,
        workflow=classify_workflow(plan.goal).value,
        status=plan.status.value,
        summary=plan.summary,
        requires_confirmation=plan.requires_confirmation,
        clarification_needed=plan.clarification_needed,
        clarification_prompt=plan.clarification_prompt,
        rejected_candidate_count=plan.rejected_candidate_count,
        created_at=plan.created_at.isoformat(),
        steps=steps,
        execution_status=stored.execution.status.value if stored.execution else None,
        verification_status=stored.verification.status.value if stored.verification else None,
        rationale=build_rationale(plan, context),
    )


def build_rationale(plan: Plan, context: HouseholdContextSummary) -> list[str]:
    """Explain, in plain sentences, why Haven proposed this plan.

    Built only from facts Haven already holds (the plan itself and the
    current context) — it is an explanation of the deterministic pipeline,
    never a fresh model call, so it cannot drift from what actually ran.
    """

    workflow = classify_workflow(plan.goal)
    lines = [f"Understood the goal as: {_WORKFLOW_LABELS[workflow]}."]

    if plan.clarification_needed:
        prompt = plan.clarification_prompt or "More information is needed."
        lines.append(f"Haven could not safely plan this yet, so it asks instead of guessing: {prompt}")
        return lines

    count = len(plan.steps)
    lines.append(f"Proposed {count} step{'s' if count != 1 else ''}.")
    if plan.rejected_candidate_count:
        lines.append(
            f"{plan.rejected_candidate_count} candidate action"
            f"{'s were' if plan.rejected_candidate_count != 1 else ' was'} ruled out by "
            "deterministic checks (time available, device availability, capabilities)."
        )

    home = [person.name for person in context.people if person.home]
    if home:
        lines.append(f"People home right now: {', '.join(home)}.")
    if context.available_minutes is not None and workflow in (
        WorkflowType.MOVIE_NIGHT,
        WorkflowType.TONIGHT_PLANNING,
    ):
        lines.append(f"About {context.available_minutes} minutes are free before the next event.")
    if context.preferred_genres and workflow in (
        WorkflowType.MOVIE_NIGHT,
        WorkflowType.TONIGHT_PLANNING,
    ):
        lines.append(f"Saved genre preferences: {', '.join(context.preferred_genres)}.")

    high_impact = [s for s in plan.steps if s.risk is ActionRisk.HIGH_IMPACT_ACTION]
    if high_impact:
        lines.append(
            f"{len(high_impact)} step{'s are' if len(high_impact) != 1 else ' is'} high-impact "
            "(locking or arming): Haven will not run any of this plan until you confirm."
        )
    elif plan.steps and all(step.action in _READ_ONLY_ACTIONS for step in plan.steps):
        lines.append("These are suggestions only; nothing in the home changes.")
    elif plan.steps:
        lines.append("Every step is reversible, so Haven can run this without asking first.")

    return lines


# --------------------------------------------------------------------------- #
# ASGI wrapper
# --------------------------------------------------------------------------- #

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class DashboardApiMiddleware:
    """Serve the read-only dashboard API; pass everything else through.

    Pure ASGI (no Starlette dependency), so it wraps any ASGI app —
    including FastMCP's `streamable_http_app()` — and forwards lifespan
    events untouched, which the MCP session manager needs.
    """

    def __init__(
        self,
        app: ASGIApp,
        get_app_context: Callable[[], AppContext] = get_shared_app_context,
        *,
        prefix: str = "/api",
    ) -> None:
        self.app = app
        self._get_app_context = get_app_context
        self._prefix = prefix.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == self._prefix or path.startswith(self._prefix + "/")):
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        if method not in ("GET", "HEAD"):
            await self._respond(send, 405, {"error": "method_not_allowed"}, [(b"allow", b"GET, HEAD")])
            return

        route = path[len(self._prefix) :] or "/"
        if route == "/health":
            await self._respond(send, 200, {"status": "ok", "service": "haven"}, head=method == "HEAD")
            return
        if route == "/dashboard":
            try:
                body = build_dashboard_snapshot(self._get_app_context()).model_dump()
            except Exception:
                logger.exception("Dashboard snapshot failed.")
                await self._respond(send, 500, {"error": "snapshot_failed"})
                return
            await self._respond(send, 200, body, head=method == "HEAD")
            return

        await self._respond(send, 404, {"error": "not_found"})

    @staticmethod
    async def _respond(
        send: Send,
        status: int,
        payload: dict[str, Any],
        extra_headers: list[tuple[bytes, bytes]] | None = None,
        *,
        head: bool = False,
    ) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            *(extra_headers or []),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": b"" if head else body})


__all__ = [
    "DashboardApiMiddleware",
    "DashboardSnapshot",
    "build_dashboard_snapshot",
    "build_rationale",
]
