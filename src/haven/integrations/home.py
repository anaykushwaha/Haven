"""Home integration: lights, thermostat, occupancy and generic device state.

Phase 5 is backed by the household simulator; no real hub (Hue, Nest,
SmartThings, ...) is called. `HomeService` owns the *semantics* of a home
command — what "turn off", "dim" or "set to away" mean in terms of device
state, and which devices may receive them — while the backend only stores
raw device state. That split is what lets a real hub replace the simulator
later without touching the executor or verifier.

Every command validates first: the device must exist
(`DeviceNotFoundError`), be the right kind (`UnsupportedDeviceError`) and
be online (`DeviceUnavailableError`). Commands are idempotent — turning off
a light that is already off succeeds and leaves it off.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from haven.integrations.errors import (
    DeviceNotFoundError,
    DeviceUnavailableError,
    UnsupportedDeviceError,
)

LIGHT = "light"
THERMOSTAT = "thermostat"

DEFAULT_HOME_CELSIUS = 21.0
AWAY_CELSIUS = 16.0


class DeviceStatus(BaseModel):
    """A device as the home integration reports it."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    name: str
    device_type: str
    room_id: str | None = None
    online: bool = True
    state: dict[str, Any] = Field(default_factory=dict)


class HomeBackend(Protocol):
    """The subset of `HouseholdSimulatorService` this integration needs."""

    def get_devices(self) -> list[dict]: ...

    def get_device_state(self, device_id: str) -> dict | None: ...

    def set_device_state(
        self, device_id: str, *, state: dict | None = None, online: bool | None = None
    ) -> dict: ...

    def get_room_state(self, room_id: str) -> dict | None: ...


def to_device_status(record: dict) -> DeviceStatus:
    """Map a raw backend device record onto `DeviceStatus`."""

    return DeviceStatus(
        device_id=record["device_id"],
        name=record["name"],
        device_type=str(record.get("device_type", "other")),
        room_id=record.get("room_id"),
        online=bool(record.get("online", True)),
        state=dict(record.get("state", {})),
    )


class HomeService:
    """Application-facing facade over a home backend (simulator today)."""

    def __init__(self, backend: HomeBackend) -> None:
        self._backend = backend

    # -- Reading ------------------------------------------------------------ #

    def get_device(self, device_id: str) -> DeviceStatus:
        """Return one device's current status."""

        return to_device_status(self._record(device_id))

    def list_devices(self, device_type: str | None = None) -> list[DeviceStatus]:
        """Return every device, optionally only one kind."""

        statuses = [to_device_status(record) for record in self._backend.get_devices()]
        if device_type is None:
            return statuses
        return [status for status in statuses if status.device_type == device_type]

    def room_occupied(self, room_id: str) -> bool | None:
        """Return a room's occupancy fact, or `None` if the room is unknown.

        Occupancy is a low-resolution sensor fact (never camera data) and
        is temporary context: it must not be written to long-term memory.
        """

        room = self._backend.get_room_state(room_id)
        if room is None:
            return None
        return bool(room.get("occupied", False))

    # -- Lights ------------------------------------------------------------- #

    def turn_on_light(self, device_id: str, brightness: int | None = None) -> DeviceStatus:
        """Switch a light on, at `brightness` (0-100) or its last level."""

        record = self._command(device_id, LIGHT)
        if brightness is None:
            current = record.get("state", {}).get("brightness", 0)
            level = int(current) if int(current) > 0 else 100
        else:
            level = _validate_brightness(brightness)
        return self._update(device_id, {"power": "on", "brightness": level})

    def turn_off_light(self, device_id: str) -> DeviceStatus:
        """Switch a light off. Succeeds if it is already off."""

        self._command(device_id, LIGHT)
        return self._update(device_id, {"power": "off", "brightness": 0})

    def set_light_brightness(self, device_id: str, brightness: int) -> DeviceStatus:
        """Set a light to `brightness` (0-100); 0 switches it off."""

        level = _validate_brightness(brightness)
        self._command(device_id, LIGHT)
        return self._update(device_id, {"power": "on" if level > 0 else "off", "brightness": level})

    # -- Thermostat --------------------------------------------------------- #

    def set_thermostat(self, device_id: str, target_celsius: float) -> DeviceStatus:
        """Set the thermostat's normal (home) target temperature."""

        self._command(device_id, THERMOSTAT)
        return self._update(
            device_id,
            {
                "mode": "heat",
                "away": False,
                "target_celsius": float(target_celsius),
                "home_target_celsius": float(target_celsius),
            },
        )

    def set_thermostat_away(
        self, device_id: str, away_celsius: float = AWAY_CELSIUS
    ) -> DeviceStatus:
        """Put the thermostat into its energy-saving away mode.

        The temperature it was holding is remembered so
        `restore_thermostat` can bring it back. Calling this twice keeps
        the original home temperature rather than overwriting it with the
        away one.
        """

        record = self._command(device_id, THERMOSTAT)
        state = record.get("state", {})
        if state.get("away"):
            home_target = state.get("home_target_celsius", DEFAULT_HOME_CELSIUS)
        else:
            home_target = state.get("target_celsius", DEFAULT_HOME_CELSIUS)
        return self._update(
            device_id,
            {
                "mode": "away",
                "away": True,
                "target_celsius": float(away_celsius),
                "home_target_celsius": float(home_target),
            },
        )

    def restore_thermostat(self, device_id: str) -> DeviceStatus:
        """Leave away mode and return to the remembered home temperature."""

        record = self._command(device_id, THERMOSTAT)
        home_target = record.get("state", {}).get("home_target_celsius", DEFAULT_HOME_CELSIUS)
        return self.set_thermostat(device_id, home_target)

    # -- Internals ---------------------------------------------------------- #

    def _record(self, device_id: str) -> dict:
        record = self._backend.get_device_state(device_id)
        if record is None:
            raise DeviceNotFoundError(device_id)
        return record

    def _command(self, device_id: str, expected_type: str) -> dict:
        """Validate that `device_id` can take a command of `expected_type`."""

        record = self._record(device_id)
        actual = str(record.get("device_type", "other"))
        if actual != expected_type:
            raise UnsupportedDeviceError(device_id, expected_type, actual)
        if not record.get("online", True):
            raise DeviceUnavailableError(device_id)
        return record

    def _update(self, device_id: str, state: dict[str, Any]) -> DeviceStatus:
        return to_device_status(self._backend.set_device_state(device_id, state=state))


def _validate_brightness(brightness: int) -> int:
    if isinstance(brightness, bool) or not isinstance(brightness, int):
        raise ValueError("brightness must be an integer between 0 and 100")
    if not 0 <= brightness <= 100:
        raise ValueError("brightness must be between 0 and 100")
    return brightness


__all__ = [
    "AWAY_CELSIUS",
    "DEFAULT_HOME_CELSIUS",
    "DeviceStatus",
    "HomeBackend",
    "HomeService",
    "to_device_status",
]
