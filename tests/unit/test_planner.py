from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from haven.agents.planner import assemble_plan, candidate_to_step, create_plan, generate_candidates
from haven.agents.policies import validate_plan_policy
from haven.agents.prompts import PLANNER_SYSTEM_PROMPT, build_planning_prompt
from haven.config import HavenConfig
from haven.models.context import DeviceCapability, DeviceContext, HouseholdContext
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


def _assert_clarification(plan: Plan, match: str) -> None:
    assert plan.clarification_needed is True
    assert plan.steps == []
    assert plan.status is PlanStatus.PROPOSED
    assert not plan.requires_confirmation
    assert plan.clarification_prompt and match in plan.clarification_prompt


def test_no_media_device_asks_for_clarification(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(
        update={"devices": [d for d in movie_night_context.devices if d.id != "tv-1"]}
    )
    _assert_clarification(create_plan("Get movie night ready", ctx), "media playback device")


def test_no_fitting_media_asks_for_clarification(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(update={"available_minutes": 30})
    _assert_clarification(create_plan("Get movie night ready", ctx), "available time")


def test_unknown_goal_asks_for_clarification(movie_night_context: HouseholdContext) -> None:
    _assert_clarification(
        create_plan("Launch the rocket", movie_night_context), "does not yet know how to plan"
    )


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
    root = Path(__file__).resolve().parents[2]
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


# --- Leaving home (planner support only; execution tests belong to Person B) --- #


def _security_context(movie_night_context: HouseholdContext) -> HouseholdContext:
    alarm = DeviceContext(
        id="alarm-1", name="Alarm Panel", room_id=None, capabilities=[DeviceCapability.SECURITY]
    )
    thermostat = DeviceContext(
        id="thermo-1",
        name="Hall Thermostat",
        room_id="living-room",
        capabilities=[DeviceCapability.THERMOSTAT],
    )
    return movie_night_context.model_copy(
        update={"devices": [*movie_night_context.devices, alarm, thermostat]}
    )


def test_leaving_home_plan_is_conservative(movie_night_context: HouseholdContext) -> None:
    plan = create_plan("We're leaving the house", movie_night_context)

    assert [s.action for s in plan.steps] == ["turn_off_lights", "stop_media", "lock_door"]
    assert plan.status is PlanStatus.AWAITING_CONFIRMATION
    assert plan.requires_confirmation
    lock = plan.steps[-1]
    assert lock.risk is ActionRisk.HIGH_IMPACT_ACTION and lock.requires_confirmation
    assert lock.device_id == "lock-1"
    assert all(not s.requires_confirmation for s in plan.steps[:-1])
    assert validate_plan_policy(plan).valid


def test_leaving_home_never_assumes_a_security_system(
    movie_night_context: HouseholdContext,
) -> None:
    plan = create_plan("Heading out, lock up", movie_night_context)
    assert all(s.action != "arm_security_system" for s in plan.steps)
    assert all(s.capability != DeviceCapability.SECURITY for s in plan.steps)


def test_leaving_home_arms_security_only_when_present_and_with_confirmation(
    movie_night_context: HouseholdContext,
) -> None:
    plan = create_plan("We're leaving the house", _security_context(movie_night_context))

    actions = [s.action for s in plan.steps]
    assert actions == [
        "turn_off_lights",
        "stop_media",
        "set_away_temperature",
        "lock_door",
        "arm_security_system",
    ]
    arm = plan.steps[-1]
    assert arm.risk is ActionRisk.HIGH_IMPACT_ACTION and arm.requires_confirmation
    assert arm.device_id == "alarm-1"
    # Someone is still marked present: the step says so, but presence is not persisted.
    assert "appear to be home" in arm.description
    assert plan.status is PlanStatus.AWAITING_CONFIRMATION


def test_leaving_home_without_devices_asks(movie_night_context: HouseholdContext) -> None:
    plan = create_plan(
        "We're leaving the house", movie_night_context.model_copy(update={"devices": []})
    )
    _assert_clarification(plan, "leaving")


def test_leaving_home_skips_unavailable_devices(movie_night_context: HouseholdContext) -> None:
    devices = [
        d.model_copy(update={"is_available": False}) if d.id == "lock-1" else d
        for d in movie_night_context.devices
    ]
    plan = create_plan(
        "We're leaving the house", movie_night_context.model_copy(update={"devices": devices})
    )
    assert [s.action for s in plan.steps] == ["turn_off_lights", "stop_media"]
    assert plan.status is PlanStatus.PROPOSED and not plan.requires_confirmation


def test_high_impact_candidates_survive_constraints_unchanged(
    movie_night_context: HouseholdContext,
) -> None:
    from haven.planning.constraints import apply_constraints

    goal = "We're leaving the house"
    candidates = generate_candidates(goal, movie_night_context)
    result = apply_constraints(goal, movie_night_context, candidates)
    assert result.rejected == []
    assert [c.risk for c in result.accepted if c.action == "lock_door"] == [
        ActionRisk.HIGH_IMPACT_ACTION
    ]


# --- Plan assembly normalisation --------------------------------------------- #


def _reasoned_with(step: PlanStep) -> ReasonedPlan:
    return ReasonedPlan(summary="s", steps=[step])


def test_assemble_plan_forces_confirmation_for_high_impact() -> None:
    downgraded = PlanStep(
        id="step-9",
        order=7,
        action="lock_door",
        title="Lock",
        description="d",
        risk=ActionRisk.HIGH_IMPACT_ACTION,
        requires_confirmation=False,  # a reasoner tried to remove confirmation
    )
    plan = assemble_plan("goal", _reasoned_with(downgraded))
    assert plan.steps[0].requires_confirmation is True
    assert plan.steps[0].order == 1
    assert plan.status is PlanStatus.AWAITING_CONFIRMATION
    assert plan.requires_confirmation
    assert validate_plan_policy(plan).valid


def test_assemble_plan_clears_spurious_confirmation_on_read_steps() -> None:
    step = PlanStep(
        id="step-1",
        order=1,
        action="select_media",
        title="t",
        description="d",
        risk=ActionRisk.READ,
        requires_confirmation=True,
    )
    plan = assemble_plan("goal", _reasoned_with(step))
    assert plan.steps[0].requires_confirmation is False
    assert plan.status is PlanStatus.PROPOSED


def test_assemble_plan_rejects_no_steps_without_clarification() -> None:
    with pytest.raises(ValueError, match="unable to produce"):
        assemble_plan("goal", ReasonedPlan(summary="", steps=[]))


def test_assemble_plan_clarification_without_prompt_gets_default() -> None:
    plan = assemble_plan("goal", ReasonedPlan(summary="", steps=[], clarification_needed=True))
    assert plan.clarification_needed and plan.clarification_prompt
    assert plan.rejected_candidate_count == 0


def test_plan_service_enforces_max_steps(movie_night_context: HouseholdContext) -> None:
    config = HavenConfig(planner_backend="mock", max_plan_steps=2)
    with pytest.raises(ValueError, match="limit is 2"):
        create_household_plan("Get movie night ready", movie_night_context, config=config)


def test_plan_service_returns_clarification_plan(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(update={"devices": []})
    plan = create_household_plan("Get movie night ready", ctx, config=HavenConfig())
    assert plan.clarification_needed and plan.steps == []


def test_plan_ids_are_unique(movie_night_context: HouseholdContext) -> None:
    ids = {create_plan("Get movie night ready", movie_night_context).id for _ in range(5)}
    assert len(ids) == 5 and all(i.startswith("plan-") for i in ids)


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
