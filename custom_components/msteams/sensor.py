"""Sensor platform for Microsoft Teams."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeamsDataUpdateCoordinator
from .entity import TeamsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Microsoft Teams sensors."""
    coordinator: TeamsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            TeamsPresenceSensor(coordinator),
            TeamsNextMeetingSensor(coordinator),
            TeamsActiveMeetingSensor(coordinator),
            TeamsUnreadChatsSensor(coordinator),
        ]
    )


class TeamsPresenceSensor(TeamsEntity, SensorEntity):
    """Sensor for current user presence and status message."""

    _attr_translation_key = "presence"
    _attr_icon = "mdi:account-clock"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "presence")

    @property
    def native_value(self) -> str | None:
        """Return the presence availability."""
        data = self.coordinator.data or {}
        presence = data.get("presence") or {}
        return presence.get("availability") or "Unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return presence attributes."""
        data = self.coordinator.data or {}
        presence = data.get("presence") or {}
        return {
            "activity": presence.get("activity"),
            "status_message": presence.get("status_message"),
        }


class TeamsNextMeetingSensor(TeamsEntity, SensorEntity):
    """Sensor representing the upcoming calendar meeting."""

    _attr_translation_key = "next_meeting"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "next_meeting")

    @property
    def native_value(self) -> str | None:
        """Return next meeting subject."""
        data = self.coordinator.data or {}
        next_m = data.get("next_meeting")
        return next_m.get("subject") if next_m else "No upcoming meetings"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of the upcoming meeting."""
        data = self.coordinator.data or {}
        next_m = data.get("next_meeting") or {}
        return {
            "meeting_id": next_m.get("id"),
            "start_time": next_m.get("start_time"),
            "end_time": next_m.get("end_time"),
            "organizer": next_m.get("organizer_name"),
            "organizer_email": next_m.get("organizer_email"),
            "location": next_m.get("location"),
            "is_online": next_m.get("is_online"),
            "join_url": next_m.get("join_url"),
            "my_response": next_m.get("my_response"),
        }


class TeamsActiveMeetingSensor(TeamsEntity, SensorEntity):
    """Sensor representing the current active meeting title and URL."""

    _attr_translation_key = "active_meeting"
    _attr_icon = "mdi:video-account"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "active_meeting")

    @property
    def native_value(self) -> str | None:
        """Return current meeting title or None."""
        data = self.coordinator.data or {}
        if data.get("in_a_meeting"):
            return data.get("meeting_title") or "Active Meeting"
        return "None"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return active meeting attributes."""
        data = self.coordinator.data or {}
        title = data.get("meeting_title")
        url = data.get("meeting_url")
        return {
            "in_meeting": data.get("in_a_meeting", False),
            "meeting_title": title,
            "meeting_name": title,
            "title": title,
            "meeting_url": url,
            "url": url,
            "join_url": url,
            "active_calendar_meeting": (data.get("active_meeting") or {}).get("subject"),
        }


class TeamsUnreadChatsSensor(TeamsEntity, SensorEntity):
    """Sensor for count of chats with unread messages."""

    _attr_translation_key = "unread_chats"
    _attr_icon = "mdi:message-badge"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "unread_chats")

    @property
    def native_value(self) -> int:
        """Return unread messages count."""
        data = self.coordinator.data or {}
        return int(data.get("unread_chats") or 0)
