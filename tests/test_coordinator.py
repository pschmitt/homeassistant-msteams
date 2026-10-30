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
