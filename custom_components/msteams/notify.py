"""Notify platform for Microsoft Teams."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEFAULT_CONVERSATION_ID, DOMAIN
from .coordinator import TeamsDataUpdateCoordinator
from .entity import TeamsEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Microsoft Teams notify entity."""
    coordinator: TeamsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([TeamsNotifyEntity(coordinator)])


class TeamsNotifyEntity(TeamsEntity, NotifyEntity):
    """Notify entity to send messages to Microsoft Teams."""

    _attr_translation_key = "notify"
    _attr_icon = "mdi:microsoft-teams"

    def __init__(self, coordinator: TeamsDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "notify")

    async def async_send_message(
        self,
        message: str,
        title: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a notification message to Microsoft Teams."""
        data = kwargs.get("data") or {}
        conversation_id = (
            data.get("conversation_id")
            or self.coordinator.entry.options.get(CONF_DEFAULT_CONVERSATION_ID)
            or self.coordinator.entry.data.get(CONF_DEFAULT_CONVERSATION_ID)
        )

        if not conversation_id:
            _LOGGER.error(
                "Cannot send Teams notification: no default conversation ID configured for %s",
                self.coordinator.entry.title,
            )
            return

        reply_to = data.get("reply_to_message_id")
        try:
            await self.coordinator.client.send_message(
                conversation_id=conversation_id,
                content=message,
                reply_to_message_id=reply_to,
                subject=title,
            )
        except Exception as err:
            _LOGGER.exception(
                "Failed to send notification to Teams conversation %s: %s", conversation_id, err
            )
