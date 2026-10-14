from __future__ import annotations

from haven.models.context import DeviceCapability, HouseholdContext
from haven.models.plan import ActionRisk
from haven.planning.constraints import (
    apply_constraints,
    filter_available_devices,
    filter_available_options,
    fits_available_time,
)
from haven.planning.models import ActionCandidate


def test_fits_available_time() -> None:
    assert not fits_available_time(145, 120)
    assert fits_available_time(95, 120)
    assert fits_available_time(500, None)


def test_filter_available_options_respects_time_and_availability(
    movie_night_context: HouseholdContext,
) -> None:
    options = filter_available_options(
        movie_night_context.media_options,
        available_minutes=movie_night_context.available_minutes,
    )
    assert [o.id for o in options] == ["m-short"]


def test_filter_available_devices_by_capability_and_room(
    movie_night_context: HouseholdContext,
) -> None:
    tvs = filter_available_devices(
        movie_night_context, capability=DeviceCapability.MEDIA_PLAYBACK, room_id="living-room"
    )
    assert [d.id for d in tvs] == ["tv-1"]
    assert filter_available_devices(movie_night_context, capability="nonexistent") == []


def test_apply_constraints_rejects_unavailable_device_and_missing_capability(
    movie_night_context: HouseholdContext,
) -> None:
    ok = ActionCandidate(
        action="a",
        title="t",
        description="d",
        risk=ActionRisk.READ,
        required_capabilities=[DeviceCapability.MEDIA_PLAYBACK],
        required_device_id="tv-1",
    )
    missing_device = ok.model_copy(update={"required_device_id": "ghost"})
    wrong_capability = ok.model_copy(update={"required_capabilities": [DeviceCapability.LOCK]})
    too_long = ActionCandidate(
        action="b", title="t", description="d", risk=ActionRisk.READ, duration_minutes=145
    )
    bad_media = ActionCandidate(
        action="c", title="t", description="d", risk=ActionRisk.READ, media_id="m-long"
    )

    result = apply_constraints(
        "goal", movie_night_context, [ok, missing_device, wrong_capability, too_long, bad_media]
    )

    assert result.accepted == [ok]
    assert len(result.rejected) == 4
    assert all(r.reason for r in result.rejected)


def test_apply_constraints_rejects_offline_device(movie_night_context: HouseholdContext) -> None:
    ctx = movie_night_context.model_copy(deep=True)
    ctx.devices[0].is_available = False
    candidate = ActionCandidate(
        action="a", title="t", description="d", risk=ActionRisk.READ, required_device_id="tv-1"
    )
    result = apply_constraints("goal", ctx, [candidate])
    assert result.accepted == []
    assert result.rejected[0].reason == "required device is unavailable"


def _candidate(**overrides: object) -> ActionCandidate:
    base = {"action": "a", "title": "t", "description": "d", "risk": ActionRisk.READ}
    return ActionCandidate(**{**base, **overrides})


def _only_reason(context: HouseholdContext, candidate: ActionCandidate) -> str | None:
    result = apply_constraints("goal", context, [candidate])
    if result.accepted:
        return None
    return result.rejected[0].reason


def test_required_device_missing_is_rejected(movie_night_context: HouseholdContext) -> None:
    reason = _only_reason(movie_night_context, _candidate(required_device_id="ghost"))
    assert reason == "required device is not present in household context"


def test_required_capability_missing_without_device_is_rejected(
    movie_night_context: HouseholdContext,
) -> None:
    # No specific device: the generic capability check must still run.
    reason = _only_reason(
        movie_night_context, _candidate(required_capabilities=[DeviceCapability.THERMOSTAT])
    )
    assert reason == "required capability is not available"


def test_required_room_missing_is_rejected(movie_night_context: HouseholdContext) -> None:
    reason = _only_reason(movie_night_context, _candidate(room_id="garage"))
    assert reason == "required room is not present in household context"


def test_required_room_missing_with_device_is_rejected(
    movie_night_context: HouseholdContext,
) -> None:
    # The device exists but its room is not part of the context. Previously the
    # room check was unreachable once a specific device passed.
    ctx = movie_night_context.model_copy(deep=True)
    ctx.devices[0].room_id = "garage"
    reason = _only_reason(ctx, _candidate(required_device_id="tv-1", room_id="garage"))
    assert reason == "required room is not present in household context"


def test_device_in_wrong_room_is_rejected(movie_night_context: HouseholdContext) -> None:
    reason = _only_reason(
        movie_night_context, _candidate(required_device_id="tv-1", room_id="bedroom")
    )
    assert reason == "required device is not in the required room"


def test_valid_candidate_is_accepted(movie_night_context: HouseholdContext) -> None:
    candidate = _candidate(
        required_device_id="tv-1",
        required_capabilities=[DeviceCapability.MEDIA_PLAYBACK],
        room_id="living-room",
        media_id="m-short",
        duration_minutes=95,
    )
    assert _only_reason(movie_night_context, candidate) is None
