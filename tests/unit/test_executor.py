"""Unit tests for the deterministic executor and its action handlers."""

from __future__ import annotations

import pytest

from haven.execution import actions
from haven.execution.actions import extract_quoted_title, supported_actions
from haven.execution.executor import execute_plan, run_step
from haven.execution.results import ExecutionStatus, StepExecutionResult, StepExecutionStatus
from haven.memory.activity import ActivityType
from haven.models.plan import ActionRisk, Plan, PlanStatus
from tests.fixtures.plans import (
    BACK_DOOR,
    BEDROOM_LIGHTS,
    FRONT_DOOR,
    KITCHEN_LIGHTS,
    LIVING_ROOM_LIGHTS,
    SECURITY_SYSTEM,
    THERMOSTAT,
    TV,
    clarification_plan,
    lock_front_door_plan,
    make_plan,
    make_step,
)


def _run(plan, household_sim, media_service, **kwargs):
    return execute_plan(plan, household=household_sim, media=media_service, **kwargs)


def _device_state(household_sim, device_id):
    return household_sim.get_device_state(device_id)["state"]


class RecordingMemory:
    def __init__(self):
        self.calls = []

    def record_activity(self, household_id, activity_type, summary, *, details=None):
        self.calls.append((household_id, activity_type, summary, details))


class BrokenMemory:
    def record_activity(self, *args, **kwargs):
        raise RuntimeError("memory is down")


# --------------------------------------------------------------------------- #
# Whole plans
# --------------------------------------------------------------------------- #


def test_movie_night_plan_changes_the_household(plan_for, household_sim, media_service):
    plan = plan_for("Get movie night ready")

    result = _run(plan, household_sim, media_service)

    assert result.status is ExecutionStatus.COMPLETED
    assert [r.action for r in result.step_results] == [
        "select_media",
        "prepare_media_session",
        "dim_lights",
    ]
    assert all(r.status is StepExecutionStatus.SUCCEEDED for r in result.step_results)
    assert _device_state(household_sim, LIVING_ROOM_LIGHTS)["brightness"] == 20
    session = media_service.get_session_state(TV)
    assert session is not None and session.status.value == "ready"


def test_leaving_home_requires_confirmation_before_anything_runs(
    plan_for, household_sim, media_service
):
    plan = plan_for("We're leaving the house")
    assert plan.status is PlanStatus.AWAITING_CONFIRMATION

    result = _run(plan, household_sim, media_service)

    assert result.status is ExecutionStatus.AWAITING_CONFIRMATION
    assert result.step_results == []
    assert _device_state(household_sim, LIVING_ROOM_LIGHTS)["power"] == "on"
    assert _device_state(household_sim, FRONT_DOOR)["locked"] is False
    assert _device_state(household_sim, SECURITY_SYSTEM)["armed"] is False


def test_confirmed_leaving_home_secures_the_house(plan_for, household_sim, media_service):
    plan = plan_for("We're leaving the house")

    result = _run(plan, household_sim, media_service, confirmed=True)

    assert result.status is ExecutionStatus.COMPLETED
    for light in (LIVING_ROOM_LIGHTS, KITCHEN_LIGHTS, BEDROOM_LIGHTS):
        assert _device_state(household_sim, light)["power"] == "off"
    thermostat = _device_state(household_sim, THERMOSTAT)
    assert thermostat["mode"] == "away" and thermostat["target_celsius"] == 16.0
    assert _device_state(household_sim, FRONT_DOOR)["locked"] is True
    assert _device_state(household_sim, BACK_DOOR)["locked"] is True
    assert _device_state(household_sim, SECURITY_SYSTEM) == {"armed": True, "mode": "away"}


def test_tonight_plan_is_read_only(plan_for, household_sim, media_service):
    before = household_sim.get_devices()

    result = _run(plan_for("What should we do tonight?"), household_sim, media_service)

    assert result.status is ExecutionStatus.COMPLETED
    assert household_sim.get_devices() == before
    assert media_service.get_session_state(TV) is None


def test_plan_without_steps_reports_no_action(household_sim, media_service):
    result = _run(clarification_plan(), household_sim, media_service)

    assert result.status is ExecutionStatus.NO_ACTION
    assert result.step_results == []
    assert result.completed_at is not None


def test_empty_plan_reports_no_action(household_sim, media_service):
    result = _run(make_plan([]), household_sim, media_service)

    assert result.status is ExecutionStatus.NO_ACTION


def test_steps_run_in_order_not_list_order(household_sim, media_service):
    plan = make_plan(
        [
            make_step("turn_off_lights", order=2, device_id=KITCHEN_LIGHTS),
            make_step("turn_off_lights", order=1, device_id=LIVING_ROOM_LIGHTS),
        ]
    )

    result = _run(plan, household_sim, media_service)

    assert [r.step_id for r in result.step_results] == ["step-1", "step-2"]


def test_a_failed_step_does_not_stop_the_rest(household_sim, media_service):
    plan = make_plan(
        [
            make_step("turn_off_lights", order=1, device_id="device-nope"),
            make_step("turn_off_lights", order=2, device_id=KITCHEN_LIGHTS),
        ]
    )

    result = _run(plan, household_sim, media_service)

    assert result.status is ExecutionStatus.PARTIAL
    assert [r.status for r in result.step_results] == [
        StepExecutionStatus.FAILED,
        StepExecutionStatus.SUCCEEDED,
    ]
    assert result.failed_step_ids == ["step-1"]
    assert result.succeeded_step_ids == ["step-2"]
    assert _device_state(household_sim, KITCHEN_LIGHTS)["power"] == "off"


def test_every_step_failing_is_a_failed_plan(household_sim, media_service):
    plan = make_plan([make_step("turn_off_lights", device_id="device-nope")])

    assert _run(plan, household_sim, media_service).status is ExecutionStatus.FAILED


def test_unconfirmed_high_impact_step_is_skipped_even_if_plan_looks_proposed(
    household_sim, media_service
):
    step = make_step("lock_door", device_id=FRONT_DOOR, risk=ActionRisk.HIGH_IMPACT_ACTION)
    plan = Plan(
        id="plan-sneaky",
        goal="Lock up",
        status=PlanStatus.PROPOSED,  # inconsistent on purpose: the executor must not trust it
        summary="",
        steps=[step],
        requires_confirmation=False,
    )

    result = _run(plan, household_sim, media_service)

    assert result.step_results[0].status is StepExecutionStatus.SKIPPED
    assert _device_state(household_sim, FRONT_DOOR)["locked"] is False


def test_max_attempts_must_be_positive(household_sim, media_service):
    with pytest.raises(ValueError, match="max_attempts"):
        _run(make_plan([]), household_sim, media_service, max_attempts=0)


# --------------------------------------------------------------------------- #
# Failure handling and retry
# --------------------------------------------------------------------------- #


def test_unsupported_action_fails_without_retry(household_sim, media_service):
    plan = make_plan([make_step("summon_dragon", device_id=TV)])

    result = _run(plan, household_sim, media_service)

    step = result.step_results[0]
    assert result.status is ExecutionStatus.FAILED
    assert step.error == actions.ERR_UNSUPPORTED_ACTION
    assert step.attempts == 1


def test_offline_device_is_retried_then_reported(household_sim, media_service):
    household_sim.set_device_state(FRONT_DOOR, online=False)
    plan = lock_front_door_plan()

    result = _run(plan, household_sim, media_service, confirmed=True)

    step = result.step_results[0]
    assert step.status is StepExecutionStatus.FAILED
    assert step.error == actions.ERR_DEVICE_UNAVAILABLE
    assert step.attempts == 2
    assert _device_state(household_sim, FRONT_DOOR)["locked"] is False


def test_unknown_device_is_not_retried(household_sim, media_service):
    plan = make_plan([make_step("turn_off_lights", device_id="device-nope")])

    step = _run(plan, household_sim, media_service).step_results[0]

    assert step.error == actions.ERR_DEVICE_NOT_FOUND
    assert step.attempts == 1


def test_missing_device_id_is_not_retried(household_sim, media_service):
    plan = make_plan([make_step("lock_door", risk=ActionRisk.HIGH_IMPACT_ACTION)])

    step = _run(plan, household_sim, media_service, confirmed=True).step_results[0]

    assert step.error == actions.ERR_MISSING_DEVICE_ID
    assert step.attempts == 1


def test_wrong_kind_of_device_is_not_retried(household_sim, media_service):
    plan = make_plan([make_step("lock_door", device_id=LIVING_ROOM_LIGHTS)])

    step = _run(plan, household_sim, media_service, confirmed=True).step_results[0]

    assert step.error == actions.ERR_UNSUPPORTED_DEVICE
    assert step.attempts == 1
    assert "light" in step.detail


def test_handler_exceptions_become_failed_steps(household_sim, media_service, monkeypatch):
    calls = []

    def exploding(step, context):
        calls.append(step.id)
        raise RuntimeError("kaboom")

    monkeypatch.setitem(actions._HANDLERS, "dim_lights", exploding)
    plan = make_plan([make_step("dim_lights", device_id=LIVING_ROOM_LIGHTS)])

    result = _run(plan, household_sim, media_service)

    step = result.step_results[0]
    assert step.status is StepExecutionStatus.FAILED
    assert step.error == "kaboom"
    assert step.attempts == 2
    assert len(calls) == 2


def test_a_flaky_step_succeeds_on_retry(household_sim, media_service, monkeypatch):
    outcomes = iter(["fail", "ok"])

    def flaky(step, context):
        if next(outcomes) == "fail":
            return StepExecutionResult(
                step_id=step.id,
                action=step.action,
                status=StepExecutionStatus.FAILED,
                detail="timed out",
                error="timeout",
            )
        return StepExecutionResult(
            step_id=step.id,
            action=step.action,
            status=StepExecutionStatus.SUCCEEDED,
            detail="worked",
        )

    monkeypatch.setitem(actions._HANDLERS, "dim_lights", flaky)
    plan = make_plan([make_step("dim_lights", device_id=LIVING_ROOM_LIGHTS)])

    result = _run(plan, household_sim, media_service)

    assert result.status is ExecutionStatus.COMPLETED
    assert result.step_results[0].attempts == 2


def test_max_attempts_of_one_disables_retry(household_sim, media_service):
    household_sim.set_device_state(FRONT_DOOR, online=False)

    result = _run(
        lock_front_door_plan(), household_sim, media_service, confirmed=True, max_attempts=1
    )

    assert result.step_results[0].attempts == 1


def test_run_step_never_raises_for_an_unconfirmed_step(household_sim, media_service):
    from haven.execution.actions import ActionContext

    step = make_step("lock_door", device_id=FRONT_DOOR, risk=ActionRisk.HIGH_IMPACT_ACTION)

    result = run_step(step, ActionContext(household_sim, media_service), confirmed=False)

    assert result.status is StepExecutionStatus.SKIPPED


# --------------------------------------------------------------------------- #
# Individual handlers
# --------------------------------------------------------------------------- #


def test_every_planner_action_has_a_handler():
    planner_actions = {
        "select_media",
        "prepare_media_session",
        "dim_lights",
        "turn_off_lights",
        "stop_media",
        "set_away_temperature",
        "lock_door",
        "arm_security_system",
        "review_schedule",
        "suggest_media",
        "suggest_wind_down",
    }

    assert planner_actions <= supported_actions()


def test_turning_off_an_already_off_light_is_a_no_op_success(household_sim, media_service):
    plan = make_plan([make_step("turn_off_lights", device_id=BEDROOM_LIGHTS)])

    step = _run(plan, household_sim, media_service).step_results[0]

    assert step.status is StepExecutionStatus.SUCCEEDED
    assert step.effect["changed"] is False
    assert "already off" in step.detail


def test_locking_twice_is_safe(household_sim, media_service):
    plan = lock_front_door_plan()

    first = _run(plan, household_sim, media_service, confirmed=True).step_results[0]
    second = _run(plan, household_sim, media_service, confirmed=True).step_results[0]

    assert first.effect["changed"] is True
    assert second.effect["changed"] is False
    assert second.status is StepExecutionStatus.SUCCEEDED


def test_arming_reports_the_mode(household_sim, media_service):
    plan = make_plan(
        [make_step("arm_security_system", device_id=SECURITY_SYSTEM, risk=ActionRisk.HIGH_IMPACT_ACTION)]
    )

    step = _run(plan, household_sim, media_service, confirmed=True).step_results[0]

    assert step.effect == {"device_id": SECURITY_SYSTEM, "armed": True, "mode": "away", "changed": True}


def test_away_temperature_remembers_the_home_temperature(household_sim, media_service):
    plan = make_plan([make_step("set_away_temperature", device_id=THERMOSTAT)])

    _run(plan, household_sim, media_service)
    _run(plan, household_sim, media_service)

    state = _device_state(household_sim, THERMOSTAT)
    assert state["target_celsius"] == 16.0
    assert state["home_target_celsius"] == 21.0


def test_stop_media_stops_an_active_session(household_sim, media_service):
    media_service.prepare_session(TV, "media-interstellar")
    plan = make_plan([make_step("stop_media", device_id=TV)])

    step = _run(plan, household_sim, media_service).step_results[0]

    assert step.effect["was_active"] is True
    assert media_service.get_session_state(TV) is None


def test_stop_media_with_nothing_playing_still_succeeds(household_sim, media_service):
    plan = make_plan([make_step("stop_media", device_id=TV)])

    step = _run(plan, household_sim, media_service).step_results[0]

    assert step.status is StepExecutionStatus.SUCCEEDED
    assert step.effect["was_active"] is False


def test_select_media_resolves_the_quoted_title(household_sim, media_service):
    step = make_step("select_media", description="Select 'Arrival' (116 min) from the catalog.")

    result = _run(make_plan([step]), household_sim, media_service).step_results[0]

    assert result.status is StepExecutionStatus.SUCCEEDED
    assert result.effect == {"media_id": "media-arrival", "title": "Arrival"}


def test_select_media_with_an_unknown_title_fails(household_sim, media_service):
    step = make_step("select_media", description="Select 'No Such Film' (90 min).")

    result = _run(make_plan([step]), household_sim, media_service).step_results[0]

    assert result.error == actions.ERR_UNKNOWN_MEDIA


def test_select_media_without_a_title_fails(household_sim, media_service):
    step = make_step("select_media", description="Select something good.")

    result = _run(make_plan([step]), household_sim, media_service).step_results[0]

    assert result.error == actions.ERR_NO_TITLE


def test_suggest_media_queues_nothing(household_sim, media_service):
    step = make_step("suggest_media", title="Suggest 'Arrival'", description="A calm sci-fi film.")

    result = _run(make_plan([step]), household_sim, media_service).step_results[0]

    assert result.status is StepExecutionStatus.SUCCEEDED
    assert media_service.get_session_state(TV) is None


def test_prepare_media_session_needs_a_device(household_sim, media_service):
    step = make_step("prepare_media_session", description="Queue 'Arrival' on the TV.")

    result = _run(make_plan([step]), household_sim, media_service).step_results[0]

    assert result.error == actions.ERR_MISSING_DEVICE_ID


def test_start_media_session_needs_a_prepared_session(household_sim, media_service):
    plan = make_plan([make_step("start_media_session", device_id=TV)])

    result = _run(plan, household_sim, media_service).step_results[0]

    assert result.status is StepExecutionStatus.FAILED
    assert "No prepared session" in result.detail


def test_start_media_session_plays_a_prepared_session(household_sim, media_service):
    media_service.prepare_session(TV, "media-arrival")
    plan = make_plan([make_step("start_media_session", device_id=TV)])

    result = _run(plan, household_sim, media_service).step_results[0]

    assert result.effect["status"] == "playing"


def test_extract_quoted_title():
    assert extract_quoted_title(None, "Queue 'Paddington 2' now") == "Paddington 2"
    assert extract_quoted_title("no quotes", "") is None
    assert extract_quoted_title("'First' and 'Second'") == "First"


# --------------------------------------------------------------------------- #
# Memory recording
# --------------------------------------------------------------------------- #


def test_completed_movie_night_is_recorded_with_its_title(plan_for, household_sim, media_service):
    memory = RecordingMemory()
    plan = plan_for("Get movie night ready")

    _run(plan, household_sim, media_service, household_id="household-001", memory=memory)

    assert len(memory.calls) == 1
    household_id, activity_type, _, details = memory.calls[0]
    assert household_id == "household-001"
    assert activity_type is ActivityType.MOVIE_NIGHT_PREPARED
    assert details["plan_id"] == plan.id
    assert isinstance(details["title"], str) and isinstance(details["media_id"], str)


def test_leaving_for_the_movies_is_not_a_movie_night(plan_for, household_sim, media_service):
    memory = RecordingMemory()
    plan = plan_for("We're leaving for the movie theater")

    _run(
        plan,
        household_sim,
        media_service,
        confirmed=True,
        household_id="household-001",
        memory=memory,
    )

    assert memory.calls == []


def test_an_incomplete_movie_night_is_not_recorded(plan_for, household_sim, media_service):
    memory = RecordingMemory()
    plan = plan_for("Get movie night ready")
    household_sim.set_device_state(LIVING_ROOM_LIGHTS, online=False)  # drops off after planning

    result = _run(plan, household_sim, media_service, household_id="household-001", memory=memory)

    assert result.status is ExecutionStatus.PARTIAL
    assert memory.calls == []


def test_a_memory_failure_never_fails_execution(plan_for, household_sim, media_service):
    result = _run(
        plan_for("Get movie night ready"),
        household_sim,
        media_service,
        household_id="household-001",
        memory=BrokenMemory(),
    )

    assert result.status is ExecutionStatus.COMPLETED


def test_memory_is_skipped_without_a_household_id(plan_for, household_sim, media_service):
    memory = RecordingMemory()

    _run(plan_for("Get movie night ready"), household_sim, media_service, memory=memory)

    assert memory.calls == []
