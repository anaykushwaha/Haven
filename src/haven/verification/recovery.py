"""Recovery strategy for a step that failed execution or verification.

Haven's Layer 4 (verification) is only useful if something sensible
happens after a mismatch is found. This module does two things:

- *recommend* what should happen next for a single step
  (`recommend_recovery_for_execution` / `recommend_recovery_for_verification`),
  without acting on it; and
- run one bounded *recovery pass* over a whole plan (`recover_plan`):
  steps verification found did not take hold are run again once and then
  re-verified, so a device that silently reverted is corrected, or the
  mismatch is reported honestly if it is not.

Recovery never goes beyond that single pass: Haven would rather report
"the front door did not lock" than keep hammering a device.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from haven.execution.actions import NON_RETRYABLE_ERRORS, ActionContext, HouseholdBackend
from haven.execution.executor import run_step
from haven.execution.results import (
    ExecutionResult,
    StepExecutionResult,
    StepExecutionStatus,
    status_for_results,
)
from haven.integrations.home import HomeService
from haven.integrations.media import MediaService
from haven.integrations.security import SecurityService
from haven.models.plan import Plan
from haven.verification.verifier import (
    StepVerification,
    StepVerificationStatus,
    VerificationResult,
    verify_plan,
)


class RecoveryAction(StrEnum):
    """What Haven should consider doing about one step's outcome."""

    CONTINUE = "CONTINUE"
    """The step succeeded and was verified; proceed to the next step."""

    RETRY = "RETRY"
    """The failure looks transient; trying the same step again may help."""

    SKIP = "SKIP"
    """Leave this step's effect as-is and continue with the rest of the plan."""

    ASK_USER = "ASK_USER"
    """Haven needs a decision it should not make on its own."""

    ABORT = "ABORT"
    """Retrying will not help; stop and surface the problem."""



def recommend_recovery_for_execution(step_result: StepExecutionResult) -> RecoveryAction:
    """Recommend what to do about a single executed step's outcome."""

    if step_result.status is StepExecutionStatus.SUCCEEDED:
        return RecoveryAction.CONTINUE

    if step_result.status is StepExecutionStatus.SKIPPED:
        # Skipped almost always means "awaiting confirmation" today.
        return RecoveryAction.ASK_USER

    # FAILED
    if step_result.error in NON_RETRYABLE_ERRORS:
        return RecoveryAction.ABORT

    return RecoveryAction.RETRY


def recommend_recovery_for_verification(verification: StepVerification) -> RecoveryAction:
    """Recommend what to do about a single step's verification outcome."""

    if verification.status is StepVerificationStatus.VERIFIED:
        return RecoveryAction.CONTINUE

    if verification.status is StepVerificationStatus.SKIPPED:
        return RecoveryAction.ASK_USER

    if verification.status is StepVerificationStatus.UNVERIFIABLE:
        # Haven can't confirm the effect, but it also has no evidence
        # anything is wrong. Move on rather than blocking on a check
        # that was never going to succeed.
        return RecoveryAction.SKIP

    # MISMATCH: the device didn't end up where the plan expected. A
    # single retry is worth attempting (the device may have been
    # momentarily unavailable); a caller that already retried should
    # treat a second mismatch as ABORT rather than calling this again.
    return RecoveryAction.RETRY


def retry_step(
    step: Any,
    context: Any,
    handler: Any,
    *,
    max_attempts: int = 2,
) -> StepExecutionResult:
    """Re-run `handler` for `step` up to `max_attempts` times.

    Stops as soon as a `SUCCEEDED` result is produced. Returns the final
    attempt's result (success or otherwise) when every attempt fails.
    Exceptions from `handler` are not caught here — callers that need
    the executor's crash-safety should call through
    `haven.execution.executor.execute_plan` instead of this helper
    directly for anything beyond a single manual retry.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    result: StepExecutionResult | None = None

    for _attempt in range(max_attempts):
        result = handler(step, context)
        if result.status is StepExecutionStatus.SUCCEEDED:
            return result

    assert result is not None  # loop runs at least once
    return result


def recover_plan(
    plan: Plan,
    execution: ExecutionResult,
    verification: VerificationResult,
    *,
    household: HouseholdBackend,
    media: MediaService,
    home: HomeService | None = None,
    security: SecurityService | None = None,
    max_attempts: int = 2,
) -> tuple[ExecutionResult, VerificationResult]:
    """Re-run steps verification found unfulfilled, then re-verify them.

    Only steps whose verification is `MISMATCH` are run again; everything
    else is left exactly as it was. Because a mismatch can only exist for a
    step that already ran, the original execution was necessarily
    confirmed, so re-running a high-impact step here does not need a fresh
    confirmation.

    Returns the updated ``(execution, verification)`` pair. When nothing
    mismatched, the inputs are returned unchanged.

    Raises `ValueError` if either result belongs to a different plan.
    """

    if execution.plan_id != plan.id or verification.plan_id != plan.id:
        raise ValueError("execution and verification must belong to the given plan")

    mismatched = [
        v.step_id
        for v in verification.step_verifications
        if v.status is StepVerificationStatus.MISMATCH
    ]
    if not mismatched:
        return execution, verification

    steps = {step.id: step for step in plan.steps}
    context = ActionContext(household=household, media=media, home=home, security=security)

    rerun: dict[str, StepExecutionResult] = {}
    for step_id in mismatched:
        step = steps.get(step_id)
        previous = next((r for r in execution.step_results if r.step_id == step_id), None)
        if step is None or previous is None:
            continue
        result = run_step(step, context, confirmed=True, max_attempts=max_attempts)
        rerun[step_id] = result.model_copy(update={"attempts": previous.attempts + result.attempts})

    step_results = [rerun.get(r.step_id, r) for r in execution.step_results]
    recovered_execution = execution.model_copy(
        update={
            "step_results": step_results,
            "status": status_for_results(step_results),
            "completed_at": datetime.now(UTC),
        }
    )
    recovered_verification = verify_plan(
        plan, recovered_execution, household=household, media=media
    )
    return recovered_execution, recovered_verification


__all__ = [
    "RecoveryAction",
    "recommend_recovery_for_execution",
    "recommend_recovery_for_verification",
    "recover_plan",
    "retry_step",
]
