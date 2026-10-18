"""Leaving Home workflow, end to end: context -> plan -> execute -> verify.

Person A's `test_movie_night.py` / `test_tonight_planning.py` cover planning;
this file covers what planning only proposes — actually securing the house
through the simulators, with the confirmation gate, failure handling and
verification a real departure needs.
"""

from __future__ import annotations

import pytest

from haven.execution.executor import execute_plan
from haven.execution.results import ExecutionStatus, StepExecutionStatus
from haven.models.plan import ActionRisk, PlanStatus
from haven.verification.recovery import recover_plan
from haven.verification.verifier import StepVerificationStatus, VerificationStatus, verify_plan
from tests.fixtures.plans import (
    BACK_DOOR,
    BEDROOM_LIGHTS,
    FRONT_DOOR,
    KITCHEN_LIGHTS,
    LIVING_ROOM_LIGHTS,
    SECURITY_SYSTEM,
    THERMOSTAT,
    TV,
    FlakyHouseholdBackend,
)

GOAL = "We're leaving the house"


def _state(household_sim, device_id):
    return household_sim.get_device_state(device_id)["state"]


def _run(plan, household_sim, media_service, **kwargs):
    execution = execute_plan(plan, household=household_sim, media=media_service, **kwargs)
    verification = verify_plan(plan, execution, household=household_sim, media=media_service)
    return execution, verification


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


def test_the_plan_secures_every_part_of_the_house(plan_for):
    plan = plan_for(GOAL)

    assert plan.status is PlanStatus.AWAITING_CONFIRMATION
    assert plan.requires_confirmation is True
    actions = [(s.action, s.device_id) for s in plan.steps]
    assert actions == [
        ("turn_off_lights", LIVING_ROOM_LIGHTS),
        ("turn_off_lights", KITCHEN_LIGHTS),
        ("turn_off_lights", BEDROOM_LIGHTS),
        ("stop_media", TV),
        ("set_away_temperature", THERMOSTAT),
        ("lock_door", FRONT_DOOR),
        ("lock_door", BACK_DOOR),
        ("arm_security_system", SECURITY_SYSTEM),
    ]


def test_only_locking_and_arming_need_confirmation(plan_for):
    plan = plan_for(GOAL)

    needs_confirmation = {s.action for s in plan.steps if s.requires_confirmation}
    assert needs_confirmation == {"lock_door", "arm_security_system"}
    assert all(
        s.risk is ActionRisk.HIGH_IMPACT_ACTION
        for s in plan.steps
        if s.action in needs_confirmation
    )
    assert all(
        s.risk is ActionRisk.REVERSIBLE_ACTION
        for s in plan.steps
        if s.action not in needs_confirmation
    )


@pytest.mark.parametrize(
    "goal",
    ["We're heading out", "I'm leaving", "Lock up the house", "We're leaving for the movie theater"],
)
def test_departure_phrasing_all_means_leaving_home(plan_for, goal):
    plan = plan_for(goal)

    assert "lock_door" in {s.action for s in plan.steps}


# --------------------------------------------------------------------------- #
# Confirmation
# --------------------------------------------------------------------------- #


def test_nothing_changes_until_the_user_confirms(plan_for, household_sim, media_service):
    plan = plan_for(GOAL)
    before = household_sim.get_devices()

    execution, verification = _run(plan, household_sim, media_service)

    assert execution.status is ExecutionStatus.AWAITING_CONFIRMATION
    assert verification.status is VerificationStatus.SKIPPED
    assert household_sim.get_devices() == before


def test_confirming_secures_the_house(plan_for, household_sim, media_service):
    plan = plan_for(GOAL)

    execution, verification = _run(plan, household_sim, media_service, confirmed=True)

    assert execution.status is ExecutionStatus.COMPLETED
    assert verification.status is VerificationStatus.VERIFIED
    for light in (LIVING_ROOM_LIGHTS, KITCHEN_LIGHTS, BEDROOM_LIGHTS):
        assert _state(household_sim, light)["power"] == "off"
    assert _state(household_sim, THERMOSTAT)["mode"] == "away"
    assert _state(household_sim, FRONT_DOOR)["locked"] is True
    assert _state(household_sim, BACK_DOOR)["locked"] is True
    assert _state(household_sim, SECURITY_SYSTEM)["armed"] is True


def test_the_tv_is_stopped_when_leaving(plan_for, household_sim, media_service):
    media_service.prepare_session(TV, "media-arrival")
    media_service.start_session(TV)
    plan = plan_for(GOAL)

    _, verification = _run(plan, household_sim, media_service, confirmed=True)

    assert media_service.get_session_state(TV) is None
    assert verification.status is VerificationStatus.VERIFIED


def test_leaving_home_can_be_repeated_safely(plan_for, household_sim, media_service):
    first = _run(plan_for(GOAL), household_sim, media_service, confirmed=True)
    second_execution, second_verification = _run(
        plan_for(GOAL), household_sim, media_service, confirmed=True
    )

    assert first[0].status is ExecutionStatus.COMPLETED
    assert second_execution.status is ExecutionStatus.COMPLETED
    assert second_verification.status is VerificationStatus.VERIFIED
    assert _state(household_sim, THERMOSTAT)["home_target_celsius"] == 21.0


# --------------------------------------------------------------------------- #
# Context-sensitivity
# --------------------------------------------------------------------------- #


def test_the_plan_warns_when_people_are_still_home(plan_for):
    plan = plan_for(GOAL)

    arm_step = next(s for s in plan.steps if s.action == "arm_security_system")
    assert "home" in arm_step.description.lower()


def test_an_offline_device_is_left_out_of_the_plan(plan_for, household_sim, media_service):
    household_sim.set_device_state(BACK_DOOR, online=False)

    plan = plan_for(GOAL)

    assert BACK_DOOR not in {s.device_id for s in plan.steps}
    execution, verification = _run(plan, household_sim, media_service, confirmed=True)
    assert execution.status is ExecutionStatus.COMPLETED
    assert verification.status is VerificationStatus.VERIFIED
    assert _state(household_sim, BACK_DOOR)["locked"] is False


def test_a_house_with_no_security_devices_still_gets_tidied(plan_for, household_sim, media_service):
    for device in household_sim.get_devices():
        if device["device_type"] in ("lock", "security_system"):
            household_sim.set_device_state(device["device_id"], online=False)

    plan = plan_for(GOAL)
    execution, _ = _run(plan, household_sim, media_service)

    assert plan.status is PlanStatus.PROPOSED  # nothing high-impact left to confirm
    assert execution.status is ExecutionStatus.COMPLETED
    assert _state(household_sim, LIVING_ROOM_LIGHTS)["power"] == "off"


# --------------------------------------------------------------------------- #
# When something goes wrong
# --------------------------------------------------------------------------- #


def test_a_door_that_drops_offline_is_reported_not_hidden(plan_for, household_sim, media_service):
    plan = plan_for(GOAL)
    household_sim.set_device_state(FRONT_DOOR, online=False)  # after planning

    execution, verification = _run(plan, household_sim, media_service, confirmed=True)

    failed = [r for r in execution.step_results if r.status is StepExecutionStatus.FAILED]
    assert execution.status is ExecutionStatus.PARTIAL
    assert [r.step_id for r in failed] == [
        next(s.id for s in plan.steps if s.device_id == FRONT_DOOR)
    ]
    assert failed[0].error == "device unavailable"
    assert failed[0].attempts == 2
    assert _state(household_sim, BACK_DOOR)["locked"] is True  # the rest still ran
    assert _state(household_sim, SECURITY_SYSTEM)["armed"] is True
    skipped = [
        v
        for v in verification.step_verifications
        if v.status is StepVerificationStatus.SKIPPED
    ]
    assert len(skipped) == 1


def test_a_lock_that_silently_ignores_the_command_is_caught_and_corrected(
    plan_for, household_sim, media_service
):
    flaky = FlakyHouseholdBackend(household_sim)
    flaky.drop_writes(FRONT_DOOR, 1)
    plan = plan_for(GOAL)
    execution = execute_plan(plan, household=flaky, media=media_service, confirmed=True)
    verification = verify_plan(plan, execution, household=flaky, media=media_service)

    assert execution.status is ExecutionStatus.COMPLETED  # the lock claimed success
    assert verification.status is VerificationStatus.PARTIAL  # ...but the door was still open
    assert _state(household_sim, FRONT_DOOR)["locked"] is False

    _, recovered = recover_plan(
        plan, execution, verification, household=flaky, media=media_service
    )

    assert recovered.status is VerificationStatus.VERIFIED
    assert _state(household_sim, FRONT_DOOR)["locked"] is True


def test_a_door_that_will_not_lock_is_reported_honestly(plan_for, household_sim, media_service):
    flaky = FlakyHouseholdBackend(household_sim)
    flaky.drop_writes(FRONT_DOOR, 10)
    plan = plan_for(GOAL)
    execution = execute_plan(plan, household=flaky, media=media_service, confirmed=True)
    verification = verify_plan(plan, execution, household=flaky, media=media_service)

    _, recovered = recover_plan(
        plan, execution, verification, household=flaky, media=media_service
    )

    mismatches = [
        v for v in recovered.step_verifications if v.status is StepVerificationStatus.MISMATCH
    ]
    assert len(mismatches) == 1
    assert FRONT_DOOR in mismatches[0].detail
    assert recovered.status is VerificationStatus.PARTIAL
