"""Haven executor: structured `Plan` -> `ExecutionResult`.

    execute_plan(plan, household=..., media=...) -> ExecutionResult

Execution is deterministic. No LLM is involved: each step is dispatched
to a plain Python handler (`haven.execution.actions`) that either
succeeds, fails, or is skipped, and every outcome is recorded — a
partially failed plan is a normal, expected result, not an exception.

The executor does not resolve context, plan, or verify device effects.
It also refuses to run a plan that still requires confirmation unless
the caller explicitly says the user has confirmed it; Haven never
silently executes a high-impact action (locking a door, arming the
security system).

A step that fails for a reason that might be temporary — most commonly a
device that did not respond — is retried once before being reported as
failed. A failure that retrying cannot fix (an unknown device, an
unsupported action) is reported immediately.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol

from haven.execution.actions import (
    ERR_UNSUPPORTED_ACTION,
    NON_RETRYABLE_ERRORS,
    ActionContext,
    HouseholdBackend,
    get_handler,
)
from haven.execution.results import (
    ExecutionResult,
    ExecutionStatus,
    StepExecutionResult,
    StepExecutionStatus,
    status_for_results,
)
from haven.integrations.home import HomeService
from haven.integrations.media import MediaService
from haven.integrations.security import SecurityService
from haven.memory.activity import ActivityType
from haven.models.plan import Plan, PlanStatus, PlanStep

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 2


class ActivityRecorder(Protocol):
    """The subset of `MemoryService` the executor optionally reports to."""

    def record_activity(
        self,
        household_id: str,
        activity_type: ActivityType,
        summary: str,
        *,
        details: dict | None = None,
    ) -> object: ...


def execute_plan(
    plan: Plan,
    *,
    household: HouseholdBackend,
    media: MediaService,
    household_id: str | None = None,
    confirmed: bool = False,
    memory: ActivityRecorder | None = None,
    home: HomeService | None = None,
    security: SecurityService | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ExecutionResult:
    """Execute every step of `plan` in order and report what happened.

    ``confirmed`` must be ``True`` for a plan whose status is
    ``AWAITING_CONFIRMATION`` (i.e. it contains a `HIGH_IMPACT_ACTION`
    step); otherwise no step runs and the result's status is
    `ExecutionStatus.AWAITING_CONFIRMATION`. This mirrors the same
    policy the planner already encodes on the plan and steps
    (`haven.agents.policies`) — the executor enforces it again rather
    than trusting a caller not to skip it.

    A plan with no steps (for example one that only asks a clarifying
    question) runs nothing and reports `ExecutionStatus.NO_ACTION`.

    ``max_attempts`` bounds how many times a step that failed for a
    possibly-temporary reason is tried (default 2, i.e. one retry).

    ``memory``, if given, is used to record a `MOVIE_NIGHT_PREPARED`
    activity when a plan that prepares a media session completes in full.
    A memory failure is logged and never fails execution.

    ``home`` / ``security`` default to services built over ``household``.

    Raises `ValueError` if ``max_attempts`` is less than 1.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    started_at = datetime.now(UTC)

    if plan.status is PlanStatus.AWAITING_CONFIRMATION and not confirmed:
        logger.info("Plan %s awaits confirmation; not executing.", plan.id)
        return _empty_result(plan, ExecutionStatus.AWAITING_CONFIRMATION, started_at)

    if plan.clarification_needed or not plan.steps:
        logger.info("Plan %s has no steps to execute.", plan.id)
        return _empty_result(plan, ExecutionStatus.NO_ACTION, started_at)

    context = ActionContext(household=household, media=media, home=home, security=security)
    step_results = [
        run_step(step, context, confirmed=confirmed, max_attempts=max_attempts)
        for step in sorted(plan.steps, key=lambda s: s.order)
    ]

    status = status_for_results(step_results)
    completed_at = datetime.now(UTC)

    logger.info(
        "Executed plan %s: status=%s succeeded=%s failed=%s",
        plan.id,
        status.value,
        sum(1 for r in step_results if r.status is StepExecutionStatus.SUCCEEDED),
        sum(1 for r in step_results if r.status is StepExecutionStatus.FAILED),
    )

    if memory is not None and status is ExecutionStatus.COMPLETED and household_id is not None:
        _record_completion(memory, household_id, plan, step_results)

    return ExecutionResult(
        plan_id=plan.id,
        goal=plan.goal,
        status=status,
        step_results=step_results,
        started_at=started_at,
        completed_at=completed_at,
    )


def run_step(
    step: PlanStep,
    context: ActionContext,
    *,
    confirmed: bool,
    max_attempts: int = 1,
) -> StepExecutionResult:
    """Run one step, retrying a possibly-temporary failure up to `max_attempts`.

    Never raises: an unsupported action, a handler that throws, and an
    unconfirmed high-impact step all come back as a result.
    """

    if step.requires_confirmation and not confirmed:
        return StepExecutionResult(
            step_id=step.id,
            action=step.action,
            status=StepExecutionStatus.SKIPPED,
            detail="Step requires confirmation, which was not given.",
        )

    handler = get_handler(step.action)
    if handler is None:
        return StepExecutionResult(
            step_id=step.id,
            action=step.action,
            status=StepExecutionStatus.FAILED,
            detail=f"No executor is registered for action '{step.action}'.",
            error=ERR_UNSUPPORTED_ACTION,
        )

    result: StepExecutionResult | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = handler(step, context)
        except Exception as exc:  # noqa: BLE001 - a handler bug must not crash the plan
            logger.exception("Handler for action '%s' raised unexpectedly.", step.action)
            result = StepExecutionResult(
                step_id=step.id,
                action=step.action,
                status=StepExecutionStatus.FAILED,
                detail=f"Executing '{step.title}' raised an unexpected error.",
                error=str(exc),
            )
        result = result.model_copy(update={"attempts": attempt})

        if result.status is StepExecutionStatus.SUCCEEDED:
            break
        if result.error in NON_RETRYABLE_ERRORS:
            break
        if attempt < max_attempts:
            logger.info("Retrying step %s (%s) after: %s", step.id, step.action, result.detail)

    assert result is not None  # max_attempts >= 1 guarantees one iteration
    return result


def _empty_result(plan: Plan, status: ExecutionStatus, started_at: datetime) -> ExecutionResult:
    return ExecutionResult(
        plan_id=plan.id,
        goal=plan.goal,
        status=status,
        step_results=[],
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def _record_completion(
    memory: ActivityRecorder,
    household_id: str,
    plan: Plan,
    step_results: list[StepExecutionResult],
) -> None:
    if not _is_movie_night_plan(plan):
        return

    details: dict[str, str | int | float | bool] = {
        "plan_id": plan.id,
        "step_count": len(plan.steps),
    }

    selected = next(
        (
            r
            for r in step_results
            if r.action == "select_media" and r.status is StepExecutionStatus.SUCCEEDED
        ),
        None,
    )
    if selected is not None:
        # Recorded so a future recommendation (haven.mcp.tools.recommendations)
        # can avoid re-suggesting whatever was just watched.
        media_id = selected.effect.get("media_id")
        title = selected.effect.get("title")
        if isinstance(media_id, str):
            details["media_id"] = media_id
        if isinstance(title, str):
            details["title"] = title

    try:
        memory.record_activity(
            household_id,
            ActivityType.MOVIE_NIGHT_PREPARED,
            f"Movie night prepared: {plan.summary}",
            details=details,
        )
    except Exception:  # noqa: BLE001 - memory is best-effort here
        logger.exception("Failed to record movie night activity for plan %s.", plan.id)


def _is_movie_night_plan(plan: Plan) -> bool:
    # Decided from what the plan *does*, not from words in the goal: a
    # leaving-home plan for "we're leaving for the movie theater" must not
    # be recorded as a movie night.
    return any(step.action == "prepare_media_session" for step in plan.steps)


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "ActivityRecorder",
    "execute_plan",
    "run_step",
]
