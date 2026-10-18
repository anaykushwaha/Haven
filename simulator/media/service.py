# service.py
# Haven Media Simulator Service

# Exposes a small media catalog and simulated playback sessions, backed
# by the JSON demo data in simulator/media/data/media.json. This lets
# the planner and executor be developed and demoed against "Fire TV"
# style playback without any real device or streaming integration.

# A "session" here just means: which media_id is queued or playing on
# which device_id. Sessions live in memory only, for the current run.

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_DATA_PATH = Path(__file__).parent / "data" / "media.json"


class SessionStatus(StrEnum):
    # Lifecycle of a simulated playback session

    READY = "ready"
    PLAYING = "playing"
    STOPPED = "stopped"


class MediaSimulatorService:
    # In-memory media catalog + simulated playback sessions

    def __init__(self, data_path: Path | str = DEFAULT_DATA_PATH) -> None:
        self._data_path = Path(data_path)
        self._catalog: list[dict[str, Any]] = []
        self._sessions: dict[str, dict[str, Any]] = {}

        self.reload()

    def reload(self) -> None:
        # Reloads the media catalog from disk. Active sessions are kept,
        # since they represent in-progress simulator state, not catalog
        # data.

        if not self._data_path.exists():
            raise FileNotFoundError(f"Media catalog not found: {self._data_path}")

        with self._data_path.open("r", encoding="utf-8") as handle:
            self._catalog = json.load(handle)

    # -- Catalog -----------------------------------------------------------

    def list_media(self) -> list[dict[str, Any]]:
        # Returns the full media catalog

        return [dict(item) for item in self._catalog]

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        # Returns a single catalog entry by id, if present

        for item in self._catalog:
            if item.get("media_id") == media_id:
                return dict(item)

        return None

    def get_duration_minutes(self, media_id: str) -> int | None:
        # Returns a title's duration in minutes, if known

        item = self.get_media(media_id)

        return None if item is None else item.get("duration_minutes")

    def search(
        self,
        query: str | None = None,
        genre: str | None = None,
        max_duration_minutes: int | None = None,
    ) -> list[dict[str, Any]]:
        # Returns catalog entries matching the given, optional filters

        results = self.list_media()

        if query:
            lowered = query.strip().lower()
            results = [item for item in results if lowered in item.get("title", "").lower()]

        if genre:
            lowered_genre = genre.strip().lower()
            results = [
                item
                for item in results
                if lowered_genre in [g.lower() for g in item.get("genres", [])]
            ]

        if max_duration_minutes is not None:
            results = [
                item
                for item in results
                if item.get("duration_minutes", 0) <= max_duration_minutes
            ]

        return results

    # -- Playback sessions ---------------------------------------------------

    def prepare_session(self, device_id: str, media_id: str) -> dict[str, Any]:
        # Queues a title on a device without starting playback. Raises
        # KeyError if the title is not in the catalog.

        media = self.get_media(media_id)
        if media is None:
            raise KeyError(f"Unknown media_id: {media_id}")

        session = {
            "device_id": device_id,
            "media_id": media_id,
            "title": media.get("title"),
            "duration_minutes": media.get("duration_minutes"),
            "status": SessionStatus.READY.value,
            "updated_at": _now_iso(),
        }
        self._sessions[device_id] = session

        return dict(session)

    def start_session(self, device_id: str) -> dict[str, Any]:
        # Starts playback for a previously prepared session. Raises
        # KeyError if no session has been prepared on this device.

        session = self._sessions.get(device_id)
        if session is None:
            raise KeyError(f"No prepared session for device_id: {device_id}")

        session["status"] = SessionStatus.PLAYING.value
        session["updated_at"] = _now_iso()

        return dict(session)

    def stop_session(self, device_id: str) -> dict[str, Any] | None:
        # Stops and clears a session on a device, if one exists

        session = self._sessions.pop(device_id, None)
        if session is None:
            return None

        session["status"] = SessionStatus.STOPPED.value
        session["updated_at"] = _now_iso()

        return session

    def get_session_state(self, device_id: str) -> dict[str, Any] | None:
        # Returns a device's current session, if any

        session = self._sessions.get(device_id)

        return None if session is None else dict(session)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "MediaSimulatorService",
    "SessionStatus",
]
