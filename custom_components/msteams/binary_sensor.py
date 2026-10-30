"""Binary sensor platform for Microsoft Teams."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up Microsoft Teams binary sensors."""
    coordinator: TeamsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            TeamsInMeetingBinarySensor(coordinator),
            TeamsMeetingMutedBinarySensor(coordinator),
            TeamsConnectedBinarySensor(coordinator),
        ]
    )


class TeamsInMeetingBinarySensor(TeamsEntity, BinarySensorEntity):
    """Binary sensor indicating if the user is currently in a meeting."""

    _attr_translation_key = "in_a_meeting"
    _attr_icon = "mdi:phone-in-talk"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "in_a_meeting")

    @property
    def is_on(self) -> bool:
        """Return True if currently attending a meeting."""
        data = self.coordinator.data or {}
        return bool(data.get("in_a_meeting"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return attributes including active title and join URL."""
        data = self.coordinator.data or {}
        return {
            "title": data.get("meeting_title"),
            "url": data.get("meeting_url"),
        }


class TeamsMeetingMutedBinarySensor(TeamsEntity, BinarySensorEntity):
    """Binary sensor indicating if microphone is currently muted in meeting."""

    _attr_translation_key = "meeting_muted"
    _attr_icon = "mdi:microphone-off"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "meeting_muted")

    @property
    def is_on(self) -> bool:
        """Return True if muted in active meeting."""
        data = self.coordinator.data or {}
        return bool(data.get("meeting_muted"))

    @property
    def icon(self) -> str:
        """Dynamic icon based on mute state."""
        return "mdi:microphone-off" if self.is_on else "mdi:microphone"


class TeamsConnectedBinarySensor(TeamsEntity, BinarySensorEntity):
    """Binary sensor indicating if API connection and authentication are healthy."""

    _attr_translation_key = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "connected")

    @property
    def is_on(self) -> bool:
        """Return True if connected and authenticated."""
        return (
            self.coordinator.last_update_success and self.coordinator.client.auth.is_authenticated
        )
