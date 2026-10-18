"""MCP tools: `propose_household_plan`, `get_plan_status`.

Proposing a plan resolves context the same way `get_household_context`
does, then hands it to Person A's planner. Nothing is executed here —
that's `haven.mcp.tools.execution.execute_household_plan`. The proposed
plan is kept in the server's `PlanStore` (`haven.mcp.server`) so a later
`get_plan_status` or `execute_household_plan` call can find it by id.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from haven.context.resolver import resolve_context
from haven.mcp.schemas import PlanStatusSummary, PlanSummary, plan_to_summary
from haven.mcp.tools.context import build_sources
from haven.planning.plan_service import create_household_plan


def propose_household_plan(app, goal: str) -> PlanSummary:
    """Resolve context for `goal` and propose a structured, policy-checked
    plan. Raises `ValueError` for an empty goal.
    """

    if not goal or not goal.strip():
        raise ValueError("goal must not be empty")

    household = app.household.get_snapshot()
    media_options = app.media.list_media_items()
    sources = build_sources(app)
    context = resolve_context(goal, household, media_options, sources)

    plan = create_household_plan(goal, context, config=app.config)

    app.plans.save(plan, household.household_id)
    app.memory.record_plan_proposed(household.household_id, plan)

    return plan_to_summary(plan)


def get_plan_status(app, plan_id: str) -> PlanStatusSummary:
    """Look up a previously proposed plan by id.

    Raises `ValueError` if `plan_id` is empty or unknown to this server
    process (plans do not currently persist across a server restart —
    see `haven.mcp.server.PlanStore`).
    """

    if not plan_id or not plan_id.strip():
        raise ValueError("plan_id must not be empty")

    stored = app.plans.get(plan_id)
    if stored is None:
        raise ValueError(f"Unknown plan_id: {plan_id!r}")

    execution_status = stored.execution.status.value if stored.execution else None
    verification_status = stored.verification.status.value if stored.verification else None

    return PlanStatusSummary(
        plan_id=stored.plan.id,
        goal=stored.plan.goal,
        plan_status=stored.plan.status.value,
        requires_confirmation=stored.plan.requires_confirmation,
        execution_status=execution_status,
        verification_status=verification_status,
        message=_status_message(stored),
    )


def _status_message(stored) -> str:
    if stored.plan.clarification_needed:
        prompt = stored.plan.clarification_prompt or "More information is needed."
        return f"Haven needs more information before it can plan this: {prompt}"

    if stored.execution is None:
        if stored.plan.requires_confirmation:
            return "Proposed and awaiting your confirmation before it can run."
        return "Proposed and ready to execute."

    execution_status = stored.execution.status.value
    verification_status = stored.verification.status.value if stored.verification else "UNKNOWN"
    return f"Executed: {execution_status.title()}; verified: {verification_status.title()}."


def register(mcp) -> None:
    from haven.mcp.server import get_app

    @mcp.tool()
    def propose_household_plan_tool(goal: str, ctx: Context) -> PlanSummary:
        """Resolve the current household context for a goal (e.g. 'get
        movie night ready') and propose a structured, multi-step plan.
        Nothing is executed yet — use execute_household_plan with the
        returned plan_id when ready.
        """

        return propose_household_plan(get_app(ctx), goal)

    @mcp.tool()
    def get_plan_status_tool(plan_id: str, ctx: Context) -> PlanStatusSummary:
        """Look up a previously proposed plan's current status, including
        execution and verification results once it has been executed.
        """

        return get_plan_status(get_app(ctx), plan_id)


__all__ = ["propose_household_plan", "get_plan_status", "register"]
