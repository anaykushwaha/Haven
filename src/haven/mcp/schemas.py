"""MCP-facing schemas.

The stable contract Alexa+ (or any other MCP client) sees. Internal
models (`haven.models.context`, `haven.models.plan`,
`haven.execution.results`, `haven.verification.verifier`) can keep
evolving without breaking Alexa+ integration, as long as the mapping
functions here are kept in sync with them. Nothing in this module talks
to a backend, a store, or the `mcp` package — it is pure data shape plus
translation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from haven.execution.results import ExecutionResult, ExecutionStatus, StepExecutionResult
from haven.memory.preferences import Preference, PreferenceValue
from haven.models.context import HouseholdContext
from haven.models.plan import Plan
from haven.verification.verifier import StepVerification, VerificationResult, VerificationStatus

# --------------------------------------------------------------------------- #
# Request shapes
# --------------------------------------------------------------------------- #
#
# The live tool functions in `haven.mcp.tools.*` keep flat, named
# parameters (goal, plan_id, confirmed, ...) rather than accepting one of
# these as a single argument — flat parameters map onto Alexa+ slot
# filling far more naturally than a nested object would, and that's
# already working, tested wiring from Phase 3. These models exist purely
# so each tool's request shape is formally documented and exportable as
# JSON Schema (see scripts/export_mcp_schemas.py and
# apps/alexa/schemas/), without duplicating that shape into the tool
# signatures themselves. If a tool's parameters change, update its
# function signature first and this model to match — not the reverse.


class GetHouseholdContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(description="What the household is trying to do, e.g. 'get movie night ready'.")


class ProposeHouseholdPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(description="What the household is trying to do.")


class GetPlanStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(description="A plan_id previously returned by propose_household_plan.")


class ExecuteHouseholdPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(description="A plan_id previously returned by propose_household_plan.")
    confirmed: bool = Field(
        default=False,
        description="Must be true for a plan that includes a high-impact step; otherwise nothing runs.",
    )


class SaveHouseholdPreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Preference key, e.g. 'preferred_genres' or 'preferred_room'.")
    value: PreferenceValue = Field(description="The value to save for this preference key.")
    person_id: str | None = Field(
        default=None, description="Scope the preference to one household member instead of the whole household."
    )


class GetActivityRecommendationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str | None = Field(
        default=None,
        description="Optional goal to steer context resolution; defaults to a general tonight recommendation.",
    )


# --------------------------------------------------------------------------- #
# Household context
# --------------------------------------------------------------------------- #


class PersonSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    home: bool


class RoomSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class DeviceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    room_id: str | None
    capabilities: list[str]
    available: bool


class MediaOptionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    duration_minutes: int
    available: bool


class HouseholdContextSummary(BaseModel):
    """What Alexa+ sees when it asks Haven for the current household state."""

    model_config = ConfigDict(extra="forbid")

    household_id: str
    people: list[PersonSummary]
    rooms: list[RoomSummary]
    devices: list[DeviceSummary]
    media_options: list[MediaOptionSummary]
    available_minutes: int | None
    preferred_room_id: str | None
    preferred_genres: list[str]


def context_to_summary(context: HouseholdContext) -> HouseholdContextSummary:
    """Map an internal `HouseholdContext` onto the MCP-facing summary."""

    return HouseholdContextSummary(
        household_id=context.household_id,
        people=[
            PersonSummary(id=p.id, name=p.display_name, home=p.presence.value == "PRESENT")
            for p in context.people
        ],
        rooms=[RoomSummary(id=r.id, name=r.name) for r in context.rooms],
        devices=[
            DeviceSummary(
                id=d.id,
                name=d.name,
                room_id=d.room_id,
                capabilities=list(d.capabilities),
                available=d.is_available,
            )
            for d in context.devices
        ],
        media_options=[
            MediaOptionSummary(
                id=m.id, title=m.title, duration_minutes=m.duration_minutes, available=m.is_available
            )
            for m in context.media_options
        ],
        available_minutes=context.available_minutes,
        preferred_room_id=context.preferred_room_id,
        preferred_genres=list(context.preferred_genres),
    )


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


class PlanStepSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    order: int
    title: str
    description: str
    risk: str
    requires_confirmation: bool


class PlanSummary(BaseModel):
    """A proposed plan, as returned by `propose_household_plan`."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    goal: str
    status: str
    requires_confirmation: bool
    summary: str
    steps: list[PlanStepSummary]


def plan_to_summary(plan: Plan) -> PlanSummary:
    """Map an internal `Plan` onto the MCP-facing summary."""

    return PlanSummary(
        plan_id=plan.id,
        goal=plan.goal,
        status=plan.status.value,
        requires_confirmation=plan.requires_confirmation,
        summary=plan.summary,
        steps=[
            PlanStepSummary(
                id=step.id,
                order=step.order,
                title=step.title,
                description=step.description,
                risk=step.risk.value,
                requires_confirmation=step.requires_confirmation,
            )
            for step in sorted(plan.steps, key=lambda s: s.order)
        ],
    )


class PlanStatusSummary(BaseModel):
    """A previously proposed plan's current status, as returned by
    `get_plan_status` — the plan itself, plus execution/verification
    status once it has been run.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    goal: str
    plan_status: str
    requires_confirmation: bool
    execution_status: str | None
    verification_status: str | None
    message: str


# --------------------------------------------------------------------------- #
# Execution + verification
# --------------------------------------------------------------------------- #


class StepExecutionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    action: str
    status: str
    detail: str


class StepVerificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    action: str
    status: str
    detail: str


class ExecutionSummary(BaseModel):
    """The result of `execute_household_plan`: what ran, and what Haven
    confirmed actually happened afterward.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    goal: str
    execution_status: str
    verification_status: str
    steps: list[StepExecutionSummary]
    verifications: list[StepVerificationSummary]
    message: str


def step_execution_to_summary(result: StepExecutionResult) -> StepExecutionSummary:
    return StepExecutionSummary(
        step_id=result.step_id, action=result.action, status=result.status.value, detail=result.detail
    )


def step_verification_to_summary(verification: StepVerification) -> StepVerificationSummary:
    return StepVerificationSummary(
        step_id=verification.step_id,
        action=verification.action,
        status=verification.status.value,
        detail=verification.detail,
    )


def execution_and_verification_to_summary(
    plan: Plan,
    execution: ExecutionResult,
    verification: VerificationResult,
) -> ExecutionSummary:
    """Combine an `ExecutionResult` and its `VerificationResult` into the
    single response `execute_household_plan` returns. Haven's own design
    ties execution to verification (Layer 4: "execute, then check"), so
    the MCP surface reflects that as one action, not two.
    """

    return ExecutionSummary(
        plan_id=plan.id,
        goal=plan.goal,
        execution_status=execution.status.value,
        verification_status=verification.status.value,
        steps=[step_execution_to_summary(r) for r in execution.step_results],
        verifications=[step_verification_to_summary(v) for v in verification.step_verifications],
        message=_execution_message(execution, verification),
    )


def _execution_message(execution: ExecutionResult, verification: VerificationResult) -> str:
    if execution.status is ExecutionStatus.AWAITING_CONFIRMATION:
        return "This plan includes a high-impact step and needs your confirmation before it runs."
    if execution.status is ExecutionStatus.COMPLETED and verification.status is VerificationStatus.VERIFIED:
        return "Done — every step ran and was confirmed."
    if execution.status is ExecutionStatus.FAILED:
        return "Nothing completed successfully. Check the step details for what went wrong."

    problems = [v.detail for v in verification.step_verifications if v.status.value == "MISMATCH"]
    if problems:
        return "Partially done — " + " ".join(problems)
    return "Partially done — some steps didn't complete. Check the step details."


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #


class PreferenceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    household_id: str
    key: str
    value: PreferenceValue
    scope: str


def preference_to_summary(preference: Preference) -> PreferenceSummary:
    return PreferenceSummary(
        household_id=preference.household_id,
        key=preference.key,
        value=preference.value,
        scope=preference.scope,
    )


# --------------------------------------------------------------------------- #
# Recommendations
# --------------------------------------------------------------------------- #


class RecommendationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    title: str
    duration_minutes: int
    reason: str


class RecommendationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    household_id: str
    available_minutes: int | None
    recommendations: list[RecommendationSummary] = Field(default_factory=list)
    message: str


__all__ = [
    "GetHouseholdContextRequest",
    "ProposeHouseholdPlanRequest",
    "GetPlanStatusRequest",
    "ExecuteHouseholdPlanRequest",
    "SaveHouseholdPreferenceRequest",
    "GetActivityRecommendationsRequest",
    "PersonSummary",
    "RoomSummary",
    "DeviceSummary",
    "MediaOptionSummary",
    "HouseholdContextSummary",
    "context_to_summary",
    "PlanStepSummary",
    "PlanSummary",
    "plan_to_summary",
    "PlanStatusSummary",
    "StepExecutionSummary",
    "StepVerificationSummary",
    "ExecutionSummary",
    "step_execution_to_summary",
    "step_verification_to_summary",
    "execution_and_verification_to_summary",
    "PreferenceSummary",
    "preference_to_summary",
    "RecommendationSummary",
    "RecommendationsResponse",
]
