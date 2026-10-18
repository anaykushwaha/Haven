"""Haven security simulator service.

Owns the simulated device store: every smart device (TV, lights,
thermostat, locks, the security system) lives in
`simulator/security/data/devices.json`, and this service is the single
in-memory copy of it for a run. `HouseholdSimulatorService` delegates its
device methods here rather than loading the file a second time, so the
household view and the security view can never disagree.

Like a real smart-home hub, the store only holds raw state and answers
"what is it now?". What a command *means* (locking a door, dimming a
light) and whether a device may receive it is the job of
`haven.integrations.home` / `haven.integrations.security`, which is what a
real Ring/Hue integration would replace.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_DEVICES_PATH = Path(__file__).parent / "data" / "devices.json"

LOCK = "lock"
SECURITY_SYSTEM = "security_system"


class SecuritySimulatorService:
    """In-memory device store, loaded from a local JSON fixture."""

    def __init__(self, devices_path: Path | str = DEFAULT_DEVICES_PATH) -> None:
        self._devices_path = Path(devices_path)
        self._devices: list[dict[str, Any]] = []

        self.reload()

    def reload(self) -> None:
        """Reload every device from disk, discarding in-memory changes."""

        if not self._devices_path.exists():
            raise FileNotFoundError(f"Simulator data file not found: {self._devices_path}")

        with self._devices_path.open("r", encoding="utf-8") as handle:
            self._devices = json.load(handle)

    # -- Devices -------------------------------------------------------------- #

    def get_devices(self) -> list[dict[str, Any]]:
        """Return a copy of every device record."""

        return copy.deepcopy(self._devices)

    def get_devices_by_type(self, device_type: str) -> list[dict[str, Any]]:
        """Return a copy of every device record of one kind."""

        return [d for d in self.get_devices() if d.get("device_type") == device_type]

    def get_device_state(self, device_id: str) -> dict[str, Any] | None:
        """Return a copy of one device record, or `None` if unknown."""

        for device in self._devices:
            if device.get("device_id") == device_id:
                return copy.deepcopy(device)

        return None

    def set_device_state(
        self,
        device_id: str,
        *,
        state: dict[str, Any] | None = None,
        online: bool | None = None,
    ) -> dict[str, Any]:
        """Update a device for the rest of this run and return its record.

        `state` is merged into the device's existing state (only the given
        keys change); `online` replaces its reachability flag when given.
        Raises `KeyError` for an unknown device.
        """

        for device in self._devices:
            if device.get("device_id") != device_id:
                continue

            if state:
                device.setdefault("state", {}).update(state)

            if online is not None:
                device["online"] = online

            return copy.deepcopy(device)

        raise KeyError(f"Unknown device_id: {device_id}")

    # -- Security summary ------------------------------------------------------ #

    def get_security_state(self) -> dict[str, Any]:
        """Summarise locks and security systems for dashboards and scripts."""

        locks = [
            {
                "device_id": d["device_id"],
                "name": d["name"],
                "locked": bool(d.get("state", {}).get("locked", False)),
                "online": bool(d.get("online", True)),
            }
            for d in self._devices
            if d.get("device_type") == LOCK
        ]
        alarms = [
            {
                "device_id": d["device_id"],
                "name": d["name"],
                "armed": bool(d.get("state", {}).get("armed", False)),
                "mode": d.get("state", {}).get("mode", "disarmed"),
                "online": bool(d.get("online", True)),
            }
            for d in self._devices
            if d.get("device_type") == SECURITY_SYSTEM
        ]

        return {
            "armed": any(alarm["armed"] for alarm in alarms),
            "all_locked": bool(locks) and all(lock["locked"] for lock in locks),
            "locks": locks,
            "alarms": alarms,
        }


__all__ = [
    "DEFAULT_DEVICES_PATH",
    "SecuritySimulatorService",
]
