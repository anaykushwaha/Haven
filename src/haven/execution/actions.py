"""Deterministic action handlers.

Each handler performs exactly one `PlanStep.action` against the household,
media, home or security backends. Execution never uses an LLM: everything
here is ordinary, testable code that either succeeds, fails, or is
skipped, and says which.

Handlers are looked up by the plan step's `action` string. An action the
planner has never proposed (or a typo) simply has no handler, and the
executor turns that into a `FAILED` step rather than raising — a plan
partially failing is normal, and it is the executor/verifier's job to
decide what to do about it, not an individual handler's.

Supported actions, by workflow:

    movie night       select_media, prepare_media_session, start_media_session,
                      dim_lights
    leaving home      turn_off_lights, stop_media, set_away_temperature,
                      lock_door, arm_security_system
    tonight planning  review_schedule, suggest_media, suggest_wind_down
                      (read-only: they record a suggestion, change nothing)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Protocol

from haven.execution.results import StepExecutionResult, StepExecutionStatus
from haven.integrations.errors import (
    DeviceNotFoundError,
    DeviceUnavailableError,
    UnsupportedDeviceError,
)
from haven.integrations.home import HomeService
from haven.integrations.media import MediaService
from haven.integrations.security import DEFAULT_ARM_MODE, SecurityService
from haven.models.plan import PlanStep

_QUOTED_TITLE = re.compile(r"'([^']+)'")

Effect = dict[str, str | int | float | bool | None]

# Error codes a handler reports in `StepExecutionResult.error`. They are the
# contract between execution and recovery, so they live together here.
ERR_UNSUPPORTED_ACTION = "unsupported action"
ERR_MISSING_DEVICE_ID = "missing device_id"
ERR_DEVICE_NOT_FOUND = "device not found"
ERR_DEVICE_UNAVAILABLE = "device unavailable"
ERR_UNSUPPORTED_DEVICE = "unsupported device type"
ERR_UNKNOWN_MEDIA = "unknown media title"
ERR_NO_TITLE = "no quoted title found in step text"

# Failures where the request itself cannot succeed as given (wrong id,
# missing target, unsupported action or device kind) — running the same
# step again will not change the outcome. A device being *offline* is
# deliberately absent: that can be transient, so it is worth a retry.
NON_RETRYABLE_ERRORS = frozenset(
    {
        ERR_UNSUPPORTED_ACTION,
        ERR_MISSING_DEVICE_ID,
        ERR_DEVICE_NOT_FOUND,
        ERR_UNSUPPORTED_DEVICE,
        ERR_UNKNOWN_MEDIA,
        ERR_NO_TITLE,
    }
)

# Actions that only record a choice or a suggestion. The verifier treats
# them as read-only: there is no household state to re-check afterwards.
READ_ONLY_ACTIONS = frozenset(
    {"select_media", "suggest_media", "review_schedule", "suggest_wind_down"}
)

# A comfortable, fixed "movie night" viewing level. A future preference
# (`PreferenceKey`-style) could make this per-household; hardcoding it
# keeps execution deterministic and simple.
VIEWING_BRIGHTNESS = 20


class HouseholdBackend(Protocol):
    """The subset of `HouseholdSimulatorService` action handlers depend on."""

    def get_device_state(self, device_id: str) -> dict | None: ...

    def set_device_state(
        self, device_id: str, *, state: dict | None = None, online: bool | None = None
    ) -> dict: ...


class ActionContext:
    """Bundles the backends an action handler needs to do its work.

    ``home`` and ``security`` default to services built over ``household``
    (the simulator), so callers that only have a household backend keep
    working; pass explicit services to substitute a different hub.
    """

    def __init__(
        self,
        household: HouseholdBackend,
        media: MediaService,
        *,
        home: HomeService | None = None,
        security: SecurityService | None = None,
    ) -> None:
        self.household = household
        self.media = media
        self.home = home if home is not None else HomeService(household)
        self.security = (
            security
            if security is not None
            else SecurityService(getattr(household, "security", household))
        )


ActionHandler = Callable[[PlanStep, ActionContext], StepExecutionResult]
"""Signature every action handler satisfies."""

DeviceOperation = Callable[[str], tuple[str, Effect]]
"""Does one device command and returns ``(detail, effect)``; may raise
a `DeviceNotFoundError`, `DeviceUnavailableError` or `UnsupportedDeviceError`."""


def extract_quoted_title(*texts: str | None) -> str | None:
    """Return the first single-quoted substring found in `texts`, if any.

    `PlanStep` does not carry a structured `media_id` (see
    `docs/friction-log.md`); the planner phrases a selected title in single
    quotes (`"Select 'Interstellar' (169 min)..."`), so this recovers it
    well enough. This is a deliberately narrow, documented bridge — not a
    general NLP parser.
    """

    for text in texts:
        if not text:
            continue
        match = _QUOTED_TITLE.search(text)
        if match:
            return match.group(1)
    return None


# --------------------------------------------------------------------------- #
# Result helpers
# --------------------------------------------------------------------------- #


def _succeeded(step: PlanStep, detail: str, effect: Effect | None = None) -> StepExecutionResult:
    return StepExecutionResult(
        step_id=step.id,
        action=step.action,
        status=StepExecutionStatus.SUCCEEDED,
        detail=detail,
        effect=effect or {},
    )


def _failed(step: PlanStep, detail: str, error: str) -> StepExecutionResult:
    return StepExecutionResult(
        step_id=step.id,
        action=step.action,
        status=StepExecutionStatus.FAILED,
        detail=detail,
        error=error,
    )


def _run_device_operation(step: PlanStep, operation: DeviceOperation) -> StepExecutionResult:
    """Run `operation` against the step's device, mapping failures to results."""

    if step.device_id is None:
        return _failed(step, "Step has no target device.", ERR_MISSING_DEVICE_ID)

    try:
        detail, effect = operation(step.device_id)
    except DeviceNotFoundError:
        return _failed(step, f"Unknown device: {step.device_id}.", ERR_DEVICE_NOT_FOUND)
    except DeviceUnavailableError:
        return _failed(
            step, f"{step.device_id} is offline and did not respond.", ERR_DEVICE_UNAVAILABLE
        )
    except UnsupportedDeviceError as exc:
        return _failed(
            step,
            f"{step.device_id} cannot do that: it is a {exc.actual}, not a {exc.expected}.",
            ERR_UNSUPPORTED_DEVICE,
        )

    return _succeeded(step, detail, effect)


def _confirm_media_title(step: PlanStep, context: ActionContext, detail: str) -> StepExecutionResult:
    """Resolve the media title a step names against the catalog."""

    title = extract_quoted_title(step.description, step.title)
    if title is None:
        return _failed(step, "Could not determine which media title was chosen.", ERR_NO_TITLE)

    media_id = context.media.find_media_id_by_title(title)
    if media_id is None:
        return _failed(step, f"'{title}' was not found in the media catalog.", ERR_UNKNOWN_MEDIA)

    return _succeeded(step, detail.format(title=title), {"media_id": media_id, "title": title})


# --------------------------------------------------------------------------- #
# Movie night
# --------------------------------------------------------------------------- #


def handle_select_media(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    # A read-only step: the planner has already chosen a title. There is
    # nothing to change in the world, only something to record.

    return _confirm_media_title(step, context, "Confirmed '{title}' as the selected media.")


def handle_prepare_media_session(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    if step.device_id is None:
        return _failed(step, "Step has no target device.", ERR_MISSING_DEVICE_ID)

    title = extract_quoted_title(step.description, step.title)
    if title is None:
        return _failed(step, "Could not determine which media title to prepare.", ERR_NO_TITLE)

    media_id = context.media.find_media_id_by_title(title)
    if media_id is None:
        return _failed(step, f"'{title}' was not found in the media catalog.", ERR_UNKNOWN_MEDIA)

    try:
        session = context.media.prepare_session(step.device_id, media_id)
    except KeyError as exc:
        return _failed(step, f"Could not prepare '{title}' on {step.device_id}.", str(exc))

    return _succeeded(
        step,
        f"Prepared '{session.title}' on {step.device_id}; not yet playing.",
        {
            "device_id": session.device_id,
            "media_id": session.media_id,
            "status": session.status.value,
        },
    )


def handle_start_media_session(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    # Not currently proposed by the planner, but supported so a future
    # explicit "start playback" confirmation step has somewhere to go.

    if step.device_id is None:
        return _failed(step, "Step has no target device.", ERR_MISSING_DEVICE_ID)

    try:
        session = context.media.start_session(step.device_id)
    except KeyError as exc:
        return _failed(step, f"No prepared session to start on {step.device_id}.", str(exc))

    return _succeeded(
        step,
        f"Started '{session.title}' on {step.device_id}.",
        {
            "device_id": session.device_id,
            "media_id": session.media_id,
            "status": session.status.value,
        },
    )


def handle_dim_lights(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    def operation(device_id: str) -> tuple[str, Effect]:
        status = context.home.set_light_brightness(device_id, VIEWING_BRIGHTNESS)
        brightness = status.state.get("brightness")
        return (
            f"Dimmed {device_id} to {brightness}% for viewing.",
            {"device_id": device_id, "brightness": brightness},
        )

    return _run_device_operation(step, operation)


# --------------------------------------------------------------------------- #
# Leaving home
# --------------------------------------------------------------------------- #


def handle_turn_off_lights(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    def operation(device_id: str) -> tuple[str, Effect]:
        before = context.home.get_device(device_id)
        after = context.home.turn_off_light(device_id)
        changed = before.state.get("power") != "off"
        detail = f"Turned off {after.name}." if changed else f"{after.name} was already off."
        return detail, {"device_id": device_id, "power": "off", "changed": changed}

    return _run_device_operation(step, operation)


def handle_stop_media(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    if step.device_id is None:
        return _failed(step, "Step has no target device.", ERR_MISSING_DEVICE_ID)

    stopped = context.media.stop_session(step.device_id)
    if stopped is None:
        return _succeeded(
            step,
            f"Nothing was playing on {step.device_id}.",
            {"device_id": step.device_id, "was_active": False},
        )

    return _succeeded(
        step,
        f"Stopped '{stopped.title}' on {step.device_id}.",
        {"device_id": step.device_id, "was_active": True, "media_id": stopped.media_id},
    )


def handle_set_away_temperature(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    def operation(device_id: str) -> tuple[str, Effect]:
        status = context.home.set_thermostat_away(device_id)
        target = status.state.get("target_celsius")
        return (
            f"Set {status.name} to away ({target}°C).",
            {"device_id": device_id, "mode": "away", "target_celsius": target},
        )

    return _run_device_operation(step, operation)


def handle_lock_door(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    def operation(device_id: str) -> tuple[str, Effect]:
        before = context.security.get_lock_status(device_id)
        after = context.security.lock_door(device_id)
        detail = f"Locked {after.name}." if not before.locked else f"{after.name} was already locked."
        return detail, {"device_id": device_id, "locked": True, "changed": not before.locked}

    return _run_device_operation(step, operation)


def handle_arm_security_system(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    def operation(device_id: str) -> tuple[str, Effect]:
        before = context.security.get_alarm_status(device_id)
        after = context.security.arm_system(device_id, DEFAULT_ARM_MODE)
        changed = not (before.armed and before.mode == after.mode)
        detail = (
            f"Armed {after.name} in {after.mode} mode."
            if changed
            else f"{after.name} was already armed in {after.mode} mode."
        )
        return detail, {"device_id": device_id, "armed": True, "mode": after.mode, "changed": changed}

    return _run_device_operation(step, operation)


# --------------------------------------------------------------------------- #
# Tonight planning (suggestions only: nothing in the household changes)
# --------------------------------------------------------------------------- #


def handle_review_schedule(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    return _succeeded(step, step.description)


def handle_suggest_media(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    return _confirm_media_title(
        step, context, "Suggested '{title}'; nothing was queued or played."
    )


def handle_suggest_wind_down(step: PlanStep, context: ActionContext) -> StepExecutionResult:
    return _succeeded(step, step.description)


_HANDLERS: dict[str, ActionHandler] = {
    "select_media": handle_select_media,
    "prepare_media_session": handle_prepare_media_session,
    "start_media_session": handle_start_media_session,
    "dim_lights": handle_dim_lights,
    "turn_off_lights": handle_turn_off_lights,
    "stop_media": handle_stop_media,
    "set_away_temperature": handle_set_away_temperature,
    "lock_door": handle_lock_door,
    "arm_security_system": handle_arm_security_system,
    "review_schedule": handle_review_schedule,
    "suggest_media": handle_suggest_media,
    "suggest_wind_down": handle_suggest_wind_down,
}


def get_handler(action: str) -> ActionHandler | None:
    """Return the handler for `action`, or `None` if it is unsupported."""

    return _HANDLERS.get(action)


def supported_actions() -> frozenset[str]:
    """Every action name the executor can run."""

    return frozenset(_HANDLERS)


__all__ = [
    "ActionContext",
    "ERR_DEVICE_NOT_FOUND",
    "ERR_DEVICE_UNAVAILABLE",
    "ERR_MISSING_DEVICE_ID",
    "ERR_NO_TITLE",
    "ERR_UNKNOWN_MEDIA",
    "ERR_UNSUPPORTED_ACTION",
    "ERR_UNSUPPORTED_DEVICE",
    "HouseholdBackend",
    "NON_RETRYABLE_ERRORS",
    "READ_ONLY_ACTIONS",
    "VIEWING_BRIGHTNESS",
    "extract_quoted_title",
    "get_handler",
    "handle_arm_security_system",
    "handle_dim_lights",
    "handle_lock_door",
    "handle_prepare_media_session",
    "handle_review_schedule",
    "handle_select_media",
    "handle_set_away_temperature",
    "handle_start_media_session",
    "handle_stop_media",
    "handle_suggest_media",
    "handle_suggest_wind_down",
    "handle_turn_off_lights",
    "supported_actions",
]
