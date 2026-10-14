"""Movie Night workflow: goal + context -> proposed plan (no regression from Phase 2).

Covers the planner end to end with the mock backend, the Bedrock structured
boundary with a fake model, and the real simulator-backed context path that
the MCP server uses.
"""

from __future__ import annotations

import pytest

from haven.agents.bedrock_reasoner import BedrockReasoner, ModelDecisionError, ModelPlanDecision
from haven.agents.planner import create_plan
from haven.agents.policies import validate_plan_policy
from haven.config import HavenConfig
from haven.models.context import DeviceCapability, DeviceContext, HouseholdContext, MediaItem
from haven.models.plan import ActionRisk, Plan, PlanStatus
from haven.planning.plan_service import create_household_plan
from haven.planning.workflows import WorkflowType, classify_workflow

GOAL = "Get movie night ready"


def _plan(context: HouseholdContext, goal: str = GOAL) -> Plan:
    return create_household_plan(goal, context, config=HavenConfig(planner_backend="mock"))


def test_goal_is_classified_as_movie_night() -> None:
    assert classify_workflow(GOAL) is WorkflowType.MOVIE_NIGHT


def test_full_plan_shape(movie_night_context: HouseholdContext) -> None:
    plan = _plan(movie_night_context)

    assert plan.status is PlanStatus.PROPOSED
    assert not plan.clarification_needed and plan.clarification_prompt is None
    assert [s.action for s in plan.steps] == ["select_media", "prepare_media_session", "dim_lights"]
    assert [s.order for s in plan.steps] == [1, 2, 3]
    assert all(s.risk is not ActionRisk.HIGH_IMPACT_ACTION for s in plan.steps)
    assert not plan.requires_confirmation
    assert validate_plan_policy(plan).valid


def test_selects_title_that_fits_available_time(movie_night_context: HouseholdContext) -> None:
    plan = _plan(movie_night_context)
    select = plan.steps[0]
    assert "Short Feature" in select.description
    assert "Long Epic" not in select.description
    assert "Unavailable Film" not in select.description


def test_unknown_available_time_does_not_reject_on_time(
    movie_night_context: HouseholdContext,
) -> None:
    ctx = movie_night_context.model_copy(update={"available_minutes": None})
    plan = _plan(ctx)
    assert "Long Epic" in plan.steps[0].description  # first available option


def test_prefers_media_device_in_preferred_room(movie_night_context: HouseholdContext) -> None:
    bedroom_tv = DeviceContext(
        id="tv-2",
        name="Bedroom TV",
        room_id="bedroom",
        capabilities=[DeviceCapability.MEDIA_PLAYBACK],
    )
    ctx = movie_night_context.model_copy(
        update={
            "devices": [bedroom_tv, *movie_night_context.devices],
            "preferred_room_id": "living-room",
        }
    )
    plan = _plan(ctx)
    prepare = plan.steps[1]
    assert prepare.device_id == "tv-1" and prepare.room_id == "living-room"


def test_falls_back_to_any_media_device_when_preferred_room_has_none(
    movie_night_context: HouseholdContext,
) -> None:
    ctx = movie_night_context.model_copy(update={"preferred_room_id": "bedroom"})
    plan = _plan(ctx)
    assert plan.steps[1].device_id == "tv-1"


def test_dims_only_lights_in_the_viewing_room(movie_night_context: HouseholdContext) -> None:
    bedroom_lamp = DeviceContext(
        id="lamp-2", name="Bedroom Lamp", room_id="bedroom", capabilities=["lighting"]
    )
    ctx = movie_night_context.model_copy(
        update={"devices": [*movie_night_context.devices, bedroom_lamp]}
    )
    plan = _plan(ctx)
    dim_steps = [s for s in plan.steps if s.action == "dim_lights"]
    assert [s.device_id for s in dim_steps] == ["lamp-1"]


def test_unavailable_light_is_skipped(movie_night_context: HouseholdContext) -> None:
    devices = [
        d.model_copy(update={"is_available": False}) if d.id == "lamp-1" else d
        for d in movie_night_context.devices
    ]
    plan = _plan(movie_night_context.model_copy(update={"devices": devices}))
    assert [s.action for s in plan.steps] == ["select_media", "prepare_media_session"]


def test_never_touches_the_lock(movie_night_context: HouseholdContext) -> None:
    plan = _plan(movie_night_context)
    assert all(s.device_id != "lock-1" for s in plan.steps)
    assert all(s.capability != DeviceCapability.LOCK for s in plan.steps)


@pytest.mark.parametrize(
    ("update", "expected_question"),
    [
        ({"available_minutes": 10}, "available time"),
        ({"media_options": []}, "available time"),
        ({"devices": []}, "media playback device"),
    ],
)
def test_missing_context_yields_clarification_plan(
    movie_night_context: HouseholdContext, update: dict, expected_question: str
) -> None:
    plan = _plan(movie_night_context.model_copy(update=update))
    assert plan.clarification_needed is True
    assert plan.steps == []
    assert plan.status is PlanStatus.PROPOSED and not plan.requires_confirmation
    assert plan.clarification_prompt and expected_question in plan.clarification_prompt
    assert validate_plan_policy(plan).valid


def test_short_media_still_fits_tight_schedule(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(
        update={
            "available_minutes": 25,
            "media_options": [
                *movie_night_context.media_options,
                MediaItem(id="m-tiny", title="Short Short", duration_minutes=20),
            ],
        }
    )
    plan = _plan(ctx)
    assert "Short Short" in plan.steps[0].description


# --- Bedrock structured boundary ------------------------------------------- #


def _reasoner(decision: ModelPlanDecision) -> BedrockReasoner:
    return BedrockReasoner(invoke_model=lambda system_prompt, prompt: decision)


def test_model_may_reorder_and_drop_but_not_invent(movie_night_context: HouseholdContext) -> None:
    decision = ModelPlanDecision(
        summary="Lights first, then queue the film.",
        selected_candidate_ids=["candidate-3", "candidate-2"],
    )
    plan = create_plan(GOAL, movie_night_context, reasoner=_reasoner(decision))
    assert [s.action for s in plan.steps] == ["dim_lights", "prepare_media_session"]
    assert plan.summary == "Lights first, then queue the film."
    assert validate_plan_policy(plan).valid


@pytest.mark.parametrize(
    "ids",
    [
        ["candidate-9"],  # unknown
        ["candidate-1", "candidate-1"],  # duplicate
        ["step-1"],  # wrong namespace
    ],
)
def test_model_cannot_select_outside_offered_candidates(
    movie_night_context: HouseholdContext, ids: list[str]
) -> None:
    decision = ModelPlanDecision(summary="bad", selected_candidate_ids=ids)
    with pytest.raises(ModelDecisionError):
        create_plan(GOAL, movie_night_context, reasoner=_reasoner(decision))


def test_model_clarification_becomes_clarification_plan(
    movie_night_context: HouseholdContext,
) -> None:
    decision = ModelPlanDecision(
        summary="",
        selected_candidate_ids=[],
        clarification_needed=True,
        clarification_prompt="Which film would you like tonight?",
    )
    plan = create_plan(GOAL, movie_night_context, reasoner=_reasoner(decision))
    assert plan.clarification_needed
    assert plan.clarification_prompt == "Which film would you like tonight?"
    assert plan.steps == []


# --- Real simulator-backed context (the path the MCP server uses) ------------ #


def test_movie_night_from_simulator_context() -> None:
    """Person B's resolver + simulators feed Person A's planner unchanged."""

    from haven.context.resolver import resolve_context
    from haven.context.sources import DeviceStateSource, SensorSource, UserStatementSource
    from haven.integrations.calendar import demo_calendar
    from haven.integrations.media import MediaService
    from simulator.household.service import HouseholdSimulatorService
    from simulator.media.service import MediaSimulatorService

    household = HouseholdSimulatorService()
    snapshot = household.get_snapshot()
    media = MediaService(MediaSimulatorService())
    sources = [UserStatementSource(), DeviceStateSource(household), SensorSource(household)]
    context = resolve_context(GOAL, snapshot, media.list_media_items(), sources)
    context = context.model_copy(update={"available_minutes": demo_calendar().available_minutes()})

    plan = _plan(context)

    assert not plan.clarification_needed
    assert plan.steps and plan.steps[0].action == "select_media"
    device_ids = {d.id for d in context.devices}
    assert all(s.device_id is None or s.device_id in device_ids for s in plan.steps)
    assert all(not s.requires_confirmation for s in plan.steps)
