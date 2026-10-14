from __future__ import annotations

from haven.agents.policies import (
    can_execute_without_confirmation,
    plan_status_for_steps,
    requires_confirmation,
    validate_plan_policy,
)
from haven.models.plan import ActionRisk, Plan, PlanStatus, PlanStep


def _step(step_id: str, risk: ActionRisk, *, confirm: bool) -> PlanStep:
    return PlanStep(
        id=step_id,
        order=1,
        action="noop",
        title="t",
        description="d",
        risk=risk,
        requires_confirmation=confirm,
    )


def _plan(steps: list[PlanStep], *, status: PlanStatus, confirm: bool) -> Plan:
    return Plan(
        id="plan-x",
        goal="g",
        status=status,
        summary="s",
        steps=steps,
        requires_confirmation=confirm,
    )


def test_read_and_reversible_do_not_require_confirmation() -> None:
    assert not requires_confirmation(ActionRisk.READ)
    assert not requires_confirmation(ActionRisk.REVERSIBLE_ACTION)
    assert can_execute_without_confirmation(ActionRisk.READ)
    assert can_execute_without_confirmation(ActionRisk.REVERSIBLE_ACTION)


def test_high_impact_requires_confirmation() -> None:
    assert requires_confirmation(ActionRisk.HIGH_IMPACT_ACTION)
    assert not can_execute_without_confirmation(ActionRisk.HIGH_IMPACT_ACTION)


def test_plan_status_reflects_confirmation() -> None:
    assert (
        plan_status_for_steps([_step("a", ActionRisk.READ, confirm=False)]) is PlanStatus.PROPOSED
    )
    assert (
        plan_status_for_steps([_step("a", ActionRisk.HIGH_IMPACT_ACTION, confirm=True)])
        is PlanStatus.AWAITING_CONFIRMATION
    )


def test_validate_accepts_policy_consistent_plan() -> None:
    plan = _plan(
        [
            _step("a", ActionRisk.READ, confirm=False),
            _step("b", ActionRisk.REVERSIBLE_ACTION, confirm=False),
        ],
        status=PlanStatus.PROPOSED,
        confirm=False,
    )
    result = validate_plan_policy(plan)
    assert result.valid
    assert not result.requires_confirmation


def test_validate_rejects_unconfirmed_high_impact() -> None:
    plan = _plan(
        [_step("unlock", ActionRisk.HIGH_IMPACT_ACTION, confirm=False)],
        status=PlanStatus.PROPOSED,
        confirm=False,
    )
    result = validate_plan_policy(plan)
    assert not result.valid
    assert any(issue.step_id == "unlock" for issue in result.issues)


def test_validate_rejects_empty_plan() -> None:
    plan = _plan([], status=PlanStatus.PROPOSED, confirm=False)
    assert not validate_plan_policy(plan).valid


def test_validate_requires_awaiting_status_for_high_impact() -> None:
    plan = _plan(
        [_step("unlock", ActionRisk.HIGH_IMPACT_ACTION, confirm=True)],
        status=PlanStatus.PROPOSED,
        confirm=True,
    )
    result = validate_plan_policy(plan)
    assert not result.valid
    assert result.requires_confirmation
