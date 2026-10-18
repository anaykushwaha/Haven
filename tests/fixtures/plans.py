"""Plain builders for hand-made plans and an unreliable device backend."""

from __future__ import annotations

from typing import Any

from haven.models.plan import ActionRisk, Plan, PlanStatus, PlanStep

# Device ids from simulator/security/data/devices.json.
TV = "device-tv-living-room"
LIVING_ROOM_LIGHTS = "device-lights-living-room"
KITCHEN_LIGHTS = "device-lights-kitchen"
BEDROOM_LIGHTS = "device-lights-bedroom-1"
THERMOSTAT = "device-thermostat-living-room"
FRONT_DOOR = "device-lock-front-door"
BACK_DOOR = "device-lock-back-door"
SECURITY_SYSTEM = "device-security-system"


def make_step(
    action: str,
    *,
    order: int = 1,
    device_id: str | None = None,
    risk: ActionRisk = ActionRisk.REVERSIBLE_ACTION,
    requires_confirmation: bool | None = None,
    title: str | None = None,
    description: str | None = None,
) -> PlanStep:
    """Build one `PlanStep`; confirmation defaults from `risk` like the planner."""

    if requires_confirmation is None:
        requires_confirmation = risk is ActionRisk.HIGH_IMPACT_ACTION
    return PlanStep(
        id=f"step-{order}",
        order=order,
        action=action,
        title=title or action.replace("_", " ").capitalize(),
        description=description or f"Test step: {action}",
        risk=risk,
        requires_confirmation=requires_confirmation,
        device_id=device_id,
    )


def make_plan(steps: list[PlanStep], *, goal: str = "Test goal", plan_id: str = "plan-test") -> Plan:
    """Build a `Plan` whose status follows its steps, as the planner's does."""

    needs_confirmation = any(step.requires_confirmation for step in steps)
    return Plan(
        id=plan_id,
        goal=goal,
        status=PlanStatus.AWAITING_CONFIRMATION if needs_confirmation else PlanStatus.PROPOSED,
        summary=f"Test plan with {len(steps)} step(s).",
        steps=steps,
        requires_confirmation=needs_confirmation,
    )


def lock_front_door_plan() -> Plan:
    """A one-step plan with a single high-impact action."""

    return make_plan(
        [make_step("lock_door", device_id=FRONT_DOOR, risk=ActionRisk.HIGH_IMPACT_ACTION)],
        goal="Lock up",
        plan_id="plan-lock",
    )


def clarification_plan() -> Plan:
    """A plan that only asks a question and has no steps."""

    return Plan(
        id="plan-clarify",
        goal="Do something unclear",
        status=PlanStatus.PROPOSED,
        summary="Haven needs more information.",
        steps=[],
        requires_confirmation=False,
        clarification_needed=True,
        clarification_prompt="What would you like me to do?",
    )


class FlakyHouseholdBackend:
    """A household backend whose devices sometimes ignore commands.

    Wraps a real `HouseholdSimulatorService`. ``drop_writes(device_id, n)``
    makes the next ``n`` writes to that device silently do nothing — the
    command "succeeds" but the device never changes state — which is
    exactly the failure verification exists to catch, and what
    `recover_plan` exists to correct.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._drops: dict[str, int] = {}
        self.write_attempts: dict[str, int] = {}

    # The security integration reads ``backend.security`` when present; point
    # it at this wrapper so security writes are flaky too.
    @property
    def security(self) -> FlakyHouseholdBackend:
        return self

    def drop_writes(self, device_id: str, count: int = 1) -> None:
        self._drops[device_id] = count

    def set_device_state(
        self, device_id: str, *, state: dict | None = None, online: bool | None = None
    ) -> dict:
        self.write_attempts[device_id] = self.write_attempts.get(device_id, 0) + 1
        remaining = self._drops.get(device_id, 0)
        if remaining > 0:
            self._drops[device_id] = remaining - 1
            record = self._inner.get_device_state(device_id)
            if record is None:
                raise KeyError(f"Unknown device_id: {device_id}")
            return record
        return self._inner.set_device_state(device_id, state=state, online=online)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
