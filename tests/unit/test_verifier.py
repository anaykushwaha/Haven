"""Unit tests for the verifier and the recovery layer built on it."""

from __future__ import annotations

import pytest

from haven.execution.actions import ActionContext, get_handler
from haven.execution.executor import execute_plan
from haven.execution.results import (
    ExecutionResult,
    ExecutionStatus,
    StepExecutionResult,
    StepExecutionStatus,
)
from haven.models.plan import ActionRisk
from haven.verification.recovery import (
    RecoveryAction,
    recommend_recovery_for_execution,
    recommend_recovery_for_verification,
    recover_plan,
    retry_step,
)
from haven.verification.verifier import (
    StepVerification,
    StepVerificationStatus,
    VerificationStatus,
    verify_plan,
)
from tests.fixtures.plans import (
    FRONT_DOOR,
    KITCHEN_LIGHTS,
    LIVING_ROOM_LIGHTS,
    SECURITY_SYSTEM,
    THERMOSTAT,
    TV,
    FlakyHouseholdBackend,
    lock_front_door_plan,
    make_plan,
    make_step,
)


def _execute_and_verify(plan, household_sim, media_service, **kwargs):
    execution = execute_plan(plan, household=household_sim, media=media_service, **kwargs)
    verification = verify_plan(plan, execution, household=household_sim, media=media_service)
    return execution, verification


def _by_action(verification, action):
    return next(v for v in verification.step_verifications if v.action == action)


# --------------------------------------------------------------------------- #
# verify_plan: the happy paths
# --------------------------------------------------------------------------- #


def test_movie_night_is_verified(plan_for, household_sim, media_service):
    _, verification = _execute_and_verify(
        plan_for("Get movie night ready"), household_sim, media_service
    )

    assert verification.status is VerificationStatus.VERIFIED
    assert {v.action for v in verification.step_verifications} == {
        "select_media",
        "prepare_media_session",
        "dim_lights",
    }
    assert all(v.status is StepVerificationStatus.VERIFIED for v in verification.step_verifications)


def test_leaving_home_is_verified(plan_for, household_sim, media_service):
    _, verification = _execute_and_verify(
        plan_for("We're leaving the house"), household_sim, media_service, confirmed=True
    )

    assert verification.status is VerificationStatus.VERIFIED
    assert len(verification.step_verifications) == 8


def test_tonight_plan_is_verified_as_read_only(plan_for, household_sim, media_service):
    _, verification = _execute_and_verify(
        plan_for("What should we do tonight?"), household_sim, media_service
    )

    assert verification.status is VerificationStatus.VERIFIED
    assert "Read-only" in _by_action(verification, "review_schedule").detail


def test_verifier_does_not_change_anything(plan_for, household_sim, media_service):
    plan = plan_for("We're leaving the house")
    execution = execute_plan(plan, household=household_sim, media=media_service, confirmed=True)
    before = household_sim.get_devices()

    verify_plan(plan, execution, household=household_sim, media=media_service)

    assert household_sim.get_devices() == before


@pytest.mark.parametrize("status", [ExecutionStatus.AWAITING_CONFIRMATION, ExecutionStatus.NO_ACTION])
def test_nothing_ran_means_nothing_to_verify(household_sim, media_service, status):
    plan = make_plan([make_step("turn_off_lights", device_id=KITCHEN_LIGHTS)])
    execution = ExecutionResult(plan_id=plan.id, goal=plan.goal, status=status)

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert verification.status is VerificationStatus.SKIPPED
    assert verification.step_verifications == []


def test_result_for_another_plan_is_rejected(household_sim, media_service):
    plan = make_plan([make_step("turn_off_lights", device_id=KITCHEN_LIGHTS)])
    other = make_plan([], plan_id="plan-other")
    execution = execute_plan(other, household=household_sim, media=media_service)

    with pytest.raises(ValueError, match="plan"):
        verify_plan(plan, execution, household=household_sim, media=media_service)


def test_steps_that_did_not_succeed_are_skipped(household_sim, media_service):
    plan = make_plan(
        [
            make_step("turn_off_lights", order=1, device_id="device-nope"),
            make_step("turn_off_lights", order=2, device_id=KITCHEN_LIGHTS),
        ]
    )

    execution, verification = _execute_and_verify(plan, household_sim, media_service)

    assert execution.status is ExecutionStatus.PARTIAL
    assert verification.step_verifications[0].status is StepVerificationStatus.SKIPPED
    assert verification.step_verifications[1].status is StepVerificationStatus.VERIFIED
    assert verification.status is VerificationStatus.VERIFIED


def test_only_skipped_steps_means_a_skipped_verification(household_sim, media_service):
    plan = make_plan([make_step("turn_off_lights", device_id="device-nope")])

    _, verification = _execute_and_verify(plan, household_sim, media_service)

    assert verification.status is VerificationStatus.SKIPPED


# --------------------------------------------------------------------------- #
# verify_plan: catching what the executor claimed but the home does not show
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("goal", "action", "sabotage"),
    [
        (
            "We're leaving the house",
            "turn_off_lights",
            lambda sim, media: sim.set_device_state(LIVING_ROOM_LIGHTS, state={"power": "on"}),
        ),
        (
            "We're leaving the house",
            "set_away_temperature",
            lambda sim, media: sim.set_device_state(
                THERMOSTAT, state={"mode": "heat", "away": False}
            ),
        ),
        (
            "We're leaving the house",
            "lock_door",
            lambda sim, media: sim.set_device_state(FRONT_DOOR, state={"locked": False}),
        ),
        (
            "We're leaving the house",
            "arm_security_system",
            lambda sim, media: sim.set_device_state(SECURITY_SYSTEM, state={"armed": False}),
        ),
        (
            "Get movie night ready",
            "dim_lights",
            lambda sim, media: sim.set_device_state(LIVING_ROOM_LIGHTS, state={"brightness": 100}),
        ),
        (
            "Get movie night ready",
            "prepare_media_session",
            lambda sim, media: media.stop_session(TV),
        ),
    ],
)
def test_a_reverted_device_is_caught(
    plan_for, household_sim, media_service, goal, action, sabotage
):
    plan = plan_for(goal)
    execution = execute_plan(plan, household=household_sim, media=media_service, confirmed=True)
    sabotage(household_sim, media_service)

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert _by_action(verification, action).status is StepVerificationStatus.MISMATCH
    assert verification.status is VerificationStatus.PARTIAL


def test_a_still_playing_tv_is_caught(household_sim, media_service):
    plan = make_plan([make_step("stop_media", device_id=TV)])
    execution = execute_plan(plan, household=household_sim, media=media_service)
    media_service.prepare_session(TV, "media-arrival")

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert verification.step_verifications[0].status is StepVerificationStatus.MISMATCH
    assert "Arrival" in verification.step_verifications[0].detail


def test_the_wrong_title_on_the_tv_is_caught(plan_for, household_sim, media_service):
    plan = plan_for("Get movie night ready")
    execution = execute_plan(plan, household=household_sim, media=media_service)
    media_service.prepare_session(TV, "media-interstellar")

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    mismatch = _by_action(verification, "prepare_media_session")
    assert mismatch.status is StepVerificationStatus.MISMATCH
    assert "Interstellar" in mismatch.detail


def test_a_device_that_went_offline_cannot_be_confirmed(plan_for, household_sim, media_service):
    plan = plan_for("We're leaving the house")
    execution = execute_plan(plan, household=household_sim, media=media_service, confirmed=True)
    household_sim.set_device_state(FRONT_DOOR, online=False)

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    locks = [v for v in verification.step_verifications if v.action == "lock_door"]
    assert sorted(v.status.value for v in locks) == ["MISMATCH", "VERIFIED"]
    assert any("offline" in v.detail for v in locks)


def test_everything_reverting_is_a_failed_verification(household_sim, media_service):
    plan = lock_front_door_plan()
    execution = execute_plan(plan, household=household_sim, media=media_service, confirmed=True)
    household_sim.set_device_state(FRONT_DOOR, state={"locked": False})

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert verification.status is VerificationStatus.FAILED


def _succeeded(step):
    return StepExecutionResult(
        step_id=step.id, action=step.action, status=StepExecutionStatus.SUCCEEDED, detail="ok"
    )


def test_an_action_without_a_check_is_unverifiable(household_sim, media_service):
    step = make_step("summon_dragon", device_id=TV)
    plan = make_plan([step])
    execution = ExecutionResult(
        plan_id=plan.id,
        goal=plan.goal,
        status=ExecutionStatus.COMPLETED,
        step_results=[_succeeded(step)],
    )

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert verification.step_verifications[0].status is StepVerificationStatus.UNVERIFIABLE


@pytest.mark.parametrize("action", ["lock_door", "turn_off_lights", "stop_media", "dim_lights"])
def test_a_device_step_without_a_device_is_unverifiable(household_sim, media_service, action):
    step = make_step(action)
    plan = make_plan([step])
    execution = ExecutionResult(
        plan_id=plan.id,
        goal=plan.goal,
        status=ExecutionStatus.COMPLETED,
        step_results=[_succeeded(step)],
    )

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert verification.step_verifications[0].status is StepVerificationStatus.UNVERIFIABLE


def test_a_media_choice_without_a_recorded_media_id_is_unverifiable(household_sim, media_service):
    step = make_step("select_media")
    plan = make_plan([step])
    execution = ExecutionResult(
        plan_id=plan.id,
        goal=plan.goal,
        status=ExecutionStatus.COMPLETED,
        step_results=[_succeeded(step)],
    )

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert verification.step_verifications[0].status is StepVerificationStatus.UNVERIFIABLE


def test_a_device_that_vanished_is_a_mismatch(household_sim, media_service):
    step = make_step("lock_door", device_id="device-nope", risk=ActionRisk.HIGH_IMPACT_ACTION)
    plan = make_plan([step])
    execution = ExecutionResult(
        plan_id=plan.id,
        goal=plan.goal,
        status=ExecutionStatus.COMPLETED,
        step_results=[_succeeded(step)],
    )

    verification = verify_plan(plan, execution, household=household_sim, media=media_service)

    assert verification.step_verifications[0].status is StepVerificationStatus.MISMATCH


# --------------------------------------------------------------------------- #
# Recovery recommendations
# --------------------------------------------------------------------------- #


def _exec_result(status, error=None):
    return StepExecutionResult(
        step_id="s", action="a", status=status, detail="d", error=error
    )


def _verification(status):
    return StepVerification(step_id="s", action="a", status=status, detail="d")


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (StepExecutionStatus.SUCCEEDED, None, RecoveryAction.CONTINUE),
        (StepExecutionStatus.SKIPPED, None, RecoveryAction.ASK_USER),
        (StepExecutionStatus.FAILED, "device unavailable", RecoveryAction.RETRY),
        (StepExecutionStatus.FAILED, "something odd", RecoveryAction.RETRY),
        (StepExecutionStatus.FAILED, "device not found", RecoveryAction.ABORT),
        (StepExecutionStatus.FAILED, "unsupported action", RecoveryAction.ABORT),
        (StepExecutionStatus.FAILED, "unsupported device type", RecoveryAction.ABORT),
        (StepExecutionStatus.FAILED, "missing device_id", RecoveryAction.ABORT),
        (StepExecutionStatus.FAILED, "unknown media title", RecoveryAction.ABORT),
    ],
)
def test_recovery_for_execution(status, error, expected):
    assert recommend_recovery_for_execution(_exec_result(status, error)) is expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (StepVerificationStatus.VERIFIED, RecoveryAction.CONTINUE),
        (StepVerificationStatus.SKIPPED, RecoveryAction.ASK_USER),
        (StepVerificationStatus.UNVERIFIABLE, RecoveryAction.SKIP),
        (StepVerificationStatus.MISMATCH, RecoveryAction.RETRY),
    ],
)
def test_recovery_for_verification(status, expected):
    assert recommend_recovery_for_verification(_verification(status)) is expected


def test_retry_step_stops_at_the_first_success(household_sim, media_service):
    step = make_step("turn_off_lights", device_id=KITCHEN_LIGHTS)
    calls = []

    def handler(s, c):
        calls.append(1)
        return _succeeded(s)

    result = retry_step(step, None, handler, max_attempts=3)

    assert result.status is StepExecutionStatus.SUCCEEDED
    assert len(calls) == 1


def test_retry_step_returns_the_last_failure(household_sim, media_service):
    step = make_step("lock_door", device_id=FRONT_DOOR)
    context = ActionContext(household_sim, media_service)
    household_sim.set_device_state(FRONT_DOOR, online=False)

    result = retry_step(step, context, get_handler("lock_door"), max_attempts=3)

    assert result.status is StepExecutionStatus.FAILED


def test_retry_step_needs_at_least_one_attempt():
    with pytest.raises(ValueError, match="max_attempts"):
        retry_step(make_step("lock_door"), None, lambda s, c: None, max_attempts=0)


# --------------------------------------------------------------------------- #
# recover_plan: one bounded pass over what verification found wrong
# --------------------------------------------------------------------------- #


def test_recovery_leaves_a_clean_run_untouched(plan_for, household_sim, media_service):
    plan = plan_for("Get movie night ready")
    execution, verification = _execute_and_verify(plan, household_sim, media_service)

    new_execution, new_verification = recover_plan(
        plan, execution, verification, household=household_sim, media=media_service
    )

    assert new_execution is execution
    assert new_verification is verification


def test_recovery_corrects_a_device_that_ignored_the_command(household_sim, media_service):
    flaky = FlakyHouseholdBackend(household_sim)
    flaky.drop_writes(FRONT_DOOR, 1)
    plan = lock_front_door_plan()
    execution = execute_plan(plan, household=flaky, media=media_service, confirmed=True)
    verification = verify_plan(plan, execution, household=flaky, media=media_service)
    assert verification.status is VerificationStatus.FAILED  # the lie is caught

    new_execution, new_verification = recover_plan(
        plan, execution, verification, household=flaky, media=media_service
    )

    assert new_verification.status is VerificationStatus.VERIFIED
    assert new_execution.status is ExecutionStatus.COMPLETED
    assert new_execution.step_results[0].attempts == 2
    assert household_sim.get_device_state(FRONT_DOOR)["state"]["locked"] is True


def test_recovery_is_a_single_pass(household_sim, media_service):
    flaky = FlakyHouseholdBackend(household_sim)
    flaky.drop_writes(FRONT_DOOR, 5)
    plan = lock_front_door_plan()
    execution = execute_plan(plan, household=flaky, media=media_service, confirmed=True)
    verification = verify_plan(plan, execution, household=flaky, media=media_service)

    _, new_verification = recover_plan(
        plan, execution, verification, household=flaky, media=media_service
    )

    assert new_verification.status is VerificationStatus.FAILED
    assert flaky.write_attempts[FRONT_DOOR] == 2  # original + exactly one recovery write


def test_recovery_only_touches_mismatched_steps(plan_for, household_sim, media_service):
    flaky = FlakyHouseholdBackend(household_sim)
    flaky.drop_writes(KITCHEN_LIGHTS, 1)
    plan = plan_for("We're leaving the house")
    execution = execute_plan(plan, household=flaky, media=media_service, confirmed=True)
    verification = verify_plan(plan, execution, household=flaky, media=media_service)
    assert verification.status is VerificationStatus.PARTIAL

    new_execution, new_verification = recover_plan(
        plan, execution, verification, household=flaky, media=media_service
    )

    assert new_verification.status is VerificationStatus.VERIFIED
    retried = [r.step_id for r in new_execution.step_results if r.attempts > 1]
    assert len(retried) == 1
    assert flaky.write_attempts[FRONT_DOOR] == 1  # untouched steps are not re-run


def test_recovery_that_hits_an_offline_device_reports_the_failure(household_sim, media_service):
    flaky = FlakyHouseholdBackend(household_sim)
    flaky.drop_writes(FRONT_DOOR, 1)
    plan = lock_front_door_plan()
    execution = execute_plan(plan, household=flaky, media=media_service, confirmed=True)
    verification = verify_plan(plan, execution, household=flaky, media=media_service)
    household_sim.set_device_state(FRONT_DOOR, online=False)

    new_execution, new_verification = recover_plan(
        plan, execution, verification, household=flaky, media=media_service
    )

    assert new_execution.status is ExecutionStatus.FAILED
    assert new_execution.step_results[0].error == "device unavailable"
    assert new_verification.step_verifications[0].status is StepVerificationStatus.SKIPPED


def test_recovery_rejects_results_for_another_plan(household_sim, media_service):
    plan = lock_front_door_plan()
    execution, verification = _execute_and_verify(
        plan, household_sim, media_service, confirmed=True
    )
    other = make_plan([], plan_id="plan-other")

    with pytest.raises(ValueError, match="plan"):
        recover_plan(other, execution, verification, household=household_sim, media=media_service)
