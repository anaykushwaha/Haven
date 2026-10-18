"""Unit tests for the read-only dashboard API (snapshot + ASGI wrapper)."""

from __future__ import annotations

import asyncio
import json

import pytest

from haven.context.resolver import resolve_context
from haven.mcp.dashboard import DashboardApiMiddleware, build_dashboard_snapshot, build_rationale
from haven.mcp.schemas import context_to_summary
from haven.mcp.tools.context import build_sources
from haven.mcp.tools.execution import execute_household_plan
from haven.mcp.tools.planning import propose_household_plan
from haven.mcp.tools.preferences import save_household_preference
from tests.fixtures.plans import FRONT_DOOR, LIVING_ROOM_LIGHTS, SECURITY_SYSTEM, TV


def _call(app, path, method="GET"):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app({"type": "http", "method": method, "path": path}, receive, send))
    start = messages[0]
    body = b"".join(m.get("body", b"") for m in messages[1:])
    return start["status"], dict(start["headers"]), body


class InnerApp:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope["type"])
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"inner"})


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #


def test_snapshot_of_a_quiet_household(app_context):
    snapshot = build_dashboard_snapshot(app_context)

    assert snapshot.household_id == "household-001"
    assert snapshot.household_name
    assert snapshot.planner_backend == "mock"
    assert snapshot.persistence_backend == "memory"
    assert len(snapshot.devices) == 8
    assert len(snapshot.rooms) == 4
    assert [p.name for p in snapshot.context.people if p.home] == ["Alex", "Sam"]
    assert snapshot.security.armed is False
    assert snapshot.security.all_locked is False
    assert snapshot.media_sessions == []
    assert snapshot.active_plan is None
    assert snapshot.recent_plans == []
    assert snapshot.recent_activity == []


def test_snapshot_shows_a_proposed_plan(app_context):
    proposed = propose_household_plan(app_context, "We're leaving the house")

    snapshot = build_dashboard_snapshot(app_context)

    plan = snapshot.active_plan
    assert plan.plan_id == proposed.plan_id
    assert plan.workflow == "leaving_home"
    assert plan.status == "AWAITING_CONFIRMATION"
    assert plan.execution_status is None
    assert len(plan.steps) == 8
    assert all(s.execution_status is None and s.verification_status is None for s in plan.steps)
    assert snapshot.recent_plans[0].plan_id == proposed.plan_id


def test_snapshot_follows_an_executed_plan(app_context):
    proposed = propose_household_plan(app_context, "We're leaving the house")
    execute_household_plan(app_context, proposed.plan_id, confirmed=True)

    snapshot = build_dashboard_snapshot(app_context)

    plan = snapshot.active_plan
    assert plan.execution_status == "COMPLETED"
    assert plan.verification_status == "VERIFIED"
    assert all(s.execution_status == "SUCCEEDED" for s in plan.steps)
    assert all(s.verification_status == "VERIFIED" for s in plan.steps)
    assert all(s.attempts == 1 for s in plan.steps)
    assert snapshot.security.armed is True and snapshot.security.all_locked is True
    lights = [d for d in snapshot.devices if d.device_type == "light"]
    assert all(d.state["power"] == "off" for d in lights)


def test_snapshot_shows_media_sessions_and_activity(app_context):
    proposed = propose_household_plan(app_context, "Get movie night ready")
    execute_household_plan(app_context, proposed.plan_id)

    snapshot = build_dashboard_snapshot(app_context)

    assert [(s.device_id, s.status) for s in snapshot.media_sessions] == [(TV, "ready")]
    assert snapshot.recent_activity[0].activity_type == "movie_night_prepared"
    assert snapshot.recent_plans[0].execution_status == "COMPLETED"


def test_snapshot_newest_plan_comes_first(app_context):
    first = propose_household_plan(app_context, "Get movie night ready")
    second = propose_household_plan(app_context, "We're leaving the house")

    snapshot = build_dashboard_snapshot(app_context)

    assert snapshot.active_plan.plan_id == second.plan_id
    assert [p.plan_id for p in snapshot.recent_plans] == [second.plan_id, first.plan_id]


def test_snapshot_reflects_a_failed_step(app_context):
    proposed = propose_household_plan(app_context, "We're leaving the house")
    app_context.household.set_device_state(FRONT_DOOR, online=False)

    execute_household_plan(app_context, proposed.plan_id, confirmed=True)
    snapshot = build_dashboard_snapshot(app_context)

    failed = [s for s in snapshot.active_plan.steps if s.execution_status == "FAILED"]
    assert len(failed) == 1
    assert failed[0].attempts == 2
    assert "offline" in failed[0].execution_detail
    assert snapshot.active_plan.execution_status == "PARTIAL"
    assert next(d for d in snapshot.devices if d.id == FRONT_DOOR).online is False


def test_snapshot_explains_a_clarification_plan(app_context):
    for device in app_context.household.get_devices():
        if device["device_type"] == "tv":
            app_context.household.set_device_state(device["device_id"], online=False)
    propose_household_plan(app_context, "Get movie night ready")

    plan = build_dashboard_snapshot(app_context).active_plan

    assert plan.clarification_needed is True
    assert plan.steps == []
    assert any("asks instead of guessing" in line for line in plan.rationale)


def test_snapshot_is_read_only(app_context):
    propose_household_plan(app_context, "We're leaving the house")
    devices = app_context.household.get_devices()
    plans = [s.plan.id for s in app_context.plans.all()]

    build_dashboard_snapshot(app_context)
    build_dashboard_snapshot(app_context)

    assert app_context.household.get_devices() == devices
    assert [s.plan.id for s in app_context.plans.all()] == plans


def test_snapshot_serialises_to_json(app_context):
    propose_household_plan(app_context, "We're leaving the house")

    payload = json.loads(build_dashboard_snapshot(app_context).model_dump_json())

    assert payload["active_plan"]["workflow"] == "leaving_home"
    assert isinstance(payload["devices"][0]["state"], dict)


def test_recent_plans_are_capped(app_context):
    for _ in range(12):
        propose_household_plan(app_context, "Get movie night ready")

    snapshot = build_dashboard_snapshot(app_context)

    assert len(snapshot.recent_plans) == 8


# --------------------------------------------------------------------------- #
# Rationale
# --------------------------------------------------------------------------- #


def test_rationale_for_a_high_impact_plan(app_context):
    proposed = propose_household_plan(app_context, "We're leaving the house")
    stored = app_context.plans.get(proposed.plan_id)
    context = context_to_summary(
        resolve_context(
            "x",
            app_context.household.get_snapshot(),
            app_context.media.list_media_items(),
            build_sources(app_context),
        )
    )

    lines = build_rationale(stored.plan, context)

    assert lines[0] == "Understood the goal as: leave the house."
    assert any("People home right now: Alex, Sam." == line for line in lines)
    assert any("3 steps are high-impact" in line and "until you confirm" in line for line in lines)


def test_rationale_for_a_reversible_plan(app_context):
    save_household_preference(app_context, "preferred_genres", ["sci-fi"])
    proposed = propose_household_plan(app_context, "Get movie night ready")

    lines = build_dashboard_snapshot(app_context).active_plan.rationale

    assert lines[0] == "Understood the goal as: get movie night ready."
    assert "Proposed 3 steps." in lines
    assert any("minutes are free" in line for line in lines)
    assert any("Saved genre preferences: sci-fi." in line for line in lines)
    assert "Every step is reversible, so Haven can run this without asking first." in lines
    assert proposed.plan_id


def test_rationale_for_suggestions_only(app_context):
    propose_household_plan(app_context, "What should we do tonight?")

    lines = build_dashboard_snapshot(app_context).active_plan.rationale

    assert "These are suggestions only; nothing in the home changes." in lines


# --------------------------------------------------------------------------- #
# ASGI wrapper
# --------------------------------------------------------------------------- #


@pytest.fixture
def wrapped(app_context):
    inner = InnerApp()
    return inner, DashboardApiMiddleware(inner, lambda: app_context)


def test_other_paths_pass_through_untouched(wrapped):
    inner, app = wrapped

    status, _, body = _call(app, "/mcp", method="POST")

    assert (status, body) == (200, b"inner")
    assert inner.scopes == ["http"]


def test_lifespan_events_pass_through(wrapped):
    inner, app = wrapped

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        return None

    asyncio.run(app({"type": "lifespan"}, receive, send))

    assert inner.scopes == ["lifespan"]


def test_a_path_that_merely_starts_with_api_is_not_the_api(wrapped):
    inner, app = wrapped

    status, _, body = _call(app, "/apiary")

    assert (status, body) == (200, b"inner")


def test_health_endpoint(wrapped):
    _, app = wrapped

    status, headers, body = _call(app, "/api/health")

    assert status == 200
    assert headers[b"content-type"].startswith(b"application/json")
    assert headers[b"cache-control"] == b"no-store"
    assert json.loads(body) == {"status": "ok", "service": "haven"}


def test_dashboard_endpoint_serves_the_snapshot(wrapped, app_context):
    _, app = wrapped
    propose_household_plan(app_context, "We're leaving the house")

    status, headers, body = _call(app, "/api/dashboard")

    payload = json.loads(body)
    assert status == 200
    assert int(headers[b"content-length"]) == len(body)
    assert payload["household_id"] == "household-001"
    assert payload["active_plan"]["goal"] == "We're leaving the house"
    assert {d["id"] for d in payload["devices"]} >= {FRONT_DOOR, SECURITY_SYSTEM, LIVING_ROOM_LIGHTS}


def test_head_requests_get_headers_without_a_body(wrapped):
    _, app = wrapped

    status, headers, body = _call(app, "/api/dashboard", method="HEAD")

    assert status == 200
    assert body == b""
    assert int(headers[b"content-length"]) > 0


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_the_api_is_read_only(wrapped, method):
    _, app = wrapped

    status, headers, body = _call(app, "/api/dashboard", method=method)

    assert status == 405
    assert headers[b"allow"] == b"GET, HEAD"
    assert json.loads(body) == {"error": "method_not_allowed"}


def test_unknown_api_paths_are_not_found(wrapped):
    _, app = wrapped

    status, _, body = _call(app, "/api/secrets")

    assert status == 404
    assert json.loads(body) == {"error": "not_found"}


def test_a_failing_snapshot_is_a_generic_500(app_context):
    def broken():
        raise RuntimeError("sensitive internal detail")

    app = DashboardApiMiddleware(InnerApp(), broken)

    status, _, body = _call(app, "/api/dashboard")

    assert status == 500
    assert json.loads(body) == {"error": "snapshot_failed"}
    assert b"sensitive" not in body


def test_a_custom_prefix(app_context):
    app = DashboardApiMiddleware(InnerApp(), lambda: app_context, prefix="/debug/")

    assert _call(app, "/debug/health")[0] == 200
    assert _call(app, "/api/health")[2] == b"inner"
