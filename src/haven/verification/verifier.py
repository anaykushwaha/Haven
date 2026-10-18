"""Haven verifier: `ExecutionResult` -> `VerificationResult`.

    verify_plan(plan, execution_result, household=..., media=...) -> VerificationResult

After a step is executed, Haven checks that the household actually
ended up in the state the step claimed, rather than trusting the
executor's own report. This is what lets Haven say something like "Fire
TV is ready, but the living room light didn't respond" instead of just
"done".

Verification is deterministic and read-only: it never re-executes an
action or mutates household/media state. A step that never ran (it
failed, or was skipped pending confirmation) is not re-checked here —
there is nothing to verify — and is reported as `SKIPPED`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from haven.execution.actions import READ_ONLY_ACTIONS, HouseholdBackend
from haven.execution.results import ExecutionResult, ExecutionStatus, StepExecutionStatus
from haven.integrations.media import MediaService, MediaSessionStatus
from haven.models.plan import Plan, PlanStep


class StepVerificationStatus(StrEnum):
    """Outcome of checking a single executed step's real-world effect."""

    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    UNVERIFIABLE = "UNVERIFIABLE"
    SKIPPED = "SKIPPED"


class StepVerification(BaseModel):
    """The result of checking one step's actual effect."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    action: str
    status: StepVerificationStatus
    detail: str


class VerificationStatus(StrEnum):
    """Overall outcome of verifying a plan's execution."""

    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class VerificationResult(BaseModel):
    """The result of verifying an executed `Plan`."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    status: VerificationStatus
    step_verifications: list[StepVerification] = Field(default_factory=list)
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def verify_plan(
    plan: Plan,
    execution_result: ExecutionResult,
    *,
    household: HouseholdBackend,
    media: MediaService,
) -> VerificationResult:
    """Check that each executed step's claimed effect actually took hold.

    Raises `ValueError` if `execution_result.plan_id` does not match
    `plan.id`.
    """

    if execution_result.plan_id != plan.id:
        raise ValueError(
            f"execution_result is for plan {execution_result.plan_id!r}, not {plan.id!r}"
        )

    if execution_result.status in (
        ExecutionStatus.AWAITING_CONFIRMATION,
        ExecutionStatus.NO_ACTION,
    ):
        return VerificationResult(
            plan_id=plan.id,
            status=VerificationStatus.SKIPPED,
            step_verifications=[],
        )

    steps_by_id: dict[str, PlanStep] = {step.id: step for step in plan.steps}
    verifications: list[StepVerification] = []

    for step_result in execution_result.step_results:
        step = steps_by_id.get(step_result.step_id)

        if step_result.status is not StepExecutionStatus.SUCCEEDED or step is None:
            verifications.append(
                StepVerification(
                    step_id=step_result.step_id,
                    action=step_result.action,
                    status=StepVerificationStatus.SKIPPED,
                    detail="Step did not succeed during execution; nothing to verify.",
                )
            )
            continue

        verifications.append(_verify_step(step, step_result.effect, household, media))

    return VerificationResult(
        plan_id=plan.id,
        status=_status_for(verifications),
        step_verifications=verifications,
    )


def _verify_step(
    step: PlanStep,
    effect: dict,
    household: HouseholdBackend,
    media: MediaService,
) -> StepVerification:
    action = step.action

    if action in ("select_media", "suggest_media"):
        return _verify_media_choice(step, effect)
    if action in READ_ONLY_ACTIONS:
        return _verified(step, "Read-only step; no household state was expected to change.")
    if action == "prepare_media_session":
        return _verify_media_session(step, effect, media, expected_status=MediaSessionStatus.READY)
    if action == "start_media_session":
        return _verify_media_session(
            step, effect, media, expected_status=MediaSessionStatus.PLAYING
        )
    if action == "stop_media":
        return _verify_media_stopped(step, media)
    if action == "dim_lights":
        return _verify_dim_lights(step, effect, household)
    if action == "turn_off_lights":
        return _verify_device(step, household, _check_light_off, "confirmed off.")
    if action == "set_away_temperature":
        return _verify_device(step, household, _check_thermostat_away(effect), "confirmed in away mode.")
    if action == "lock_door":
        return _verify_device(step, household, _check_locked, "confirmed locked.")
    if action == "arm_security_system":
        return _verify_device(step, household, _check_armed(effect), "confirmed armed.")

    return StepVerification(
        step_id=step.id,
        action=step.action,
        status=StepVerificationStatus.UNVERIFIABLE,
        detail=f"No verification check is defined for action '{step.action}'.",
    )


def _verified(step: PlanStep, detail: str) -> StepVerification:
    return StepVerification(
        step_id=step.id,
        action=step.action,
        status=StepVerificationStatus.VERIFIED,
        detail=detail,
    )


def _mismatch(step: PlanStep, detail: str) -> StepVerification:
    return StepVerification(
        step_id=step.id,
        action=step.action,
        status=StepVerificationStatus.MISMATCH,
        detail=detail,
    )


def _unverifiable(step: PlanStep, detail: str) -> StepVerification:
    return StepVerification(
        step_id=step.id,
        action=step.action,
        status=StepVerificationStatus.UNVERIFIABLE,
        detail=detail,
    )


StateCheck = Callable[[dict], str | None]
"""Inspects a device's state dict; returns a problem description, or `None` if fine."""


def _verify_device(
    step: PlanStep,
    household: HouseholdBackend,
    check: StateCheck,
    confirmed: str | Callable[[dict], str],
) -> StepVerification:
    """Re-read a device and confirm it ended up where the step claimed."""

    if step.device_id is None:
        return _unverifiable(step, "Step has no target device to verify.")

    record = household.get_device_state(step.device_id)
    if record is None:
        return _mismatch(step, f"Device {step.device_id} could not be found after execution.")

    if not record.get("online", True):
        return _mismatch(
            step, f"{step.device_id} is offline, so its state could not be confirmed."
        )

    state = record.get("state", {})
    problem = check(state)
    if problem is not None:
        return _mismatch(step, f"{step.device_id} {problem}")

    suffix = confirmed(state) if callable(confirmed) else confirmed
    return _verified(step, f"{step.device_id} {suffix}")


def _check_light_off(state: dict) -> str | None:
    if state.get("power") == "off":
        return None
    return f"is {state.get('power')!r}, expected 'off'."


def _check_locked(state: dict) -> str | None:
    if state.get("locked") is True:
        return None
    return "is not locked."


def _check_thermostat_away(effect: dict) -> StateCheck:
    def check(state: dict) -> str | None:
        if state.get("away") is not True or state.get("mode") != "away":
            return f"is in mode {state.get('mode')!r}, expected 'away'."
        expected = effect.get("target_celsius")
        if expected is not None and state.get("target_celsius") != expected:
            return f"target is {state.get('target_celsius')!r}, expected {expected!r}."
        return None

    return check


def _check_armed(effect: dict) -> StateCheck:
    def check(state: dict) -> str | None:
        if state.get("armed") is not True:
            return "is not armed."
        expected_mode = effect.get("mode")
        if expected_mode is not None and state.get("mode") != expected_mode:
            return f"is armed in mode {state.get('mode')!r}, expected {expected_mode!r}."
        return None

    return check


def _verify_media_stopped(step: PlanStep, media: MediaService) -> StepVerification:
    if step.device_id is None:
        return _unverifiable(step, "Step has no target device to verify.")

    session = media.get_session_state(step.device_id)
    if session is None:
        return _verified(step, f"{step.device_id} confirmed idle; nothing is playing.")
    return _mismatch(
        step,
        f"{step.device_id} still has '{session.title}' ({session.status.value}) after stopping.",
    )


def _verify_media_choice(step: PlanStep, effect: dict) -> StepVerification:
    # Read-only: nothing in the world to re-check beyond the recorded effect.
    if effect.get("media_id"):
        return StepVerification(
            step_id=step.id,
            action=step.action,
            status=StepVerificationStatus.VERIFIED,
            detail=f"Media selection confirmed: {effect.get('title', effect.get('media_id'))}.",
        )
    return StepVerification(
        step_id=step.id,
        action=step.action,
        status=StepVerificationStatus.UNVERIFIABLE,
        detail="Execution did not record a selected media_id.",
    )


def _verify_media_session(
    step: PlanStep,
    effect: dict,
    media: MediaService,
    *,
    expected_status: MediaSessionStatus,
) -> StepVerification:
    if step.device_id is None:
        return StepVerification(
            step_id=step.id,
            action=step.action,
            status=StepVerificationStatus.UNVERIFIABLE,
            detail="Step has no target device to verify.",
        )

    session = media.get_session_state(step.device_id)
    expected_media_id = effect.get("media_id")

    if session is None:
        return StepVerification(
            step_id=step.id,
            action=step.action,
            status=StepVerificationStatus.MISMATCH,
            detail=f"No session found on {step.device_id} after execution.",
        )

    if expected_media_id and session.media_id != expected_media_id:
        return StepVerification(
            step_id=step.id,
            action=step.action,
            status=StepVerificationStatus.MISMATCH,
            detail=(
                f"{step.device_id} is playing '{session.title}', "
                f"not the expected media_id {expected_media_id!r}."
            ),
        )

    if session.status is not expected_status:
        return StepVerification(
            step_id=step.id,
            action=step.action,
            status=StepVerificationStatus.MISMATCH,
            detail=(
                f"{step.device_id} session status is '{session.status.value}', "
                f"expected '{expected_status.value}'."
            ),
        )

    return StepVerification(
        step_id=step.id,
        action=step.action,
        status=StepVerificationStatus.VERIFIED,
        detail=f"{step.device_id} confirmed {expected_status.value} with '{session.title}'.",
    )


def _verify_dim_lights(
    step: PlanStep,
    effect: dict,
    household: HouseholdBackend,
) -> StepVerification:
    expected_brightness = effect.get("brightness")

    def check(state: dict) -> str | None:
        actual = state.get("brightness")
        if expected_brightness is not None and actual != expected_brightness:
            return f"brightness is {actual!r}, expected {expected_brightness!r}."
        return None

    return _verify_device(
        step,
        household,
        check,
        lambda state: f"confirmed dimmed to {state.get('brightness')}%.",
    )


def _status_for(verifications: list[StepVerification]) -> VerificationStatus:
    relevant = [v for v in verifications if v.status is not StepVerificationStatus.SKIPPED]

    if not relevant:
        return VerificationStatus.SKIPPED

    verified = [v for v in relevant if v.status is StepVerificationStatus.VERIFIED]

    if len(verified) == len(relevant):
        return VerificationStatus.VERIFIED
    if verified:
        return VerificationStatus.PARTIAL
    return VerificationStatus.FAILED


__all__ = [
    "StepVerificationStatus",
    "StepVerification",
    "VerificationStatus",
    "VerificationResult",
    "verify_plan",
]
