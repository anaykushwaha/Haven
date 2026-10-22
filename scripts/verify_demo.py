"""Rehearse Haven's demo against the real tool functions.

Runs every beat of the demo (see docs/demo.md) through the same
`haven.mcp.tools.*` functions Alexa+ reaches over MCP, on a fresh simulated
household, and checks what the demo *says* happens actually happens:
the plan Haven proposes, the confirmation gate, what changed in the home,
what Haven verified, and how it reports a failure. If a beat drifts, this
fails loudly before you record rather than during.

Usage:
    python scripts/verify_demo.py
    python scripts/verify_demo.py --dump-snapshots build/demo-snapshots

`--dump-snapshots DIR` also writes the dashboard's JSON at each checkpoint,
which is how the dashboard screenshots in docs/images are produced.

Fully offline: mock planner, in-memory persistence, simulators. It does not
exercise Alexa+, AgentCore or Bedrock — see docs/testing.md for those.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from haven.config import HavenConfig
from haven.mcp.dashboard import build_dashboard_snapshot
from haven.mcp.server import AppContext, build_app_context, reset_app_context
from haven.mcp.tools.execution import execute_household_plan
from haven.mcp.tools.planning import get_plan_status, propose_household_plan
from haven.mcp.tools.preferences import save_household_preference
from haven.mcp.tools.recommendations import get_activity_recommendations

FRONT_DOOR = "device-lock-front-door"
BACK_DOOR = "device-lock-back-door"
LIVING_ROOM_TV = "device-tv-living-room"
LIVING_ROOM_LIGHTS = "device-lights-living-room"
SECURITY_SYSTEM = "device-security-system"


@dataclass
class Check:
    description: str
    passed: bool
    detail: str = ""


@dataclass
class BeatResult:
    number: int
    title: str
    says: str
    checks: list[Check] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(check.passed for check in self.checks)


class Rehearsal:
    """One run of the demo against a fresh simulated household."""

    def __init__(self, dump_dir: Path | None = None) -> None:
        reset_app_context()
        self.app: AppContext = build_app_context(
            HavenConfig(planner_backend="mock", persistence_backend="memory")
        )
        self._dump_dir = dump_dir
        self._checks: list[Check] = []
        if dump_dir is not None:
            dump_dir.mkdir(parents=True, exist_ok=True)

    # -- helpers -------------------------------------------------------------- #

    def expect(self, description: str, condition: bool, detail: str = "") -> None:
        self._checks.append(Check(description, bool(condition), "" if condition else detail))

    def state(self, device_id: str) -> dict:
        record = self.app.household.get_device_state(device_id)
        return {} if record is None else record["state"]

    def checkpoint(self, name: str) -> None:
        """Save the dashboard's view of this moment, if dumping."""

        if self._dump_dir is None:
            return
        snapshot = build_dashboard_snapshot(self.app)
        (self._dump_dir / f"{name}.json").write_text(
            json.dumps(json.loads(snapshot.model_dump_json()), indent=2) + "\n", encoding="utf-8"
        )

    def fresh_home(self) -> None:
        """Put the simulated home back to its starting state (plans are kept)."""

        self.app.household.reload()

    def take(self) -> list[Check]:
        checks, self._checks = self._checks, []
        return checks

    # -- the beats (keep in step with docs/demo.md) ------------------------------ #

    def beat_tonight(self) -> None:
        self.checkpoint("01-idle")
        result = get_activity_recommendations(self.app)
        self.expect("Haven offers one to three options", 1 <= len(result.recommendations) <= 3)
        self.expect(
            "every option fits the free time",
            result.available_minutes is not None
            and all(r.duration_minutes <= result.available_minutes for r in result.recommendations),
        )
        self.expect("nothing in the home changed", self.state(LIVING_ROOM_LIGHTS)["power"] == "on")

    def beat_preference(self) -> None:
        saved = save_household_preference(self.app, "preferred_genres", ["sci-fi", "comedy"])
        self.expect("the preference is saved for the household", saved.scope == "household")
        top = get_activity_recommendations(self.app).recommendations[0]
        self.expect(
            "recommendations now reflect it",
            any(genre in top.reason.lower() for genre in ("sci-fi", "comedy")),
            f"top reason was: {top.reason}",
        )
        try:
            save_household_preference(self.app, "room_occupied", True)
            self.expect("temporary state is refused as memory", False, "it was accepted")
        except ValueError:
            self.expect("temporary state is refused as memory", True)

    def beat_movie_night(self) -> None:
        plan = propose_household_plan(self.app, "Get movie night ready")
        self.expect("the plan has three steps", len(plan.steps) == 3, f"got {len(plan.steps)}")
        self.expect("it needs no confirmation", plan.requires_confirmation is False)
        result = execute_household_plan(self.app, plan.plan_id)
        self.expect(
            "it completes and verifies",
            (result.execution_status, result.verification_status) == ("COMPLETED", "VERIFIED"),
            f"{result.execution_status}/{result.verification_status}",
        )
        session = self.app.media.get_session_state(LIVING_ROOM_TV)
        self.expect("a film is queued on the TV", session is not None and session.status.value == "ready")
        self.expect("the lights are dimmed", self.state(LIVING_ROOM_LIGHTS).get("brightness") == 20)
        self.checkpoint("02-movie-night")

    def beat_leaving_home(self) -> None:
        plan = propose_household_plan(self.app, "We're leaving the house")
        actions = [step.title for step in plan.steps]
        self.expect("the plan has eight steps", len(plan.steps) == 8, f"got {len(plan.steps)}")
        self.expect("it waits for confirmation", plan.status == "AWAITING_CONFIRMATION")
        self.expect(
            "locking and arming are the confirmed steps",
            sum(1 for step in plan.steps if step.requires_confirmation) == 3,
            str(actions),
        )
        self.checkpoint("03-leaving-awaiting-confirmation")

        held = execute_household_plan(self.app, plan.plan_id)
        self.expect("without a yes, nothing runs", held.execution_status == "AWAITING_CONFIRMATION")
        self.expect("the front door is still unlocked", self.state(FRONT_DOOR).get("locked") is False)
        self.expect(
            "the status tool says it is waiting",
            "confirmation" in get_plan_status(self.app, plan.plan_id).message.lower(),
        )

        done = execute_household_plan(self.app, plan.plan_id, confirmed=True)
        self.expect(
            "after a yes it completes and verifies",
            (done.execution_status, done.verification_status) == ("COMPLETED", "VERIFIED"),
            f"{done.execution_status}/{done.verification_status}",
        )
        self.expect(
            "both doors are locked",
            self.state(FRONT_DOOR).get("locked") is True and self.state(BACK_DOOR).get("locked") is True,
        )
        self.expect("security is armed", self.state(SECURITY_SYSTEM).get("armed") is True)
        self.expect(
            "the movie from earlier was stopped",
            self.app.media.get_session_state(LIVING_ROOM_TV) is None,
        )
        self.checkpoint("04-leaving-verified")

    def beat_failure(self) -> None:
        self.fresh_home()
        plan = propose_household_plan(self.app, "We're leaving the house")
        self.app.household.set_device_state(FRONT_DOOR, online=False)  # drops off after planning
        partial = execute_household_plan(self.app, plan.plan_id, confirmed=True)
        self.expect("Haven reports a partial result", partial.execution_status == "PARTIAL")
        self.expect("it says which door did not respond", FRONT_DOOR in partial.message)
        self.expect("everything else still ran", self.state(BACK_DOOR).get("locked") is True)
        self.checkpoint("05-partial-failure")

        self.app.household.set_device_state(FRONT_DOOR, online=True)
        retried = execute_household_plan(self.app, plan.plan_id, confirmed=True)
        self.expect(
            "once the door is back, running it again finishes the job",
            (retried.execution_status, retried.verification_status) == ("COMPLETED", "VERIFIED"),
        )

    def beat_question(self) -> None:
        self.fresh_home()
        self.app.household.set_device_state(LIVING_ROOM_TV, online=False)
        plan = propose_household_plan(self.app, "Get movie night ready")
        self.expect("Haven asks instead of guessing", plan.clarification_needed and bool(plan.clarification_prompt))
        self.expect("there are no steps to run", plan.steps == [])
        result = execute_household_plan(self.app, plan.plan_id)
        self.expect("executing it runs nothing", result.execution_status == "NO_ACTION")
        self.checkpoint("06-clarification")


BEATS: list[tuple[str, str, str]] = [
    ("beat_tonight", "Ask what to do tonight", "What should we do tonight?"),
    ("beat_preference", "Teach Haven a preference", "We like sci-fi and comedy."),
    ("beat_movie_night", "Movie night", "Get movie night ready."),
    ("beat_leaving_home", "Leaving home, with confirmation", "We're leaving the house. … Yes, go ahead."),
    ("beat_failure", "When a door does not respond", "(a door drops offline mid-plan)"),
    ("beat_question", "A question instead of a guess", "Get movie night ready. (the TV is offline)"),
]


def run_demo(dump_dir: Path | None = None) -> list[BeatResult]:
    """Run every beat and return the results. Never raises for a failed check."""

    rehearsal = Rehearsal(dump_dir)
    results: list[BeatResult] = []
    try:
        for number, (method, title, says) in enumerate(BEATS, start=1):
            result = BeatResult(number=number, title=title, says=says)
            started = time.perf_counter()
            try:
                getattr(rehearsal, method)()
            except Exception as exc:  # noqa: BLE001 - report, keep rehearsing
                result.error = f"{type(exc).__name__}: {exc}"
            result.checks = rehearsal.take()
            result.seconds = time.perf_counter() - started
            results.append(result)
    finally:
        reset_app_context()
    return results


def _print(results: Sequence[BeatResult], out: Callable[[str], object] = print) -> None:
    for beat in results:
        mark = "PASS" if beat.passed else "FAIL"
        out(f"{mark}  Beat {beat.number}: {beat.title}   [{beat.seconds:.2f}s]")
        out(f"      you say: {beat.says}")
        for check in beat.checks:
            tick = "ok " if check.passed else "XX "
            suffix = f"  ({check.detail})" if check.detail else ""
            out(f"      {tick}{check.description}{suffix}")
        if beat.error:
            out(f"      !! raised {beat.error}")
    failed = [beat for beat in results if not beat.passed]
    total = sum(len(beat.checks) for beat in results)
    out("")
    out(f"{len(results) - len(failed)} of {len(results)} beats passed ({total} checks).")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rehearse Haven's demo against the real tool functions.")
    parser.add_argument(
        "--dump-snapshots",
        type=Path,
        metavar="DIR",
        help="Also write the dashboard's JSON at each checkpoint into DIR.",
    )
    args = parser.parse_args(argv)

    results = run_demo(args.dump_snapshots)
    _print(results)
    return 0 if all(beat.passed for beat in results) else 1


if __name__ == "__main__":
    sys.exit(main())
