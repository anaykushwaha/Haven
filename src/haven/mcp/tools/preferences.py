"""MCP tool: `save_household_preference`.

Thin wrapper over `haven.memory.service.MemoryService.save_preference`.
Transient state (presence, device status, lock state, ...) is rejected
there, not here — see `haven.memory.preferences.TRANSIENT_KEYS` — so a
client can't accidentally turn a passing observation into durable memory
by calling the wrong tool.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context

from haven.mcp.schemas import PreferenceSummary, preference_to_summary
from haven.memory.preferences import PreferenceValue


def save_household_preference(
    app,
    key: str,
    value: PreferenceValue,
    *,
    person_id: str | None = None,
) -> PreferenceSummary:
    """Save a durable household (or person-scoped) preference.

    Raises `ValueError` for an empty key or a key describing transient
    state (see `haven.memory.preferences.is_durable_preference_key`).
    """

    household_id = app.household.get_snapshot().household_id
    preference = app.memory.save_preference(household_id, key, value, person_id=person_id)
    return preference_to_summary(preference)


def register(mcp) -> None:
    from haven.mcp.server import get_app

    @mcp.tool()
    def save_household_preference_tool(
        key: str,
        value: str | int | float | bool | list[str],
        ctx: Context,
        person_id: str | None = None,
    ) -> PreferenceSummary:
        """Save a durable household preference, such as preferred movie
        genres, a preferred room for media, temperature, a preferred
        media device, or quiet hours. Pass person_id to scope the
        preference to one household member instead of the whole
        household. Do not use this for anything temporary (who's home
        right now, whether a device is on) — Haven rejects that.
        """

        return save_household_preference(get_app(ctx), key, value, person_id=person_id)


__all__ = ["save_household_preference", "register"]
