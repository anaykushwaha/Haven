from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from haven.integrations.calendar import CalendarEvent, CalendarService, demo_calendar

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)


def _event(event_id: str, start_offset: timedelta, minutes: int = 30) -> CalendarEvent:
    start = NOW + start_offset
    return CalendarEvent(
        id=event_id, title=event_id, start=start, end=start + timedelta(minutes=minutes)
    )


def _service(*events: CalendarEvent) -> CalendarService:
    return CalendarService(events, clock=lambda: NOW)


def test_next_event_and_available_minutes() -> None:
    service = _service(_event("later", timedelta(hours=3)), _event("soon", timedelta(minutes=95)))
    assert service.next_event().id == "soon"
    assert service.available_minutes() == 95


def test_past_events_are_ignored() -> None:
    service = _service(_event("done", timedelta(hours=-2)))
    assert service.next_event() is None
    assert service.available_minutes() is None


def test_no_upcoming_event() -> None:
    service = _service()
    assert service.next_event() is None
    assert service.available_minutes() is None
    assert service.lookup_fact("available_time_minutes") is None
    assert service.lookup_fact("next_event_time") is None


def test_event_in_progress_means_no_available_time() -> None:
    service = _service(_event("now", timedelta(minutes=-10), minutes=60))
    assert service.current_event().id == "now"
    assert service.available_minutes() == 0


def test_explicit_now_and_other_timezone() -> None:
    service = _service(_event("soon", timedelta(minutes=90)))
    pacific = timezone(timedelta(hours=-7))
    later = (NOW + timedelta(minutes=30)).astimezone(pacific)
    assert service.available_minutes(later) == 60


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CalendarEvent(id="x", title="x", start=datetime(2026, 1, 1), end=datetime(2026, 1, 1, 1))
    service = _service()
    with pytest.raises(ValueError, match="timezone-aware"):
        service.available_minutes(datetime(2026, 1, 1))


def test_event_end_must_follow_start() -> None:
    with pytest.raises(ValueError, match="after start"):
        CalendarEvent(id="x", title="x", start=NOW, end=NOW)


def test_events_between() -> None:
    a = _event("a", timedelta(hours=1))
    b = _event("b", timedelta(hours=5))
    service = _service(a, b)
    assert service.events_between(NOW, NOW + timedelta(hours=2)) == [a]


# --- Person B ConnectedServiceSource callback shape -------------------------- #


def test_lookup_fact_supports_connected_service_facts() -> None:
    service = _service(_event("soon", timedelta(minutes=45)))
    assert service.lookup_fact("available_time_minutes") == 45
    assert service.lookup_fact("next_event_time") == NOW + timedelta(minutes=45)
    assert service.lookup_fact("next_event_time").tzinfo is UTC
    assert service.lookup_fact("unsupported_fact") is None
    # Extra kwargs from FactRequest.params must be tolerated.
    assert service.lookup_fact("available_time_minutes", room_id="living-room") == 45


def test_demo_calendar_is_deterministic() -> None:
    service = demo_calendar(NOW)
    assert service.available_minutes() == 120
    assert service.next_event().title == "Family video call"
    assert len(service.events()) == 2
