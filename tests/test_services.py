"""Unit tests for Teams services."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.msteams.const import DOMAIN
from custom_components.msteams.services import (
    SERVICE_ADD_REACTION,
    SERVICE_REPLY_MESSAGE,
    SERVICE_SEND_MESSAGE,
    SERVICE_SET_MUTE,
    SERVICE_TOGGLE_MUTE,
    async_setup_services,
    async_unload_services,
)


@pytest.mark.asyncio
async def test_services_registration_and_calls():
    """Test service registration and dispatching calls to client."""
    hass = MagicMock()
    registered_services: dict[str, Any] = {}

    def async_register(domain, service_name, handler, schema=None):
        registered_services[service_name] = handler

    def async_remove(domain, service_name):
        registered_services.pop(service_name, None)

    hass.services.async_register = async_register
    hass.services.async_remove = async_remove

    # Setup services
    await async_setup_services(hass)

    assert SERVICE_SEND_MESSAGE in registered_services
    assert SERVICE_REPLY_MESSAGE in registered_services
    assert SERVICE_ADD_REACTION in registered_services
    assert SERVICE_TOGGLE_MUTE in registered_services
    assert SERVICE_SET_MUTE in registered_services

    # Setup mock coordinator in hass.data
    mock_coordinator = MagicMock()
    mock_coordinator.entry = MagicMock()
    mock_coordinator.entry.data = {"default_conversation_id": "19:default@thread.v2"}
    mock_coordinator.entry.options = {}
    mock_coordinator.client = MagicMock()
    mock_coordinator.client.send_message = AsyncMock()
    mock_coordinator.client.add_reaction = AsyncMock()
    mock_coordinator.client.toggle_meeting_mute = AsyncMock(return_value=True)
    mock_coordinator.client.set_meeting_mute = AsyncMock(return_value=True)
    mock_coordinator.async_request_refresh = AsyncMock()

    hass.data = {DOMAIN: {"entry1": mock_coordinator}}

    # Call send_message
    call = MagicMock()
    call.data = {"message": "Hello teams!"}
    await registered_services[SERVICE_SEND_MESSAGE](call)
    mock_coordinator.client.send_message.assert_awaited_once_with(
        conversation_id="19:default@thread.v2",
        content="Hello teams!",
        reply_to_message_id=None,
        subject=None,
    )

    # Call toggle_mute
    call_mute = MagicMock()
    call_mute.data = {}
    await registered_services[SERVICE_TOGGLE_MUTE](call_mute)
    mock_coordinator.client.toggle_meeting_mute.assert_awaited_once()

    # Unload services
    await async_unload_services(hass)
    assert len(registered_services) == 0
