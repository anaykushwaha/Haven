"""Person A workflow harness: goal + demo HouseholdContext -> Plan.

Runs the planning path end to end without Alexa+, MCP, or execution.

Usage:
    python scripts/test_workflow.py movie-night
    python scripts/test_workflow.py leaving-home --backend mock
    python scripts/test_workflow.py tonight --backend bedrock

Exit codes: 0 plan proposed, 1 invalid input, 2 Bedrock failure,
3 Haven needs clarification before it can plan.

The demo HouseholdContext is built here explicitly so the harness does not
depend on Person B's context resolver. Available time comes from the demo
calendar integration. Nothing is executed: the output is a proposed plan.

Requires the package to be installed (``pip install -e .``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from haven.config import (
    BEDROCK_PLANNER_BACKEND,
    SUPPORTED_PLANNER_BACKENDS,
    HavenConfig,
    get_config,
)
from haven.integrations.calendar import demo_calendar
from haven.memory.service import MemoryService
from haven.models.context import (
    DeviceCapability,
    DeviceContext,
    HouseholdContext,
    MediaItem,
    PersonContext,
    PresenceState,
    RoomContext,
)
from haven.models.plan import Plan
from haven.planning.plan_service import create_household_plan

SCENARIOS: dict[str, str] = {
    "movie-night": "Get movie night ready",
    "leaving-home": "We're leaving the house, get it ready",
    "tonight": "What should we do tonight?",
}


def build_demo_context(available_minutes: int | None) -> HouseholdContext:
    """A hand-built planning-facing context conforming to the shared contract."""

    return HouseholdContext(
        household_id="demo-household",
        people=[
            PersonContext(id="person-ari", display_name="Ari", presence=PresenceState.PRESENT),
        ],
        rooms=[
            RoomContext(id="living-room", name="Living Room"),
            RoomContext(id="kitchen", name="Kitchen"),
        ],
        devices=[
            DeviceContext(
                id="tv-living-room",
                name="Living Room TV",
                room_id="living-room",
                capabilities=[DeviceCapability.MEDIA_PLAYBACK, DeviceCapability.DISPLAY],
            ),
            DeviceContext(
                id="lamp-living-room",
                name="Living Room Lamp",
                room_id="living-room",
                capabilities=[DeviceCapability.LIGHTING],
            ),
            DeviceContext(
                id="lock-front-door",
                name="Front Door Lock",
                capabilities=[DeviceCapability.LOCK],
            ),
        ],
        media_options=[
            MediaItem(id="media-epic", title="Long Epic", duration_minutes=145),
            MediaItem(id="media-feature", title="Short Feature", duration_minutes=95),
        ],
        available_minutes=available_minutes,
        preferred_room_id="living-room",
        preferred_genres=["comedy", "adventure"],
    )


def print_plan(plan: Plan, *, backend: str) -> None:
    print(f"Backend: {backend}")
    print(f"Plan ID: {plan.id}")
    print(f"Goal:    {plan.goal}")
    print(f"Status:  {plan.status.value}")
    print(f"Summary: {plan.summary}")
    print(f"Requires confirmation: {'yes' if plan.requires_confirmation else 'no'}")
    print(f"Candidates rejected by constraints: {plan.rejected_candidate_count}")
    print()
    if plan.clarification_needed:
        print(f"Haven asks: {plan.clarification_prompt}")
        print()
        print("No plan was proposed; Haven needs an answer first.")
        return
    print("Steps:")
    for step in plan.steps:
        confirm = "confirmation required" if step.requires_confirmation else "no confirmation"
        print(f"  {step.order}. {step.title}  [{step.risk.value}; {confirm}]")
        print(f"     {step.description}")
        if step.device_id or step.room_id:
            print(f"     device={step.device_id or '-'} room={step.room_id or '-'}")
    print()
    print("No actions were executed. This is a proposed plan only.")


def run(scenario: str, backend: str | None) -> int:
    goal = SCENARIOS[scenario]

    base = get_config()
    config = (
        HavenConfig(
            planner_backend=backend,
            max_plan_steps=base.max_plan_steps,
            log_level=base.log_level,
        )
        if backend
        else base
    )
    logging.basicConfig(level=config.log_level, format="%(levelname)s %(name)s: %(message)s")

    calendar = demo_calendar()
    available = calendar.available_minutes()
    context = build_demo_context(available)

    print(f"Scenario: {scenario}")
    print(f"Goal:     {goal}")
    print(f"Calendar: next event in {available} min ({calendar.next_event().title})")
    print()

    try:
        plan = create_household_plan(goal, context, config=config)
    except ValueError as exc:
        print(f"Planning failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # provider failures when backend=bedrock
        # ModelInvocationError already carries "[category] ..." in its message.
        print(f"Bedrock planning failed: {exc}", file=sys.stderr)
        if config.planner_backend == BEDROCK_PLANNER_BACKEND:
            print(
                "The Bedrock backend did not produce a plan. There is no fallback to mock; "
                "fix AWS access (credentials, account verification, model access) and rerun.",
                file=sys.stderr,
            )
        return 2

    print_plan(plan, backend=config.planner_backend)

    # Demonstrate the memory foundation without persisting anything.
    memory = MemoryService(default_household_id=context.household_id)
    memory.record_plan_proposed(context.household_id, plan)
    history = memory.lookup_fact("history:plan_proposed")
    print(f"Memory: {len(history or [])} plan(s) recorded this session (in-memory only).")
    return 0 if not plan.clarification_needed else 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Haven's planning workflow locally.")
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument(
        "--backend",
        choices=sorted(SUPPORTED_PLANNER_BACKENDS),
        default=None,
        help="Planner backend (default: HAVEN_PLANNER_BACKEND or mock).",
    )
    args = parser.parse_args(argv)
    return run(args.scenario, args.backend)


if __name__ == "__main__":
    sys.exit(main())
