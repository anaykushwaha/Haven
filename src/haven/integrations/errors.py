"""Errors raised by Haven's device integrations (home and security).

Integrations raise these instead of vendor-specific exceptions so the
executor can map a failure to a stable, human-meaningful error code
without knowing which backend (simulator today, a real hub later) failed.
"""

from __future__ import annotations


class IntegrationError(Exception):
    """Base class for every integration-level failure."""


class DeviceNotFoundError(IntegrationError):
    """The backend does not know a device with the requested id."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        super().__init__(f"Unknown device: {device_id}")


class DeviceUnavailableError(IntegrationError):
    """The device exists but is offline, so it cannot take a command."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        super().__init__(f"Device is offline: {device_id}")


class UnsupportedDeviceError(IntegrationError):
    """The device exists but is the wrong kind for the requested command."""

    def __init__(self, device_id: str, expected: str, actual: str) -> None:
        self.device_id = device_id
        self.expected = expected
        self.actual = actual
        super().__init__(f"Device {device_id} is a {actual}, not a {expected}")


__all__ = [
    "IntegrationError",
    "DeviceNotFoundError",
    "DeviceUnavailableError",
    "UnsupportedDeviceError",
]
