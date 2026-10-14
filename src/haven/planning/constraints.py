"""Deterministic planning constraints.

If ordinary code can reliably make the decision, do not ask an LLM to
reason about it. This layer only uses fields that already exist on
`HouseholdContext`. Constraints that need richer context should be added
later when those fields exist; they are not simulated here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from haven.models.context import (
    DeviceContext,
    HouseholdContext,
    MediaItem,
    PersonContext,
    PresenceState,
)
from haven.planning.models import ActionCandidate, ConstraintResult, RejectedCandidate

logger = logging.getLogger(__name__)


def fits_available_time(duration_minutes: int, available_minutes: int | None) -> bool:
    """Return True when a duration fits the household's remaining time.

    If available time is unknown, the constraint cannot be evaluated and
    the option is not rejected on time grounds.
    """

    if available_minutes is None:
        return True
    return duration_minutes <= available_minutes


def filter_available_devices(
    context: HouseholdContext,
    *,
    capability: str | None = None,
    room_id: str | None = None,
) -> list[DeviceContext]:
    """Return devices that are currently available, optionally by capability/room."""

    devices: list[DeviceContext] = []
    for device in context.devices:
        if not device.is_available:
            continue
        if capability is not None and capability not in device.capabilities:
            continue
        if room_id is not None and device.room_id != room_id:
            continue
        devices.append(device)
    return devices


def filter_available_options(
    options: Sequence[MediaItem],
    *,
    available_minutes: int | None = None,
) -> list[MediaItem]:
    """Return media that is available and fits remaining time when known."""

    accepted: list[MediaItem] = []
    for option in options:
        if not option.is_available:
            continue
        if not fits_available_time(option.duration_minutes, available_minutes):
            continue
        accepted.append(option)
    return accepted


def context_has_capability(context: HouseholdContext, capability: str) -> bool:
    """Return True if an available device currently exposes `capability`."""

    return any(
        device.is_available and capability in device.capabilities for device in context.devices
    )


def people_present(context: HouseholdContext) -> list[PersonContext]:
    """Return people whose presence is currently known to be PRESENT.

    Presence is temporary context: it may shape *this* plan but must never be
    written to memory (see `haven.memory.service.TRANSIENT_KEYS`).
    """

    return [person for person in context.people if person.presence is PresenceState.PRESENT]


def presence_is_known(context: HouseholdContext) -> bool:
    """Return True when at least one person has a known presence state."""

    return any(person.presence is not PresenceState.UNKNOWN for person in context.people)


def longest_fitting_option(
    options: Sequence[MediaItem],
    *,
    available_minutes: int | None = None,
) -> MediaItem | None:
    """Return the longest available option that still fits the time, if any.

    Used for evening suggestions, where filling the window well is the goal.
    Ties keep catalogue order so the result is stable.
    """

    fitting = filter_available_options(options, available_minutes=available_minutes)
    if not fitting:
        return None
    return max(fitting, key=lambda item: item.duration_minutes)


def is_short_window(available_minutes: int | None, *, threshold_minutes: int = 30) -> bool:
    """Return True when the known remaining time is too short for a feature."""

    return available_minutes is not None and available_minutes < threshold_minutes


def apply_constraints(
    goal: str,
    context: HouseholdContext,
    candidates: Sequence[ActionCandidate],
) -> ConstraintResult:
    """Filter candidates that ordinary deterministic checks can reject.

    `goal` is accepted for a stable interface; Phase 1 constraints are
    driven by context and candidate requirements, not by NLP.
    """

    del goal  # Goal-specific reasoning belongs in the planner, not here.

    available_media_ids = {
        item.id
        for item in filter_available_options(
            context.media_options,
            available_minutes=context.available_minutes,
        )
    }
    devices_by_id = {device.id: device for device in context.devices}

    accepted: list[ActionCandidate] = []
    rejected: list[RejectedCandidate] = []

    for candidate in candidates:
        reason = _rejection_reason(
            candidate,
            context=context,
            devices_by_id=devices_by_id,
            available_media_ids=available_media_ids,
        )
        if reason is None:
            accepted.append(candidate)
        else:
            rejected.append(RejectedCandidate(candidate=candidate, reason=reason))

    logger.info(
        "Applied planning constraints: accepted=%s rejected=%s",
        len(accepted),
        len(rejected),
    )
    return ConstraintResult(accepted=accepted, rejected=rejected)


def _rejection_reason(
    candidate: ActionCandidate,
    *,
    context: HouseholdContext,
    devices_by_id: dict[str, DeviceContext],
    available_media_ids: set[str],
) -> str | None:
    """Return why a candidate is rejected, or None when it is accepted.

    Checks run in a fixed order: time, media, specific device, capability,
    room. The first failing check decides the reason.
    """

    if candidate.duration_minutes is not None and not fits_available_time(
        candidate.duration_minutes,
        context.available_minutes,
    ):
        return "does not fit available time"

    if candidate.media_id is not None and candidate.media_id not in available_media_ids:
        return "required media option is unavailable or does not fit available time"

    if candidate.required_device_id is not None:
        device = devices_by_id.get(candidate.required_device_id)

        if device is None:
            return "required device is not present in household context"

        if not device.is_available:
            return "required device is unavailable"

        missing = [
            capability
            for capability in candidate.required_capabilities
            if capability not in device.capabilities
        ]

        if missing:
            return "required device is missing a needed capability"

        if candidate.room_id is not None and device.room_id != candidate.room_id:
            return "required device is not in the required room"

    for capability in candidate.required_capabilities:
        if not context_has_capability(context, capability):
            return "required capability is not available"

    if candidate.room_id is not None and all(
        room.id != candidate.room_id for room in context.rooms
    ):
        return "required room is not present in household context"

    return None
