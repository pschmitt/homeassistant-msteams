"""Unit tests for TeamsDataUpdateCoordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.msteams.const import (
    EVENT_MEETING_ENDED,
    EVENT_MEETING_REMINDER,
    EVENT_MEETING_STARTED,
)
from custom_components.msteams.coordinator import TeamsDataUpdateCoordinator


@pytest.mark.asyncio
async def test_coordinator_update_data_and_events():
    """Test coordinator data fetch, active meeting detection, and event firing."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry123"
    entry.title = "Alice"
    entry.options = {}
    entry.data = {}

    client = MagicMock()
    client.browser_daemon_url = None
    client.auth = MagicMock()
    client.auth.user_mri = "8:orgid:alice"
    client.auth.user_display_name = "Alice"
    client.auth.user_email = "alice@example.com"

    # Presence
    client.get_presence = AsyncMock(
        return_value={"availability": "Busy", "activity": "InAMeeting", "status_message": ""}
    )

    # Calendar meetings
    now = datetime.now(UTC)
    meeting_now = {
        "id": "meeting-1",
        "subject": "Sprint Review",
        "start_time": (now - timedelta(minutes=5)).isoformat(),
        "end_time": (now + timedelta(minutes=25)).isoformat(),
        "join_url": "https://teams.microsoft.com/meet/123",
        "is_online": True,
    }
    meeting_later = {
        "id": "meeting-2",
        "subject": "One on One",
        "start_time": (now + timedelta(minutes=4)).isoformat(),  # within 5m reminder window
        "end_time": (now + timedelta(minutes=34)).isoformat(),
        "join_url": "https://teams.microsoft.com/meet/456",
        "is_online": True,
    }

    client.get_calendar_meetings = AsyncMock(return_value=[meeting_now, meeting_later])
    client.get_unread_chats_count = AsyncMock(return_value=3)
    client.get_active_call_and_unread = AsyncMock(return_value=(None, 3))
    client.get_thread_participants = AsyncMock(return_value=["Alice", "Bob"])

    coordinator = TeamsDataUpdateCoordinator(hass, entry, client)

    fired_events: list[tuple[str, dict]] = []

    def event_listener(event_type, event_data):
        fired_events.append((event_type, event_data))

    coordinator.register_event_listener(event_listener)

    data = await coordinator._async_update_data()

    assert data["presence"]["availability"] == "Busy"
    assert data["in_a_meeting"] is True
    assert data["meeting_title"] == "Sprint Review"
    assert data["unread_chats"] == 3
    assert data["active_meeting"]["id"] == "meeting-1"

    # Verify fired events
    event_types = [e[0] for e in fired_events]
    assert EVENT_MEETING_STARTED in event_types
    assert EVENT_MEETING_REMINDER in event_types

    # End meeting on next refresh
    client.get_calendar_meetings = AsyncMock(return_value=[])
    client.get_presence = AsyncMock(
        return_value={"availability": "Available", "activity": "Available", "status_message": ""}
    )
    data2 = await coordinator._async_update_data()
    assert data2["in_a_meeting"] is False
    assert EVENT_MEETING_ENDED in [e[0] for e in fired_events]


def test_coordinator_token_persistence():
    """Verify coordinator registers callback and updates entry data on token refresh."""
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {"refresh_token": "old_token", "tenant_id": "test"}
    entry.options = {}
    client = MagicMock()
    auth = MagicMock()
    client.auth = auth

    TeamsDataUpdateCoordinator(hass, entry, client)

    assert auth.on_tokens_refreshed is not None
    auth.on_tokens_refreshed({"refresh_token": "new_rotated_token"})

    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        data={"refresh_token": "new_rotated_token", "tenant_id": "test"},
    )


@pytest.mark.asyncio
async def test_meeting_event_details_persist_through_updates_and_end():
    """Every event carries meeting details, including after the meeting disappears."""
    hass = MagicMock()
    entry = MagicMock(entry_id="test", title="test", options={}, data={})
    client = MagicMock()
    client.auth = MagicMock()
    client.get_presence = AsyncMock(
        return_value={"activity": "Available", "availability": "Available"}
    )
    now = datetime.now(UTC)
    meeting = {
        "id": "meeting-1",
        "subject": "Planning",
        "description": "Agenda",
        "join_url": "https://example.com/join",
        "start_time": (now - timedelta(minutes=1)).isoformat(),
        "end_time": (now + timedelta(minutes=30)).isoformat(),
        "organizer_name": "Organizer",
        "location": "Online",
        "thread_id": "thread-1",
        "is_online": True,
    }
    client.get_calendar_meetings = AsyncMock(return_value=[meeting])
    client.get_active_call_and_unread = AsyncMock(return_value=(None, 0))
    client.get_thread_participants = AsyncMock(return_value=["Alice", "Bob"])
    coordinator = TeamsDataUpdateCoordinator(hass, entry, client)
    events = []
    coordinator.register_event_listener(lambda kind, data: events.append((kind, data)))

    await coordinator._async_update_data()
    started = events[-1][1]
    assert events[-1][0] == EVENT_MEETING_STARTED
    assert started["description"] == "Agenda"
    assert started["url"] == started["join_url"] == "https://example.com/join"
    assert started["participants"] == ["Alice", "Bob"]

    updated = {**meeting, "description": "Updated agenda"}
    client.get_calendar_meetings.return_value = [updated]
    await coordinator._async_update_data()
    client.get_calendar_meetings.return_value = []
    await coordinator._async_update_data()
    kind, ended = events[-1]
    assert kind == EVENT_MEETING_ENDED
    assert ended["meeting_id"] == "meeting-1"
    assert ended["title"] == "Planning"
    assert ended["description"] == "Updated agenda"
    assert ended["join_url"] == "https://example.com/join"
    assert ended["participants_count"] == 2
    await coordinator._async_update_data()
    assert sum(kind == EVENT_MEETING_ENDED for kind, _ in events) == 1


@pytest.mark.asyncio
async def test_reminder_includes_meeting_details():
    hass = MagicMock()
    entry = MagicMock(entry_id="test", title="test", options={}, data={})
    client = MagicMock()
    client.auth = MagicMock()
    client.get_presence = AsyncMock(return_value={})
    now = datetime.now(UTC)
    client.get_calendar_meetings = AsyncMock(
        return_value=[
            {
                "id": "next",
                "subject": "Next",
                "description": "Topics",
                "join_url": "https://example.com/join",
                "start_time": (now + timedelta(minutes=4)).isoformat(),
                "end_time": (now + timedelta(minutes=34)).isoformat(),
                "organizer_name": "Organizer",
            }
        ]
    )
    client.get_active_call_and_unread = AsyncMock(return_value=(None, 0))
    coordinator = TeamsDataUpdateCoordinator(hass, entry, client)
    events = []
    coordinator.register_event_listener(lambda kind, data: events.append((kind, data)))
    await coordinator._async_update_data()
    kind, reminder = events[0]
    assert kind == EVENT_MEETING_REMINDER
    assert reminder["description"] == "Topics"
    assert reminder["title"] == "Next"
    assert reminder["organizer_name"] == "Organizer"
    assert reminder["url"] == reminder["join_url"]


@pytest.mark.asyncio
async def test_switching_meetings_ends_previous_before_starting_next():
    hass = MagicMock()
    entry = MagicMock(entry_id="test", title="test", options={}, data={})
    client = MagicMock()
    client.auth = MagicMock()
    client.get_presence = AsyncMock(return_value={})
    client.get_active_call_and_unread = AsyncMock(return_value=(None, 0))
    now = datetime.now(UTC)
    meeting = {
        "id": "one",
        "subject": "First",
        "start_time": (now - timedelta(minutes=2)).isoformat(),
        "end_time": (now + timedelta(minutes=20)).isoformat(),
    }
    client.get_calendar_meetings = AsyncMock(return_value=[meeting])
    coordinator = TeamsDataUpdateCoordinator(hass, entry, client)
    events = []
    coordinator.register_event_listener(lambda kind, data: events.append((kind, data)))
    await coordinator._async_update_data()
    client.get_calendar_meetings.return_value = [{**meeting, "id": "two", "subject": "Second"}]
    await coordinator._async_update_data()
    assert [(kind, data["meeting_id"]) for kind, data in events] == [
        (EVENT_MEETING_STARTED, "one"),
        (EVENT_MEETING_ENDED, "one"),
        (EVENT_MEETING_STARTED, "two"),
    ]


def _make_coordinator(monkeypatch):
    monkeypatch.setattr("custom_components.msteams.coordinator.CALENDAR_RETRY_DELAY", 0)
    entry = MagicMock()
    entry.entry_id = "entry123"
    entry.title = "Alice"
    entry.options = {}
    entry.data = {}
    client = MagicMock()
    client.browser_daemon_url = None
    client.unread_chats = []
    client.auth = MagicMock()
    client.auth.user_mri = "8:orgid:alice"
    client.get_presence = AsyncMock(
        return_value={"availability": "Available", "activity": "Available"}
    )
    client.get_calendar_meetings = AsyncMock(return_value=[])
    client.get_active_call_and_unread = AsyncMock(return_value=(None, 2))
    return TeamsDataUpdateCoordinator(MagicMock(), entry, client), client


@pytest.mark.asyncio
async def test_calendar_failure_keeps_last_meetings(monkeypatch):
    """A failing calendar must not make the whole update fail."""
    from custom_components.msteams.api import TeamsApiError

    coordinator, client = _make_coordinator(monkeypatch)
    now = datetime.now(UTC)
    meeting = {
        "id": "m1",
        "subject": "Planning",
        "start_time": (now + timedelta(hours=2)).isoformat(),
        "end_time": (now + timedelta(hours=3)).isoformat(),
    }
    client.get_calendar_meetings = AsyncMock(return_value=[meeting])
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data["next_meeting"]["id"] == "m1"

    client.get_calendar_meetings = AsyncMock(
        side_effect=TeamsApiError("Calendar API failed (HTTP 503)")
    )
    data = await coordinator._async_update_data()

    assert client.get_calendar_meetings.await_count == 2
    assert data["meetings"] == [meeting]
    assert data["presence"]["availability"] == "Available"
    assert data["unread_chats"] == 2


@pytest.mark.asyncio
async def test_calendar_retry_recovers(monkeypatch):
    from custom_components.msteams.api import TeamsApiError

    coordinator, client = _make_coordinator(monkeypatch)
    client.get_calendar_meetings = AsyncMock(side_effect=[TeamsApiError("HTTP 500"), []])
    data = await coordinator._async_update_data()
    assert data["meetings"] == []
    assert client.get_calendar_meetings.await_count == 2


@pytest.mark.asyncio
async def test_active_call_failure_keeps_last_state(monkeypatch):
    coordinator, client = _make_coordinator(monkeypatch)
    call = {"thread_id": "19:t@thread.v2", "title": "Standup", "url": "https://example.com/j"}
    client.get_active_call_and_unread = AsyncMock(return_value=(call, 4))
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data["in_a_meeting"] is True

    client.get_active_call_and_unread = AsyncMock(side_effect=TimeoutError())
    data = await coordinator._async_update_data()
    assert data["in_a_meeting"] is True
    assert data["unread_chats"] == 4


@pytest.mark.asyncio
async def test_presence_failure_still_fails_update(monkeypatch):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coordinator, client = _make_coordinator(monkeypatch)
    client.get_presence = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
