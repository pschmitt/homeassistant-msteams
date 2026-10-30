"""Button platform for Microsoft Teams."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TeamsDataUpdateCoordinator
from .entity import TeamsEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Microsoft Teams buttons."""
    coordinator: TeamsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            TeamsRefreshTokenButton(coordinator),
        ]
    )


class TeamsRefreshTokenButton(TeamsEntity, ButtonEntity):
    """Button to manually force token refresh."""

    _attr_translation_key = "refresh_tokens"
    _attr_icon = "mdi:key-change"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "refresh_tokens")

    async def async_press(self) -> None:
        """Trigger an immediate token refresh."""
        try:
            await self.coordinator.client.auth.async_refresh_tokens()
            # Update stored tokens in entry data
            new_data = {
                **self.coordinator.entry.data,
                **self.coordinator.client.auth.export_data(),
            }
            if self.coordinator.hass:
                self.coordinator.hass.config_entries.async_update_entry(
                    self.coordinator.entry, data=new_data
                )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.exception("Manual token refresh failed: %s", err)
