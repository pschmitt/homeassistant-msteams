"""Event platform for Microsoft Teams."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MEETING_EVENT_TYPES
from .coordinator import TeamsDataUpdateCoordinator
from .entity import TeamsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Microsoft Teams event entities."""
    coordinator: TeamsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([TeamsMeetingEvent(coordinator)])


class TeamsMeetingEvent(TeamsEntity, EventEntity):
    """Event entity firing meeting lifecycle events (started, ended, reminder)."""

    _attr_translation_key = "meeting_event"
    _attr_icon = "mdi:calendar-star"
    _attr_event_types = MEETING_EVENT_TYPES

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "meeting_event")
        self._remove_listener: Any = None

    async def async_added_to_hass(self) -> None:
        """Register meeting lifecycle callback with coordinator."""
        await super().async_added_to_hass()

        @callback
        def _handle_coordinator_event(event_type: str, event_data: dict[str, Any]) -> None:
            if event_type in self._attr_event_types:
                self._async_handle_event(event_type, event_data)
                self.async_write_ha_state()

        self._remove_listener = self.coordinator.register_event_listener(_handle_coordinator_event)

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listener on remove."""
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()
