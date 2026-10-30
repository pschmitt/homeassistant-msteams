"""Unit tests for config flow and options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.msteams.config_flow import TeamsConfigFlow, TeamsOptionsFlow
from custom_components.msteams.const import (
    CONF_CREDENTIALS_FILE,
    CONF_DEFAULT_CONVERSATION_ID,
    CONF_POLL_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
)


@pytest.mark.asyncio
async def test_config_flow_manual_success():
    """Test successful configuration via manual refresh token."""
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.context = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_set_unique_id = AsyncMock()

    with patch(
        "custom_components.msteams.config_flow.TeamsAuth.async_refresh_tokens",
        new=AsyncMock(return_value=True),
    ):
        result = await flow.async_step_user(
            {
                CONF_REFRESH_TOKEN: "valid_token_123",
                CONF_TENANT_ID: "tenant-uuid",
            }
        )

    assert result["type"] == "create_entry"
    assert result["data"]["refresh_token"] == "valid_token_123"


@pytest.mark.asyncio
async def test_config_flow_credentials_file_success():
    """Test successful configuration via credentials file."""
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.context = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_set_unique_id = AsyncMock()

    mock_auth = MagicMock()
    mock_auth.user_mri = "8:orgid:alice"
    mock_auth.user_display_name = "Alice Smith"
    mock_auth.user_email = "alice@example.com"
    mock_auth.export_data.return_value = {
        "refresh_token": "token123",
        "tenant_id": "tenant123",
        "client_id": "client123",
        "user_mri": "8:orgid:alice",
        "user_display_name": "Alice Smith",
        "user_email": "alice@example.com",
    }

    with patch(
        "custom_components.msteams.config_flow.TeamsAuth.async_from_credentials",
        new=AsyncMock(return_value=mock_auth),
    ):
        result = await flow.async_step_user(
            {
                CONF_CREDENTIALS_FILE: "/tmp/fake_creds.json",
            }
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "Alice Smith"
    assert result["data"]["refresh_token"] == "token123"


@pytest.mark.asyncio
async def test_options_flow():
    """Test updating options."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {CONF_POLL_INTERVAL: 30}
    entry.options = {}

    options_flow = TeamsOptionsFlow()
    options_flow.handler = "test_entry"
    hass = MagicMock()
    hass.config_entries.async_get_known_entry.return_value = entry
    options_flow.hass = hass

    # Show form
    form = await options_flow.async_step_init()
    assert form["type"] == "form"

    # Submit form
    res = await options_flow.async_step_init(
        {
            CONF_POLL_INTERVAL: 45,
            CONF_DEFAULT_CONVERSATION_ID: "19:conv456",
        }
    )
    assert res["type"] == "create_entry"
    assert res["data"][CONF_POLL_INTERVAL] == 45
