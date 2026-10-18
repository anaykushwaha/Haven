"""Security integration: doors, locks and the security system.

Phase 5 is backed by the household simulator; no real Ring/Yale/August
API is called. As with `haven.integrations.home`, `SecurityService` owns
the semantics (what "lock" and "arm" mean in device state, and which
devices accept them) and the backend only stores raw state.

Locking and arming are `HIGH_IMPACT_ACTION`s in Haven's policy: the
planner marks them as requiring confirmation and the executor refuses to
run them unconfirmed. This module deliberately does *not* re-check that —
it is the mechanism, not the policy — so it must only ever be reached
through the executor.

Commands are idempotent (locking a locked door succeeds) and validate the
device first: it must exist (`DeviceNotFoundError`), be the right kind
(`UnsupportedDeviceError`) and be online (`DeviceUnavailableError`).
Reading status never requires the device to be online.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from haven.integrations.errors import (
    DeviceNotFoundError,
    DeviceUnavailableError,
    UnsupportedDeviceError,
)

LOCK = "lock"
SECURITY_SYSTEM = "security_system"

ARM_MODES = ("away", "home")
DEFAULT_ARM_MODE = "away"


class LockStatus(BaseModel):
    """One lock's current state."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    name: str
    locked: bool
    online: bool = True


class AlarmStatus(BaseModel):
    """One security system's current state."""

    model_config = ConfigDict(extra="forbid")

    device_id: str
    name: str
    armed: bool
    mode: str = "disarmed"
    online: bool = True


class SecurityStatus(BaseModel):
    """The whole household's security picture."""

    model_config = ConfigDict(extra="forbid")

    armed: bool
    all_locked: bool
    alarms: list[AlarmStatus] = Field(default_factory=list)
    locks: list[LockStatus] = Field(default_factory=list)
    unlocked_device_ids: list[str] = Field(default_factory=list)


class SecurityBackend(Protocol):
    """The subset of the device store this integration needs."""

    def get_devices(self) -> list[dict]: ...

    def get_device_state(self, device_id: str) -> dict | None: ...

    def set_device_state(
        self, device_id: str, *, state: dict | None = None, online: bool | None = None
    ) -> dict: ...


class SecurityService:
    """Application-facing facade over a security backend (simulator today)."""

    def __init__(self, backend: SecurityBackend) -> None:
        self._backend = backend

    # -- Reading ------------------------------------------------------------ #

    def get_lock_status(self, device_id: str) -> LockStatus:
        """Return one lock's state."""

        return _to_lock(self._record(device_id, LOCK))

    def get_alarm_status(self, device_id: str) -> AlarmStatus:
        """Return one security system's state."""

        return _to_alarm(self._record(device_id, SECURITY_SYSTEM))

    def get_security_status(self) -> SecurityStatus:
        """Summarise every lock and security system in the household."""

        records = self._backend.get_devices()
        locks = [_to_lock(r) for r in records if r.get("device_type") == LOCK]
        alarms = [_to_alarm(r) for r in records if r.get("device_type") == SECURITY_SYSTEM]
        unlocked = [lock.device_id for lock in locks if not lock.locked]
        return SecurityStatus(
            armed=any(alarm.armed for alarm in alarms),
            all_locked=bool(locks) and not unlocked,
            alarms=alarms,
            locks=locks,
            unlocked_device_ids=unlocked,
        )

    # -- Commands ----------------------------------------------------------- #

    def lock_door(self, device_id: str) -> LockStatus:
        """Lock a door. Succeeds if it is already locked."""

        self._command(device_id, LOCK)
        return _to_lock(self._backend.set_device_state(device_id, state={"locked": True}))

    def unlock_door(self, device_id: str) -> LockStatus:
        """Unlock a door. Succeeds if it is already unlocked."""

        self._command(device_id, LOCK)
        return _to_lock(self._backend.set_device_state(device_id, state={"locked": False}))

    def arm_system(self, device_id: str, mode: str = DEFAULT_ARM_MODE) -> AlarmStatus:
        """Arm a security system in `mode` ("away" or "home")."""

        if mode not in ARM_MODES:
            raise ValueError(f"mode must be one of {ARM_MODES}, got {mode!r}")
        self._command(device_id, SECURITY_SYSTEM)
        return _to_alarm(
            self._backend.set_device_state(device_id, state={"armed": True, "mode": mode})
        )

    def disarm_system(self, device_id: str) -> AlarmStatus:
        """Disarm a security system. Succeeds if it is already disarmed."""

        self._command(device_id, SECURITY_SYSTEM)
        return _to_alarm(
            self._backend.set_device_state(device_id, state={"armed": False, "mode": "disarmed"})
        )

    # -- Internals ---------------------------------------------------------- #

    def _record(self, device_id: str, expected_type: str) -> dict:
        record = self._backend.get_device_state(device_id)
        if record is None:
            raise DeviceNotFoundError(device_id)
        actual = str(record.get("device_type", "other"))
        if actual != expected_type:
            raise UnsupportedDeviceError(device_id, expected_type, actual)
        return record

    def _command(self, device_id: str, expected_type: str) -> dict:
        record = self._record(device_id, expected_type)
        if not record.get("online", True):
            raise DeviceUnavailableError(device_id)
        return record


def _to_lock(record: dict[str, Any]) -> LockStatus:
    return LockStatus(
        device_id=record["device_id"],
        name=record["name"],
        locked=bool(record.get("state", {}).get("locked", False)),
        online=bool(record.get("online", True)),
    )


def _to_alarm(record: dict[str, Any]) -> AlarmStatus:
    state = record.get("state", {})
    armed = bool(state.get("armed", False))
    return AlarmStatus(
        device_id=record["device_id"],
        name=record["name"],
        armed=armed,
        mode=str(state.get("mode", DEFAULT_ARM_MODE if armed else "disarmed")),
        online=bool(record.get("online", True)),
    )


__all__ = [
    "ARM_MODES",
    "AlarmStatus",
    "DEFAULT_ARM_MODE",
    "LockStatus",
    "SecurityBackend",
    "SecurityService",
    "SecurityStatus",
]
