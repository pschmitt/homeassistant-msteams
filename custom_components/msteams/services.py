"""Custom services for Microsoft Teams integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import CONF_DEFAULT_CONVERSATION_ID, DOMAIN
from .coordinator import TeamsDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_MESSAGE = "send_message"
SERVICE_REPLY_MESSAGE = "reply_message"
SERVICE_ADD_REACTION = "add_reaction"

SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Optional("conversation_id"): cv.string,
        vol.Optional("reply_to_message_id"): cv.string,
        vol.Optional("subject"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

REPLY_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required("conversation_id"): cv.string,
        vol.Required("message_id"): cv.string,
        vol.Required("message"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)

ADD_REACTION_SCHEMA = vol.Schema(
    {
        vol.Required("conversation_id"): cv.string,
        vol.Required("message_id"): cv.string,
        vol.Optional("reaction", default="like"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)


def _get_coordinator(
    hass: HomeAssistant, entry_id: str | None = None
) -> TeamsDataUpdateCoordinator | None:
    """Retrieve the coordinator for the specified entry_id or the first available entry."""
    domain_data = hass.data.get(DOMAIN, {})
    if entry_id and entry_id in domain_data:
        return domain_data[entry_id]
    if domain_data:
        return next(iter(domain_data.values()))
    return None


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register Microsoft Teams services."""

    async def handle_send_message(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        if not coordinator:
            _LOGGER.error("No Microsoft Teams integration entries found to send message")
            return

        conversation_id = (
            call.data.get("conversation_id")
            or coordinator.entry.options.get(CONF_DEFAULT_CONVERSATION_ID)
            or coordinator.entry.data.get(CONF_DEFAULT_CONVERSATION_ID)
        )
        if not conversation_id:
            _LOGGER.error("Missing conversation_id for msteams.send_message")
            return

        await coordinator.client.send_message(
            conversation_id=conversation_id,
            content=call.data["message"],
            reply_to_message_id=call.data.get("reply_to_message_id"),
            subject=call.data.get("subject"),
        )

    async def handle_reply_message(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        if not coordinator:
            _LOGGER.error("No Microsoft Teams integration entries found to reply message")
            return

        await coordinator.client.send_message(
            conversation_id=call.data["conversation_id"],
            content=call.data["message"],
            reply_to_message_id=call.data["message_id"],
        )

    async def handle_add_reaction(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        if not coordinator:
            _LOGGER.error("No Microsoft Teams integration entries found to add reaction")
            return

        await coordinator.client.add_reaction(
            conversation_id=call.data["conversation_id"],
            message_id=call.data["message_id"],
            reaction=call.data["reaction"],
        )

    services = [
        (SERVICE_SEND_MESSAGE, handle_send_message, SEND_MESSAGE_SCHEMA),
        (SERVICE_REPLY_MESSAGE, handle_reply_message, REPLY_MESSAGE_SCHEMA),
        (SERVICE_ADD_REACTION, handle_add_reaction, ADD_REACTION_SCHEMA),
    ]

    for service_name, handler, schema in services:
        hass.services.async_register(DOMAIN, service_name, handler, schema=schema)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Microsoft Teams services."""
    services = [
        SERVICE_SEND_MESSAGE,
        SERVICE_REPLY_MESSAGE,
        SERVICE_ADD_REACTION,
    ]
    for service_name in services:
        hass.services.async_remove(DOMAIN, service_name)
