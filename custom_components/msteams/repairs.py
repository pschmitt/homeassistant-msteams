"""Repairs flow for Microsoft Teams authentication issues."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TeamsAuth, TeamsAuthError
from .const import (
    CONF_BROWSER_DAEMON_PROFILE,
    CONF_BROWSER_DAEMON_SECRET,
    CONF_BROWSER_DAEMON_URL,
    DEFAULT_BROWSER_DAEMON_URL,
    DEFAULT_PROFILE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class TeamsAuthRepairFlow(RepairsFlow):
    """Handler for an issue fixing flow when Teams authentication expires."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the first step of the repairs flow."""
        return await self.async_step_reauth(user_input)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Re-authenticate with browser-daemon to restore session tokens."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if not entry:
            return self.async_abort(reason="entry_not_found")

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            daemon_url = user_input[CONF_BROWSER_DAEMON_URL]
            profile = user_input.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE)
            secret = user_input.get(CONF_BROWSER_DAEMON_SECRET) or None

            try:
                auth = await TeamsAuth.async_from_browser_daemon(
                    session=session,
                    browser_daemon_url=daemon_url,
                    profile=profile,
                    secret=secret,
                    hass=self.hass,
                )
                # Update config entry with refreshed auth state
                new_data = {
                    **entry.data,
                    **user_input,
                    **auth.export_data(),
                }
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                ir.async_delete_issue(self.hass, DOMAIN, f"auth_failed_{self.entry_id}")
                await self.hass.config_entries.async_reload(self.entry_id)
                return self.async_create_entry(title="", data={})
            except TeamsAuthError as err:
                _LOGGER.warning("Repair authentication failed: %s", err)
                errors["base"] = "auth_failed"
            except Exception as err:
                _LOGGER.exception("Unexpected error in Teams repair flow: %s", err)
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BROWSER_DAEMON_URL,
                    default=entry.data.get(CONF_BROWSER_DAEMON_URL, DEFAULT_BROWSER_DAEMON_URL),
                ): str,
                vol.Optional(
                    CONF_BROWSER_DAEMON_PROFILE,
                    default=entry.data.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE),
                ): str,
                vol.Optional(
                    CONF_BROWSER_DAEMON_SECRET,
                    default=entry.data.get(CONF_BROWSER_DAEMON_SECRET, ""),
                ): str,
            }
        )
        return self.async_show_form(step_id="reauth", data_schema=schema, errors=errors)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create flow to resolve Teams issue."""
    entry_id = data.get("entry_id") if data else issue_id.replace("auth_failed_", "")
    return TeamsAuthRepairFlow(entry_id)
