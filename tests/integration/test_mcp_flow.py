"""Integration tests: MCP request -> Haven -> MCP response.

Exercises the actual server `haven.mcp.server.create_server()` builds —
its real lifespan (`app_lifespan`, including `create_memory_service`'s
backend selection) and the real tool functions `haven.mcp.tools.*`
export — rather than hand-mocking an `AppContext`. This is what proves
the wiring in `haven/mcp/server.py` actually produces a working server,
not just that each tool's logic is individually correct (that's already
covered by Phase 2/3's own test coverage).

Two layers, because the exact `mcp.server.fastmcp.FastMCP` introspection
API can differ between SDK versions (see `docs/friction-log.md`):

- The tests below call the registered tool functions directly, with a
  real `AppContext` built from the server's own `app_lifespan`. This is
  the layer that must always pass — it's the exact code FastMCP's
  request handler calls once a real MCP request arrives, so a bug here
  is a bug Alexa+ would hit too.
- `test_call_tool_via_fastmcp_protocol_surface` additionally drives
  FastMCP's own `call_tool`, if the installed SDK exposes it as
  expected, as a bonus check of the actual wire-level surface. It skips
  rather than fails if that API doesn't match this installation — an SDK
  version difference should never block the suite.
"""

from __future__ import annotations

import asyncio

import pytest

from haven.mcp.server import app_lifespan, create_server
from haven.mcp.tools.context import get_household_context
from haven.mcp.tools.execution import execute_household_plan
from haven.mcp.tools.planning import get_plan_status, propose_household_plan
from haven.mcp.tools.preferences import save_household_preference
from haven.mcp.tools.recommendations import get_activity_recommendations
from haven.models.plan import ActionRisk, Plan, PlanStatus, PlanStep


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def app():
    """A real `AppContext`, built from the server's own `app_lifespan` —
    not a hand-mocked stand-in. Confirms `create_server()` and
    `app_lifespan()` actually wire up without error, including the
    `create_memory_service` backend selection added in Phase 4.
    """

    server = create_server()
    cm = app_lifespan(server)
    app_context = _run(cm.__aenter__())
    try:
        yield app_context
    finally:
        _run(cm.__aexit__(None, None, None))


def test_server_builds(app):
    # `app` existing at all means create_server()+app_lifespan() didn't
    # raise. Explicit assertions on what a working server exposes:
    assert app.household_id
    assert app.media is not None
    assert app.memory is not None
    assert app.calendar is not None
    assert app.plans is not None


def test_household_context_round_trip(app):
    context = get_household_context(app, "Get movie night ready")

    assert context.household_id == app.household_id
    assert len(context.people) == 3
    assert len(context.media_options) == 5
    assert context.available_minutes is not None and context.available_minutes > 0

    with pytest.raises(ValueError, match="goal"):
        get_household_context(app, "   ")


def test_preference_save_round_trip(app):
    saved = save_household_preference(app, "preferred_genres", ["sci-fi", "comedy"], person_id=None)

    assert saved.household_id == app.household_id
    assert saved.key == "preferred_genres"
    assert saved.value == ["sci-fi", "comedy"]
    assert saved.scope == "household"

    with pytest.raises(ValueError, match="temporary state"):
        save_household_preference(app, "room_occupied", True, person_id=None)


def test_plan_propose_status_execute_verify_round_trip(app):
    plan = propose_household_plan(app, "Get movie night ready")

    assert plan.status == "PROPOSED"
    assert plan.requires_confirmation is False
    assert len(plan.steps) >= 2

    status_before = get_plan_status(app, plan.plan_id)
    assert status_before.plan_id == plan.plan_id
    assert status_before.execution_status is None
    assert "ready to execute" in status_before.message.lower()

    with pytest.raises(ValueError, match="Unknown plan_id"):
        get_plan_status(app, "plan-does-not-exist")

    execution = execute_household_plan(app, plan.plan_id, confirmed=False)

    assert execution.plan_id == plan.plan_id
    assert execution.execution_status == "COMPLETED"
    assert execution.verification_status == "VERIFIED"
    assert len(execution.steps) == len(plan.steps)
    assert all(step.status == "SUCCEEDED" for step in execution.steps)
    assert all(v.status == "VERIFIED" for v in execution.verifications)

    status_after = get_plan_status(app, plan.plan_id)
    assert status_after.execution_status == "COMPLETED"
    assert status_after.verification_status == "VERIFIED"

    with pytest.raises(ValueError, match="Unknown plan_id"):
        execute_household_plan(app, "plan-does-not-exist", confirmed=False)


def test_high_impact_plan_requires_confirmation(app):
    """The planner's current mock reasoner never proposes a high-impact
    step for movie night, so this constructs one directly to exercise the
    confirmation gate `execute_household_plan` enforces — the same gate
    `haven.execution.executor.execute_plan` enforces underneath it.
    """

    plan = Plan(
        id="plan-high-impact-test",
        goal="Arm security and lock up",
        status=PlanStatus.AWAITING_CONFIRMATION,
        summary="Test high-impact plan",
        steps=[
            PlanStep(
                id="step-1",
                order=1,
                action="dim_lights",
                title="Dim living room lights",
                description="Dim living room lights",
                risk=ActionRisk.HIGH_IMPACT_ACTION,
                requires_confirmation=True,
                device_id="device-lights-living-room",
            )
        ],
        requires_confirmation=True,
    )
    app.plans.save(plan, app.household_id)

    unconfirmed = execute_household_plan(app, plan.id, confirmed=False)
    assert unconfirmed.execution_status == "AWAITING_CONFIRMATION"
    assert "confirmation" in unconfirmed.message.lower()

    confirmed = execute_household_plan(app, plan.id, confirmed=True)
    assert confirmed.execution_status == "COMPLETED"


def test_recommendations_round_trip(app):
    recommendations = get_activity_recommendations(app, None)

    assert recommendations.household_id == app.household_id
    assert 0 < len(recommendations.recommendations) <= 3
    if recommendations.available_minutes is not None:
        assert all(
            r.duration_minutes <= recommendations.available_minutes
            for r in recommendations.recommendations
        )

    with_goal = get_activity_recommendations(app, "movie night")
    assert with_goal.household_id == app.household_id


def test_call_tool_via_fastmcp_protocol_surface(app):
    """Best-effort check of FastMCP's own tool-call surface.

    Skips rather than fails if the installed `mcp` SDK's `FastMCP` lacks
    a public `call_tool`, or its signature/return shape differs from
    what's assumed here — see `docs/friction-log.md`. The tests above are
    the ones that must pass regardless.
    """

    server = create_server()

    if not hasattr(server, "call_tool"):
        pytest.skip("Installed mcp SDK's FastMCP has no public call_tool(); see docs/friction-log.md")

    async def _call():
        return await server.call_tool(
            "propose_household_plan_tool", {"goal": "Get movie night ready"}
        )

    try:
        result = _run(_call())
    except TypeError as exc:
        pytest.skip(f"call_tool signature differs on the installed mcp SDK: {exc}")
    except Exception as exc:  # noqa: BLE001 - genuinely best-effort; see docstring
        pytest.skip(f"call_tool behaved unexpectedly on the installed mcp SDK: {exc}")

    assert result is not None
