"""MCP tool: `execute_household_plan`.

Executes a previously proposed plan (looked up by id in the server's
`PlanStore`) and immediately verifies the result — Haven's Layer 4
("execute, then check") is not a separate MCP tool; it always happens as
part of executing a plan, matching how `scripts/run_movie_night.py`
chains `execute_plan` straight into `verify_plan` locally.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from haven.execution.executor import execute_plan
from haven.execution.results import ExecutionStatus
from haven.mcp.schemas import ExecutionSummary, execution_and_verification_to_summary
from haven.mcp.tools.planning import unknown_plan_error
from haven.verification.recovery import recover_plan
from haven.verification.verifier import VerificationStatus, verify_plan


def execute_household_plan(app, plan_id: str, confirmed: bool = False) -> ExecutionSummary:
    """Execute a previously proposed plan and verify what happened.

    `confirmed` must be `True` for a plan that includes a high-impact
    (irreversible) step; otherwise no step runs and the returned
    `execution_status` is `AWAITING_CONFIRMATION`. This mirrors the same
    policy already encoded on the plan (`haven.agents.policies`) and
    enforced again by `execute_plan` itself — Haven never silently runs a
    high-impact action just because a client forgot to ask.

    A plan that already ran to completion and was verified is not run
    again: a client that retries after a timeout (or asks twice) gets the
    earlier result back, so doors are not re-actuated and the same movie
    night is not remembered twice. A plan that only partly worked, or was
    waiting for confirmation, can be executed again.

    Raises `ValueError` if `plan_id` is empty or unknown to this server
    process.
    """

    if not plan_id or not plan_id.strip():
        raise ValueError("plan_id must not be empty")

    stored = app.plans.get(plan_id)
    if stored is None:
        raise unknown_plan_error(plan_id)

    if (
        stored.execution is not None
        and stored.verification is not None
        and stored.execution.status is ExecutionStatus.COMPLETED
        and stored.verification.status is VerificationStatus.VERIFIED
    ):
        earlier = execution_and_verification_to_summary(
            stored.plan, stored.execution, stored.verification
        )
        return earlier.model_copy(
            update={"message": "Already done earlier — nothing was run again. " + earlier.message}
        )

    execution_result = execute_plan(
        stored.plan,
        household=app.household,
        media=app.media,
        household_id=stored.household_id,
        confirmed=confirmed,
        memory=app.memory,
    )
    verification_result = verify_plan(
        stored.plan, execution_result, household=app.household, media=app.media
    )

    # One bounded recovery pass: anything verification found did not take
    # hold is run again and re-checked, so a device that silently reverted
    # is corrected, or the mismatch is reported honestly if it is not.
    execution_result, verification_result = recover_plan(
        stored.plan,
        execution_result,
        verification_result,
        household=app.household,
        media=app.media,
    )

    app.plans.record_execution(plan_id, execution_result, verification_result)

    return execution_and_verification_to_summary(stored.plan, execution_result, verification_result)


def register(mcp) -> None:
    from haven.mcp.server import get_app

    @mcp.tool()
    def execute_household_plan_tool(
        plan_id: str, ctx: Context, confirmed: bool = False
    ) -> ExecutionSummary:
        """Execute a previously proposed plan (by the plan_id returned
        from propose_household_plan) and verify the result. Set confirmed
        to true only after the user has explicitly agreed to a plan that
        includes a high-impact step — otherwise leave it false and Haven
        will report that it's waiting for confirmation instead of acting.
        """

        return execute_household_plan(get_app(ctx), plan_id, confirmed=confirmed)


__all__ = ["execute_household_plan", "register"]
