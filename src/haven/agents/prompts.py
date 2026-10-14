"""Central planning prompts and instructions for Haven.

All planner prompt text lives here so the Strands + Bedrock reasoner, the
mock reasoner, and tests share one source of truth.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from haven.models.context import HouseholdContext
from haven.models.plan import ActionRisk, PlanStatus
from haven.planning.models import OfferedCandidate
from haven.planning.workflows import classify_workflow

PLANNER_SYSTEM_PROMPT = """You are Haven, a household planning agent.

Your job is planning only. Do not execute device actions, do not verify
effects, and do not persist or deploy anything. Produce an explicit
structured plan made of understandable steps.

Planning rules:
- Use only the supplied household context.
- Use the minimum household context required for the user's request.
- Do not invent unavailable devices, people, rooms, media, preferences, or other facts.
- If required information is missing, prefer clarification over guessing.
- Respect deterministic constraints already applied to candidate actions.
  If ordinary rules have rejected an option, do not revive it.
- Respect action-risk policy:
  READ does not require confirmation.
  REVERSIBLE_ACTION may proceed automatically under current policy.
  HIGH_IMPACT_ACTION always requires explicit user confirmation.
- Treat high-impact actions conservatively. Include them only when the
  user's goal clearly requires them, and mark them as requiring confirmation.
- Keep steps concrete, minimal, and understandable.
- Distinguish planning from execution and verification. Never claim that
  a step has already been performed.

Workflow guidance (the workflow is decided deterministically before you see
the candidates; you choose among the candidates it produced):
- movie_night: pick one title that fits the available time, prepare the
  session on the chosen device, and dim lights only in that room.
- leaving_home: comfort actions (lights, media, thermostat) may run on their
  own. Locks and the security system are HIGH_IMPACT_ACTION and always need
  confirmation; never assume a security system exists if none is offered.
- tonight_planning: suggestions only. Every candidate is READ; recommend,
  do not operate devices. Ask for clarification when the free time is
  unknown rather than guessing.
"""

CANDIDATE_SELECTION_RULES = """Decision rules:
- You may choose ONLY among the offered candidate actions, by candidate_id.
- Order the selected candidate_ids in the sequence they should run.
- Do not invent new actions, devices, rooms, media, or risk levels.
- Actions that are absent from the offered list were rejected by
  deterministic constraints or do not exist. Do not reintroduce them.
- Risk classification and confirmation requirements are fixed by Haven's
  policy; you cannot change them.
- If the offered candidates cannot satisfy the goal, set
  clarification_needed to true, leave selected_candidate_ids empty, and
  provide a short, specific clarification_prompt for the user.
- Return the structured decision only. Selecting is planning, not
  execution: nothing happens until a later, separate execution step.
"""


class _PromptContext(BaseModel):
    """Minimal household view sent to the model alongside candidates.

    People and presence are deliberately excluded: they are temporary
    context and are not needed to choose among pre-approved candidates.
    """

    model_config = ConfigDict(extra="forbid")

    household_id: str
    available_minutes: int | None = None
    preferred_room_id: str | None = None
    preferred_genres: list[str] = Field(default_factory=list)
    rooms: list[dict[str, str]] = Field(default_factory=list)
    devices: list[dict[str, object]] = Field(default_factory=list)
    media: list[dict[str, object]] = Field(default_factory=list)


def build_planning_prompt(
    goal: str,
    context: HouseholdContext,
    candidates: Sequence[OfferedCandidate] | None = None,
) -> str:
    """Build the user-turn planning prompt.

    Without ``candidates`` (Phase 1 behaviour) the full resolved context is
    included. With ``candidates`` the prompt contains only the context those
    candidates reference plus the selection rules and the structured
    decision requirement.
    """

    if candidates is None:
        return _full_context_prompt(goal, context)
    return _candidate_selection_prompt(goal, context, candidates)


def _full_context_prompt(goal: str, context: HouseholdContext) -> str:
    context_json = context.model_dump_json(indent=2)
    return (
        "Create a structured household plan for the following request.\n\n"
        f"Goal:\n{goal}\n\n"
        "Resolved household context (JSON). Use only these facts:\n"
        f"{context_json}\n\n"
        "Allowed action risk values:\n"
        f"{', '.join(risk.value for risk in ActionRisk)}\n\n"
        "Allowed plan status values for a newly proposed plan:\n"
        f"{PlanStatus.PROPOSED.value}, {PlanStatus.AWAITING_CONFIRMATION.value}\n\n"
        "Return a plan with explicit steps. Do not execute anything."
    )


def _candidate_selection_prompt(
    goal: str,
    context: HouseholdContext,
    candidates: Sequence[OfferedCandidate],
) -> str:
    minimal = minimal_prompt_context(context, candidates)
    candidates_json = "[\n" + ",\n".join(c.model_dump_json(indent=2) for c in candidates) + "\n]"
    workflow = classify_workflow(goal)
    return (
        "Plan the following household request by selecting among the offered candidate "
        "actions.\n\n"
        f"Goal:\n{goal}\n\n"
        f"Workflow: {workflow.value}\n\n"
        "Relevant household context (JSON). Use only these facts:\n"
        f"{minimal.model_dump_json(indent=2)}\n\n"
        "Offered candidate actions (JSON). These already passed Haven's deterministic "
        "constraints:\n"
        f"{candidates_json}\n\n"
        f"{CANDIDATE_SELECTION_RULES}\n"
        "Respond with a structured decision containing: summary, "
        "selected_candidate_ids, clarification_needed, clarification_prompt."
    )


def minimal_prompt_context(
    context: HouseholdContext, candidates: Sequence[OfferedCandidate]
) -> BaseModel:
    """Reduce the context to what the offered candidates reference."""

    device_ids = {c.device_id for c in candidates if c.device_id}
    room_ids = {c.room_id for c in candidates if c.room_id}
    media_ids = {c.media_id for c in candidates if c.media_id}

    # Rooms of referenced devices are relevant even when a candidate omits room_id.
    for device in context.devices:
        if device.id in device_ids and device.room_id:
            room_ids.add(device.room_id)

    return _PromptContext(
        household_id=context.household_id,
        available_minutes=context.available_minutes,
        preferred_room_id=context.preferred_room_id,
        preferred_genres=list(context.preferred_genres),
        rooms=[{"id": r.id, "name": r.name} for r in context.rooms if r.id in room_ids],
        devices=[
            {"id": d.id, "name": d.name, "room_id": d.room_id, "capabilities": list(d.capabilities)}
            for d in context.devices
            if d.id in device_ids
        ],
        media=[
            {"id": m.id, "title": m.title, "duration_minutes": m.duration_minutes}
            for m in context.media_options
            if m.id in media_ids
        ],
    )
