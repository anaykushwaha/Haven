"""Integration tests: MCP request -> Haven -> MCP response.

Two layers:

- **Direct tool calls** (synchronous) build the real server with
  `create_server()` and call the real tool functions in
  `haven.mcp.tools.*` against an isolated `AppContext` (the `app_context`
  fixture, built exactly as `build_app_context` builds the server's). This
  is the exact code FastMCP runs once a request arrives, so a bug here is
  a bug Alexa+ would hit too — and it covers every workflow's full path:
  context -> plan -> confirm -> execute -> verify -> recover.
- **Protocol tests** (``anyio``) drive Haven's real AgentCore entrypoint
  with a real MCP client over an in-process ASGI transport, one stateless
  request at a time, exactly as AgentCore sends them. They prove the
  wire-level contract: tool schemas, optional arguments, and state
  surviving across separate requests.

Everything is offline: mock planner, in-memory persistence, simulators.
"""

from __future__ import annotations

import asyncio

import pytest

from haven.mcp.server import (
    app_lifespan,
    create_server,
    get_shared_app_context,
    reset_app_context,
)
from haven.mcp.tools.context import get_household_context
from haven.mcp.tools.execution import execute_household_plan
from haven.mcp.tools.planning import get_plan_status, propose_household_plan
from haven.mcp.tools.preferences import save_household_preference
from haven.mcp.tools.recommendations import get_activity_recommendations
from tests.fixtures.plans import (
    BACK_DOOR,
    FRONT_DOOR,
    KITCHEN_LIGHTS,
    LIVING_ROOM_LIGHTS,
    SECURITY_SYSTEM,
    THERMOSTAT,
    FlakyHouseholdBackend,
    lock_front_door_plan,
)

pytestmark = pytest.mark.integration

LEAVING = "We're leaving the house"


def _state(app, device_id):
    return app.household.get_device_state(device_id)["state"]


# --------------------------------------------------------------------------- #
# The server and its shared runtime
# --------------------------------------------------------------------------- #


def test_server_builds_with_every_tool(app_context):
    server = create_server()

    assert server is not None
    assert app_context.household_id == "household-001"
    assert app_context.plans.latest() is None


def test_the_lifespan_shares_one_context_across_requests(app_context):
    reset_app_context()

    async def enter():
        async with app_lifespan(create_server()) as first:
            pass
        async with app_lifespan(create_server()) as second:
            pass
        return first, second

    first, second = asyncio.run(enter())

    assert first is second
    assert first is get_shared_app_context()
    reset_app_context()


# --------------------------------------------------------------------------- #
# Context, preferences, recommendations
# --------------------------------------------------------------------------- #


def test_household_context_round_trip(app_context):
    context = get_household_context(app_context, "Get movie night ready")

    assert context.household_id == app_context.household_id
    assert len(context.people) == 3
    assert len(context.devices) == 8
    assert len(context.media_options) == 5
    assert context.available_minutes is not None and context.available_minutes > 0

    with pytest.raises(ValueError, match="goal"):
        get_household_context(app_context, "   ")


def test_preference_save_round_trip(app_context):
    saved = save_household_preference(app_context, "preferred_genres", ["sci-fi", "comedy"])

    assert saved.household_id == app_context.household_id
    assert saved.key == "preferred_genres"
    assert saved.value == ["sci-fi", "comedy"]
    assert saved.scope == "household"
    assert get_household_context(app_context, "movie night").preferred_genres == [
        "sci-fi",
        "comedy",
    ]

    with pytest.raises(ValueError, match="temporary state"):
        save_household_preference(app_context, "room_occupied", True)


def test_recommendations_round_trip(app_context):
    recommendations = get_activity_recommendations(app_context)

    assert recommendations.household_id == app_context.household_id
    assert 0 < len(recommendations.recommendations) <= 3
    assert all(
        r.duration_minutes <= recommendations.available_minutes
        for r in recommendations.recommendations
    )
    assert get_activity_recommendations(app_context, "movie night").household_id


def test_recommendations_respect_saved_preferences_and_history(app_context):
    save_household_preference(app_context, "preferred_genres", ["comedy"])
    first = get_activity_recommendations(app_context).recommendations[0]
    assert "comedy" in first.reason.lower()

    plan = propose_household_plan(app_context, "Get movie night ready")
    execute_household_plan(app_context, plan.plan_id)
    watched = app_context.memory.list_activity(app_context.household_id)[0].details["title"]
    after = get_activity_recommendations(app_context).recommendations

    assert watched
    assert {r.title for r in after if "recently" in r.reason.lower()} <= {watched}


# --------------------------------------------------------------------------- #
# Movie night
# --------------------------------------------------------------------------- #


def test_movie_night_propose_status_execute_verify(app_context):
    plan = propose_household_plan(app_context, "Get movie night ready")

    assert plan.status == "PROPOSED"
    assert plan.requires_confirmation is False
    assert plan.clarification_needed is False
    assert len(plan.steps) == 3

    before = get_plan_status(app_context, plan.plan_id)
    assert before.execution_status is None
    assert "ready to execute" in before.message.lower()

    execution = execute_household_plan(app_context, plan.plan_id)

    assert execution.execution_status == "COMPLETED"
    assert execution.verification_status == "VERIFIED"
    assert all(s.status == "SUCCEEDED" for s in execution.steps)
    assert all(v.status == "VERIFIED" for v in execution.verifications)
    assert _state(app_context, LIVING_ROOM_LIGHTS)["brightness"] == 20

    after = get_plan_status(app_context, plan.plan_id)
    assert (after.execution_status, after.verification_status) == ("COMPLETED", "VERIFIED")


def test_unknown_plan_ids_and_blank_input_are_rejected(app_context):
    with pytest.raises(ValueError, match="Unknown plan_id"):
        get_plan_status(app_context, "plan-does-not-exist")
    with pytest.raises(ValueError, match="Unknown plan_id"):
        execute_household_plan(app_context, "plan-does-not-exist")
    with pytest.raises(ValueError, match="goal"):
        propose_household_plan(app_context, "")
    with pytest.raises(ValueError, match="plan_id"):
        get_plan_status(app_context, " ")


# --------------------------------------------------------------------------- #
# Leaving home: confirmation, verification and recovery over MCP
# --------------------------------------------------------------------------- #


def test_leaving_home_waits_for_confirmation(app_context):
    plan = propose_household_plan(app_context, LEAVING)

    assert plan.status == "AWAITING_CONFIRMATION"
    assert plan.requires_confirmation is True
    assert "confirmation" in get_plan_status(app_context, plan.plan_id).message.lower()

    held = execute_household_plan(app_context, plan.plan_id)  # confirmed defaults to False

    assert held.execution_status == "AWAITING_CONFIRMATION"
    assert held.verification_status == "SKIPPED"
    assert "confirmation" in held.message.lower()
    assert _state(app_context, FRONT_DOOR)["locked"] is False
    assert _state(app_context, SECURITY_SYSTEM)["armed"] is False


def test_confirming_leaving_home_secures_and_verifies(app_context):
    plan = propose_household_plan(app_context, LEAVING)

    done = execute_household_plan(app_context, plan.plan_id, confirmed=True)

    assert (done.execution_status, done.verification_status) == ("COMPLETED", "VERIFIED")
    assert len(done.steps) == 8
    assert done.message.startswith("Done")
    assert _state(app_context, FRONT_DOOR)["locked"] is True
    assert _state(app_context, BACK_DOOR)["locked"] is True
    assert _state(app_context, SECURITY_SYSTEM)["armed"] is True
    assert _state(app_context, THERMOSTAT)["mode"] == "away"
    assert _state(app_context, KITCHEN_LIGHTS)["power"] == "off"


def test_a_door_that_will_not_respond_gives_a_partial_result(app_context):
    plan = propose_household_plan(app_context, LEAVING)
    app_context.household.set_device_state(FRONT_DOOR, online=False)

    result = execute_household_plan(app_context, plan.plan_id, confirmed=True)

    assert result.execution_status == "PARTIAL"
    assert result.message.startswith("Partially done")
    assert "offline" in result.message
    assert "retried 1 step" in result.message
    assert _state(app_context, BACK_DOOR)["locked"] is True
    assert get_plan_status(app_context, plan.plan_id).execution_status == "PARTIAL"


def test_the_mcp_tool_corrects_a_lock_that_ignored_the_command(app_context):
    flaky = FlakyHouseholdBackend(app_context.household)
    flaky.drop_writes(FRONT_DOOR, 1)
    plan = propose_household_plan(app_context, LEAVING)
    app_context.household = flaky

    result = execute_household_plan(app_context, plan.plan_id, confirmed=True)

    assert (result.execution_status, result.verification_status) == ("COMPLETED", "VERIFIED")
    assert "retried 1 step" in result.message
    assert flaky._inner.get_device_state(FRONT_DOOR)["state"]["locked"] is True


def test_a_high_impact_plan_needs_confirmation(app_context):
    plan = lock_front_door_plan()
    app_context.plans.save(plan, app_context.household_id)

    held = execute_household_plan(app_context, plan.id, confirmed=False)
    assert held.execution_status == "AWAITING_CONFIRMATION"
    assert _state(app_context, FRONT_DOOR)["locked"] is False

    done = execute_household_plan(app_context, plan.id, confirmed=True)
    assert done.execution_status == "COMPLETED"
    assert _state(app_context, FRONT_DOOR)["locked"] is True


# --------------------------------------------------------------------------- #
# Clarification and tonight planning
# --------------------------------------------------------------------------- #


def _take_every_tv_offline(app):
    for device in app.household.get_devices():
        if device["device_type"] == "tv":
            app.household.set_device_state(device["device_id"], online=False)


def test_a_plan_that_needs_more_information_says_so(app_context):
    _take_every_tv_offline(app_context)

    plan = propose_household_plan(app_context, "Get movie night ready")

    assert plan.clarification_needed is True
    assert plan.clarification_prompt
    assert plan.steps == []
    status = get_plan_status(app_context, plan.plan_id)
    assert plan.clarification_prompt in status.message


def test_executing_a_clarification_plan_runs_nothing(app_context):
    _take_every_tv_offline(app_context)
    plan = propose_household_plan(app_context, "Get movie night ready")
    before = app_context.household.get_devices()

    result = execute_household_plan(app_context, plan.plan_id, confirmed=True)

    assert result.execution_status == "NO_ACTION"
    assert result.verification_status == "SKIPPED"
    assert result.steps == []
    assert plan.clarification_prompt in result.message
    assert app_context.household.get_devices() == before


def test_tonight_planning_suggests_without_changing_anything(app_context):
    before = app_context.household.get_devices()
    plan = propose_household_plan(app_context, "What should we do tonight?")

    result = execute_household_plan(app_context, plan.plan_id)

    assert plan.requires_confirmation is False
    assert len(plan.steps) >= 2
    assert result.execution_status == "COMPLETED"
    assert app_context.household.get_devices() == before


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def test_plan_store_tracks_the_newest_plan_first(app_context):
    first = propose_household_plan(app_context, "Get movie night ready")
    second = propose_household_plan(app_context, LEAVING)

    assert app_context.plans.latest().plan.id == second.plan_id
    assert [s.plan.id for s in app_context.plans.all()] == [second.plan_id, first.plan_id]


def test_proposed_plans_are_recorded_in_memory(app_context):
    plan = propose_household_plan(app_context, "Get movie night ready")

    records = app_context.memory.list_activity(app_context.household_id)

    assert any(plan.plan_id in str(r.details) or plan.plan_id in r.summary for r in records)


# --------------------------------------------------------------------------- #
# Protocol-level tests (real MCP client, Haven's AgentCore entrypoint)
# --------------------------------------------------------------------------- #

EXECUTE_TOOL = "execute_household_plan_tool"


@pytest.mark.anyio
async def test_optional_arguments_are_really_optional(inprocess_mcp) -> None:
    async with inprocess_mcp() as connect, connect() as probe:
        tools = {t.name: t for t in (await probe.session.list_tools()).tools}

        for name, optional in {
            EXECUTE_TOOL: "confirmed",
            "save_household_preference_tool": "person_id",
            "get_activity_recommendations_tool": "goal",
        }.items():
            required = tools[name].inputSchema.get("required") or []
            assert optional not in required, f"{name} must not require {optional}"
            assert "ctx" not in tools[name].inputSchema.get("properties", {})


@pytest.mark.anyio
async def test_leaving_home_confirmation_flow_over_mcp(inprocess_mcp) -> None:
    async with inprocess_mcp() as connect:
        async with connect() as probe:
            plan = await probe.call_json("propose_household_plan_tool", goal=LEAVING)
        assert plan["requires_confirmation"] is True
        assert plan["clarification_needed"] is False

        async with connect() as probe:  # the user has not confirmed yet
            held = await probe.call_json(EXECUTE_TOOL, plan_id=plan["plan_id"])
        assert held["execution_status"] == "AWAITING_CONFIRMATION"

        async with connect() as probe:  # "yes, go ahead"
            done = await probe.call_json(EXECUTE_TOOL, plan_id=plan["plan_id"], confirmed=True)
        assert done["execution_status"] == "COMPLETED"
        assert done["verification_status"] == "VERIFIED"

        async with connect() as probe:
            status = await probe.call_json("get_plan_status_tool", plan_id=plan["plan_id"])
        assert status["execution_status"] == "COMPLETED"


@pytest.mark.anyio
async def test_preferences_and_recommendations_over_mcp(inprocess_mcp) -> None:
    async with inprocess_mcp() as connect:
        async with connect() as probe:
            saved = await probe.call_json(
                "save_household_preference_tool", key="preferred_genres", value=["comedy"]
            )
        assert saved["scope"] == "household"

        async with connect() as probe:
            recommendations = await probe.call_json("get_activity_recommendations_tool")
        assert recommendations["recommendations"]
        assert "comedy" in recommendations["recommendations"][0]["reason"].lower()


@pytest.mark.anyio
async def test_a_blank_goal_is_a_tool_error_over_mcp(inprocess_mcp) -> None:
    async with inprocess_mcp() as connect, connect() as probe:
        is_error, text = await probe.call("propose_household_plan_tool", goal="  ")

    assert is_error
    assert "goal" in text.lower()
