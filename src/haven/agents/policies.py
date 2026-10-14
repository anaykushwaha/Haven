"""Planning/action policy for Haven.

This module answers policy questions used during planning. It does not
execute actions, talk to devices, or call AWS.
"""

from __future__ import annotations

import logging

from haven.models.plan import ActionRisk, Plan, PlanStatus, PlanStep
from haven.planning.models import PolicyIssue, PolicyValidationResult

logger = logging.getLogger(__name__)

_ALLOWED_RISKS = frozenset(ActionRisk)


def requires_confirmation(risk: ActionRisk) -> bool:
    """Return True when this risk class needs explicit user confirmation.

    READ: no confirmation.
    REVERSIBLE_ACTION: may proceed automatically under current policy.
    HIGH_IMPACT_ACTION: always requires confirmation.
    """

    return risk is ActionRisk.HIGH_IMPACT_ACTION


def can_execute_without_confirmation(risk: ActionRisk) -> bool:
    """Return True when current policy would allow automatic execution.

    Future policy tightening should happen here rather than in the planner.
    """

    return not requires_confirmation(risk)


def confirmation_required_for_step(step: PlanStep) -> bool:
    """Return the policy-required confirmation flag for a step's risk."""

    return requires_confirmation(step.risk)


def plan_status_for_steps(steps: list[PlanStep]) -> PlanStatus:
    """Return the initial plan status implied by the steps' policy flags."""

    if any(step.requires_confirmation for step in steps):
        return PlanStatus.AWAITING_CONFIRMATION
    return PlanStatus.PROPOSED


def validate_plan_policy(plan: Plan) -> PolicyValidationResult:
    """Check that a proposed plan's risk flags match Haven policy.

    A policy-invalid plan must not be returned to callers. High-impact
    actions are allowed in a plan, but only when marked as requiring
    confirmation.
    """

    issues: list[PolicyIssue] = []

    if plan.clarification_needed:
        # A clarification plan asks a question instead of proposing actions.
        if plan.steps:
            issues.append(PolicyIssue(message="clarification plan must not contain steps"))
        if not (plan.clarification_prompt or "").strip():
            issues.append(PolicyIssue(message="clarification plan must carry a prompt"))
        if plan.requires_confirmation or plan.status is not PlanStatus.PROPOSED:
            issues.append(PolicyIssue(message="clarification plan cannot require confirmation"))
    elif not plan.steps:
        issues.append(PolicyIssue(message="plan contains no steps"))

    plan_needs_confirmation = False
    for step in plan.steps:
        if step.risk not in _ALLOWED_RISKS:
            issues.append(
                PolicyIssue(
                    step_id=step.id,
                    message="step has an unknown action risk",
                )
            )
            continue

        expected = requires_confirmation(step.risk)
        if expected:
            plan_needs_confirmation = True
        if step.requires_confirmation != expected:
            if expected:
                issues.append(
                    PolicyIssue(
                        step_id=step.id,
                        message="high-impact action must require user confirmation",
                    )
                )
            else:
                issues.append(
                    PolicyIssue(
                        step_id=step.id,
                        message="step confirmation flag does not match action risk policy",
                    )
                )

    any_step_flagged = any(step.requires_confirmation for step in plan.steps)
    if plan.requires_confirmation != any_step_flagged:
        issues.append(PolicyIssue(message="plan confirmation flag does not match its steps"))

    if plan_needs_confirmation and plan.status is not PlanStatus.AWAITING_CONFIRMATION:
        issues.append(
            PolicyIssue(
                message="plan with high-impact actions must await user confirmation",
            )
        )

    result = PolicyValidationResult(
        valid=not issues,
        issues=issues,
        requires_confirmation=plan_needs_confirmation,
    )
    if not result.valid:
        logger.info("Plan policy validation failed with %s issue(s)", len(issues))
    return result
