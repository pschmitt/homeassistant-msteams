"""Unit tests for Teams entities (sensors, binary sensors, buttons, notify)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.msteams.binary_sensor import (
    TeamsConnectedBinarySensor,
    TeamsInMeetingBinarySensor,
)
from custom_components.msteams.button import (
    TeamsRefreshTokenButton,
)
from custom_components.msteams.notify import TeamsNotifyEntity
from custom_components.msteams.sensor import (
    TeamsActiveMeetingSensor,
    TeamsNextMeetingSensor,
    TeamsPresenceSensor,
    TeamsUnreadChatsSensor,
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.entry = MagicMock()
    coordinator.entry.entry_id = "test_entry"
    coordinator.entry.title = "Alice"
    coordinator.entry.data = {"default_conversation_id": "19:notes@thread.v2"}
    coordinator.entry.options = {}
    coordinator.last_update_success = True
    coordinator.client = MagicMock()
    coordinator.client.auth = MagicMock()
    coordinator.client.auth.is_authenticated = True

    coordinator.data = {
        "presence": {
            "availability": "Available",
            "activity": "Available",
            "status_message": "Working from home",
        },
        "next_meeting": {
            "id": "meet123",
            "subject": "Sprint Planning",
            "start_time": "2026-11-02T09:00:00Z",
            "end_time": "2026-11-02T10:00:00Z",
            "organizer_name": "Bob",
            "join_url": "https://teams.microsoft.com/meet/xyz",
        },
        "active_meeting": {
            "subject": "Daily Standup",
        },
        "in_a_meeting": True,
        "meeting_title": "Daily Standup",
        "meeting_url": "https://teams.microsoft.com/meet/standup",
        "unread_chats": 5,
        "user_display_name": "Alice Smith",
        "user_email": "alice@example.com",
    }
    return coordinator


def test_sensors(mock_coordinator):
    """Test presence, next meeting, active meeting, and unread chats sensors."""
    presence_sensor = TeamsPresenceSensor(mock_coordinator)
    assert presence_sensor.native_value == "Available"
    assert presence_sensor.extra_state_attributes["status_message"] == "Working from home"

    next_meeting_sensor = TeamsNextMeetingSensor(mock_coordinator)
    assert next_meeting_sensor.native_value == "Sprint Planning"
    assert next_meeting_sensor.extra_state_attributes["organizer"] == "Bob"

    active_meeting_sensor = TeamsActiveMeetingSensor(mock_coordinator)
    assert active_meeting_sensor.native_value == "Daily Standup"
    assert active_meeting_sensor.extra_state_attributes["in_meeting"] is True

    unread_sensor = TeamsUnreadChatsSensor(mock_coordinator)
    assert unread_sensor.native_value == 5


def test_binary_sensors(mock_coordinator):
    """Test in_a_meeting and connected binary sensors."""
    in_meeting = TeamsInMeetingBinarySensor(mock_coordinator)
    assert in_meeting.is_on is True
    assert in_meeting.extra_state_attributes["title"] == "Daily Standup"

    connected = TeamsConnectedBinarySensor(mock_coordinator)
    assert connected.is_on is True


@pytest.mark.asyncio
async def test_buttons(mock_coordinator):
    """Test button press for token refresh."""
    mock_coordinator.client.auth.async_refresh_tokens = AsyncMock()
    mock_coordinator.async_request_refresh = AsyncMock()
    refresh_btn = TeamsRefreshTokenButton(mock_coordinator)
    await refresh_btn.async_press()
    mock_coordinator.client.auth.async_refresh_tokens.assert_awaited_once()
    mock_coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_entity(mock_coordinator):
    """Test Teams notify entity sending message."""
    mock_coordinator.client.send_message = AsyncMock()
    notify = TeamsNotifyEntity(mock_coordinator)

    await notify.async_send_message(
        message="Build finished!",
        title="CI Alert",
    )
    mock_coordinator.client.send_message.assert_awaited_once_with(
        conversation_id="19:notes@thread.v2",
        content="Build finished!",
        reply_to_message_id=None,
        subject="CI Alert",
    )


@pytest.mark.asyncio
async def test_notify_defaults_to_self_and_propagates_failures(mock_coordinator):
    from homeassistant.exceptions import HomeAssistantError

    mock_coordinator.entry.data = {}
    mock_coordinator.client.send_message = AsyncMock()
    notify = TeamsNotifyEntity(mock_coordinator)
    await notify.async_send_message(message="test")
    assert mock_coordinator.client.send_message.await_args.kwargs["conversation_id"] == "48:notes"
    mock_coordinator.client.send_message.side_effect = RuntimeError("failed")
    with pytest.raises(HomeAssistantError):
        await notify.async_send_message(message="test")
