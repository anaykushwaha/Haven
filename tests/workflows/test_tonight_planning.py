"""Tonight planning: "What should we do tonight?" -> suggestions or a question.

This workflow only ever proposes READ steps. When the planner lacks the
context to suggest anything sensible it returns a clarification plan rather
than guessing or touching devices.
"""

from __future__ import annotations

import pytest

from haven.agents.bedrock_reasoner import BedrockReasoner, ModelPlanDecision
from haven.agents.planner import create_plan, generate_candidates
from haven.agents.policies import validate_plan_policy
from haven.agents.prompts import build_planning_prompt
from haven.config import HavenConfig
from haven.memory.activity import ActivityType
from haven.memory.service import MemoryService
from haven.models.context import HouseholdContext, MediaItem
from haven.models.plan import ActionRisk, Plan, PlanStatus
from haven.planning.plan_service import create_household_plan
from haven.planning.workflows import WorkflowType, classify_workflow

GOAL = "What should we do tonight?"


def _plan(context: HouseholdContext, goal: str = GOAL) -> Plan:
    return create_household_plan(goal, context, config=HavenConfig(planner_backend="mock"))


@pytest.mark.parametrize(
    "goal", [GOAL, "Any ideas for this evening?", "we have the evening free, what could we do"]
)
def test_goal_variants_are_tonight_planning(goal: str) -> None:
    assert classify_workflow(goal) is WorkflowType.TONIGHT_PLANNING


def test_structured_suggestion_plan(movie_night_context: HouseholdContext) -> None:
    plan = _plan(movie_night_context)

    assert isinstance(plan, Plan)
    assert plan.status is PlanStatus.PROPOSED
    assert not plan.clarification_needed
    assert [s.action for s in plan.steps] == ["review_schedule", "suggest_media"]
    assert all(s.risk is ActionRisk.READ for s in plan.steps)
    assert all(s.device_id is None for s in plan.steps)
    assert not plan.requires_confirmation
    assert validate_plan_policy(plan).valid


def test_suggests_the_longest_title_that_fits(movie_night_context: HouseholdContext) -> None:
    # 120 minutes available: Long Epic (145) does not fit; Short Feature (95) does.
    plan = _plan(movie_night_context)
    assert "Short Feature" in plan.steps[1].title
    assert "120 minutes" in plan.steps[0].description

    roomy = movie_night_context.model_copy(update={"available_minutes": 180})
    assert "Long Epic" in _plan(roomy).steps[1].title


def test_mentions_known_genre_preferences_only_when_present(
    movie_night_context: HouseholdContext,
) -> None:
    plain = _plan(movie_night_context)
    assert "usually prefers" not in plain.steps[1].description

    with_genres = movie_night_context.model_copy(update={"preferred_genres": ["comedy", "drama"]})
    assert "comedy, drama" in _plan(with_genres).steps[1].description


def test_unknown_available_time_asks_for_clarification(
    movie_night_context: HouseholdContext,
) -> None:
    ctx = movie_night_context.model_copy(update={"available_minutes": None})
    plan = _plan(ctx)

    assert plan.clarification_needed is True
    assert plan.steps == []
    assert plan.clarification_prompt and "How much time" in plan.clarification_prompt
    assert plan.status is PlanStatus.PROPOSED and not plan.requires_confirmation
    assert validate_plan_policy(plan).valid


def test_short_window_suggests_wind_down_instead_of_a_feature(
    movie_night_context: HouseholdContext,
) -> None:
    ctx = movie_night_context.model_copy(update={"available_minutes": 20})
    plan = _plan(ctx)
    assert [s.action for s in plan.steps] == ["review_schedule", "suggest_wind_down"]
    assert all(s.risk is ActionRisk.READ for s in plan.steps)


def test_enough_time_but_nothing_fits_asks_instead_of_guessing(
    movie_night_context: HouseholdContext,
) -> None:
    ctx = movie_night_context.model_copy(
        update={
            "available_minutes": 60,
            "media_options": [MediaItem(id="m-x", title="Too Long", duration_minutes=200)],
        }
    )
    plan = _plan(ctx)
    assert plan.clarification_needed
    assert plan.clarification_prompt and "media library" in plan.clarification_prompt


def test_tonight_never_proposes_device_actions(movie_night_context: HouseholdContext) -> None:
    for minutes in (20, 60, 120, 240):
        ctx = movie_night_context.model_copy(update={"available_minutes": minutes})
        for candidate in generate_candidates(GOAL, ctx):
            assert candidate.risk is ActionRisk.READ
            assert candidate.required_device_id is None
            assert candidate.required_capabilities == []


def test_prompt_names_the_workflow_and_only_offered_candidates(
    movie_night_context: HouseholdContext,
) -> None:
    from haven.agents.bedrock_reasoner import offer_candidates

    candidates = generate_candidates(GOAL, movie_night_context)
    offered = list(offer_candidates(candidates).values())
    prompt = build_planning_prompt(GOAL, movie_night_context, offered)
    assert "Workflow: tonight_planning" in prompt
    assert "candidate-1" in prompt and "candidate-2" in prompt
    assert "candidate-3" not in prompt
    assert "lock-1" not in prompt and "tv-1" not in prompt  # no devices referenced


def test_model_can_keep_only_the_suggestion(movie_night_context: HouseholdContext) -> None:
    decision = ModelPlanDecision(summary="Just the film.", selected_candidate_ids=["candidate-2"])
    reasoner = BedrockReasoner(invoke_model=lambda system_prompt, prompt: decision)
    plan = create_plan(GOAL, movie_night_context, reasoner=reasoner)
    assert [s.action for s in plan.steps] == ["suggest_media"]
    assert plan.steps[0].risk is ActionRisk.READ


def test_plan_history_records_workflow_but_not_clarifications(
    movie_night_context: HouseholdContext,
) -> None:
    memory = MemoryService(default_household_id=movie_night_context.household_id)

    proposed = _plan(movie_night_context)
    record = memory.record_plan_proposed(movie_night_context.household_id, proposed)
    assert record is not None and record.details["workflow"] == "tonight_planning"

    question = _plan(movie_night_context.model_copy(update={"available_minutes": None}))
    assert memory.record_plan_proposed(movie_night_context.household_id, question) is None
    history = memory.list_activity(
        movie_night_context.household_id, activity_type=ActivityType.PLAN_PROPOSED
    )
    assert [r.details["plan_id"] for r in history] == [proposed.id]
