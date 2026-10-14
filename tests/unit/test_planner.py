from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from haven.agents.planner import candidate_to_step, create_plan
from haven.agents.prompts import PLANNER_SYSTEM_PROMPT, build_planning_prompt
from haven.config import HavenConfig
from haven.models.context import HouseholdContext
from haven.models.plan import ActionRisk, Plan, PlanStatus, PlanStep
from haven.planning.models import ReasonedPlan
from haven.planning.plan_service import create_household_plan


def test_movie_night_plan_is_structured(movie_night_context: HouseholdContext) -> None:
    plan = create_plan("Get movie night ready", movie_night_context)

    assert isinstance(plan, Plan)
    assert plan.goal == "Get movie night ready"
    assert plan.status is PlanStatus.PROPOSED
    assert not plan.requires_confirmation
    assert [s.action for s in plan.steps] == ["select_media", "prepare_media_session", "dim_lights"]
    assert [s.order for s in plan.steps] == [1, 2, 3]
    assert all(isinstance(s, PlanStep) for s in plan.steps)
    assert plan.rejected_candidate_count == 0


def test_movie_night_respects_time_constraint(movie_night_context: HouseholdContext) -> None:
    plan = create_plan("Get movie night ready", movie_night_context)
    select = plan.steps[0]
    assert "Short Feature" in select.description
    assert "Long Epic" not in select.description


def test_movie_night_uses_only_existing_devices(movie_night_context: HouseholdContext) -> None:
    plan = create_plan("Get movie night ready", movie_night_context)
    device_ids = {d.id for d in movie_night_context.devices}
    for step in plan.steps:
        assert step.device_id is None or step.device_id in device_ids
        assert step.risk in (ActionRisk.READ, ActionRisk.REVERSIBLE_ACTION)
        assert step.requires_confirmation is False


def test_movie_night_without_lights_omits_dim_step(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(
        update={"devices": [d for d in movie_night_context.devices if d.id != "lamp-1"]}
    )
    plan = create_plan("Get movie night ready", ctx)
    assert [s.action for s in plan.steps] == ["select_media", "prepare_media_session"]


def test_no_media_device_raises(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(
        update={"devices": [d for d in movie_night_context.devices if d.id != "tv-1"]}
    )
    with pytest.raises(ValueError, match="media playback device"):
        create_plan("Get movie night ready", ctx)


def test_no_fitting_media_raises(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(update={"available_minutes": 30})
    with pytest.raises(ValueError, match="available time"):
        create_plan("Get movie night ready", ctx)


def test_unknown_goal_raises(movie_night_context: HouseholdContext) -> None:
    with pytest.raises(ValueError):
        create_plan("Launch the rocket", movie_night_context)


@pytest.mark.parametrize("goal", ["", "   "])
def test_empty_goal_raises(goal: str, movie_night_context: HouseholdContext) -> None:
    with pytest.raises(ValueError, match="empty"):
        create_plan(goal, movie_night_context)


def test_invalid_context_raises() -> None:
    with pytest.raises(ValueError, match="HouseholdContext"):
        create_plan("Get movie night ready", None)  # type: ignore[arg-type]


def test_plan_service_returns_policy_valid_plan(movie_night_context: HouseholdContext) -> None:
    plan = create_household_plan("Get movie night ready", movie_night_context)
    assert isinstance(plan, Plan)
    assert plan.steps


def test_prompt_builder_includes_goal_and_context(movie_night_context: HouseholdContext) -> None:
    prompt = build_planning_prompt("Get movie night ready", movie_night_context)
    assert "Get movie night ready" in prompt
    assert "household-1" in prompt
    assert "HIGH_IMPACT_ACTION" in prompt
    assert "Do not execute" in PLANNER_SYSTEM_PROMPT or "not execute" in PLANNER_SYSTEM_PROMPT


_FORBIDDEN_MODULES = ("boto3", "botocore", "strands", "ask_sdk_core")


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root)])}
    env.pop("HAVEN_PLANNER_BACKEND", None)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )


def test_mock_planner_import_does_not_load_aws_modules() -> None:
    # Runs in a fresh interpreter so other tests importing Strands cannot
    # influence the result.
    code = (
        "import sys\n"
        "import haven.agents.planner, haven.planning.plan_service\n"
        f"hit = {set(_FORBIDDEN_MODULES)!r} & set(sys.modules)\n"
        "print(sorted(hit))\n"
        "sys.exit(1 if hit else 0)\n"
    )
    result = _run_isolated(code)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mock_planning_runs_offline_without_aws_modules(
    movie_night_context: HouseholdContext,
) -> None:
    code = (
        "import sys, json\n"
        "from haven.models.context import HouseholdContext\n"
        "from haven.planning.plan_service import create_household_plan\n"
        f"ctx = HouseholdContext.model_validate_json({movie_night_context.model_dump_json()!r})\n"
        "plan = create_household_plan('Get movie night ready', ctx)\n"
        f"hit = {set(_FORBIDDEN_MODULES)!r} & set(sys.modules)\n"
        "print(len(plan.steps), sorted(hit))\n"
        "sys.exit(1 if hit or not plan.steps else 0)\n"
    )
    result = _run_isolated(code)
    assert result.returncode == 0, result.stdout + result.stderr


def test_create_plan_accepts_explicit_mock_backend(movie_night_context: HouseholdContext) -> None:
    plan = create_plan("Get movie night ready", movie_night_context, backend="mock")
    assert len(plan.steps) == 3


def test_create_plan_accepts_injected_reasoner(movie_night_context: HouseholdContext) -> None:
    seen: dict[str, int] = {}

    def reasoner(goal, context, candidates):  # type: ignore[no-untyped-def]
        seen["count"] = len(candidates)
        return ReasonedPlan(
            summary="only the first",
            steps=[candidate_to_step(candidates[0], 1)],
        )

    plan = create_plan("Get movie night ready", movie_night_context, reasoner=reasoner)
    assert seen["count"] == 3
    assert [s.action for s in plan.steps] == ["select_media"]
    assert plan.summary == "only the first"


def test_unknown_backend_raises(movie_night_context: HouseholdContext) -> None:
    with pytest.raises(ValueError, match="unsupported planner backend"):
        create_plan("Get movie night ready", movie_night_context, backend="quantum")


def test_select_reasoner_by_backend() -> None:
    from haven.agents.bedrock_reasoner import BedrockReasoner
    from haven.agents.planner import _mock_reasoner, select_reasoner

    assert select_reasoner("mock") is _mock_reasoner
    assert isinstance(select_reasoner("bedrock"), BedrockReasoner)
    with pytest.raises(ValueError):
        select_reasoner("nope")


def test_select_reasoner_defaults_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from haven.agents.planner import _mock_reasoner, select_reasoner

    monkeypatch.delenv("HAVEN_PLANNER_BACKEND", raising=False)
    assert select_reasoner() is _mock_reasoner
    monkeypatch.setenv("HAVEN_PLANNER_BACKEND", "bedrock")
    assert type(select_reasoner()).__name__ == "BedrockReasoner"
    monkeypatch.setenv("HAVEN_PLANNER_BACKEND", "other")
    with pytest.raises(ValueError):
        select_reasoner()


def test_plan_service_uses_configured_backend(movie_night_context: HouseholdContext) -> None:
    config = HavenConfig(planner_backend="mock")
    plan = create_household_plan("Get movie night ready", movie_night_context, config=config)
    assert plan.steps


def test_candidate_prompt_lists_only_offered_candidates(
    movie_night_context: HouseholdContext,
) -> None:
    from haven.agents.bedrock_reasoner import offer_candidates
    from haven.agents.planner import generate_candidates

    candidates = generate_candidates("Get movie night ready", movie_night_context)
    offered = list(offer_candidates(candidates).values())
    prompt = build_planning_prompt("Get movie night ready", movie_night_context, offered)

    assert "candidate-1" in prompt and "candidate-3" in prompt
    assert "ONLY among the offered candidate" in prompt
    assert "Do not reintroduce" in prompt
    assert "selected_candidate_ids" in prompt
    # Minimal context: the front-door lock and people are not relevant to these candidates.
    assert "lock-1" not in prompt
    assert "Ari" not in prompt
    assert "tv-1" in prompt and "Short Feature" in prompt
