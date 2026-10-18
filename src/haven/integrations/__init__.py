"""External-service integrations behind stable Haven interfaces."""

from haven.integrations.calendar import CalendarEvent, CalendarService, demo_calendar
from haven.integrations.errors import (
    DeviceNotFoundError,
    DeviceUnavailableError,
    IntegrationError,
    UnsupportedDeviceError,
)
from haven.integrations.home import DeviceStatus, HomeService
from haven.integrations.media import MediaBackend, MediaService, MediaSession, MediaSessionStatus
from haven.integrations.security import AlarmStatus, LockStatus, SecurityService, SecurityStatus

__all__ = [
    "AlarmStatus",
    "CalendarEvent",
    "CalendarService",
    "DeviceNotFoundError",
    "DeviceStatus",
    "DeviceUnavailableError",
    "HomeService",
    "IntegrationError",
    "LockStatus",
    "MediaBackend",
    "MediaService",
    "MediaSession",
    "MediaSessionStatus",
    "SecurityService",
    "SecurityStatus",
    "UnsupportedDeviceError",
    "demo_calendar",
]
