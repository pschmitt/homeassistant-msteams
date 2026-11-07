"""Repairs flow for Microsoft Teams authentication issues."""

from __future__ import annotations

import logging
from typing import Any

import probatio as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TeamsAuth, TeamsAuthError, _same_user_mri
from .config_flow import InvalidBrowserDaemonConnection, browser_connection
from .const import (
    CONF_BROWSER_DAEMON_ENTRY_ID,
    CONF_BROWSER_DAEMON_NODE_GROUP,
    CONF_BROWSER_DAEMON_OVERRIDE,
    CONF_BROWSER_DAEMON_PROFILE,
    CONF_BROWSER_DAEMON_SECRET,
    CONF_BROWSER_DAEMON_URL,
    CONF_CREDENTIALS_FILE,
    CONF_PREFER_PASSKEY,
    CONF_RBW_ENTRY_ID,
    CONF_RBW_ITEM_ID,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_USE_BROWSER_DAEMON,
    DEFAULT_PROFILE,
    DOMAIN,
    TEAMS_CLIENT_ID,
)
from .setup_form import browser_daemon_fields

_LOGGER = logging.getLogger(__name__)


class TeamsAuthRepairFlow(RepairsFlow):
    """Handler for an issue fixing flow when Teams authentication expires."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the first step of the repairs flow.

        Core starts a repair flow with the issue's data as ``user_input``; it is not form input.
        """
        return await self.async_step_reauth()

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Re-authenticate with browser-daemon to restore session tokens."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if not entry:
            return self.async_abort(reason="entry_not_found")

        settings = {**entry.data, **entry.options}
        use_browser = settings.get(CONF_USE_BROWSER_DAEMON, False)
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            submitted = {**settings, **user_input}

            try:
                connection: dict[str, Any] = {}
                if use_browser:
                    connection = browser_connection(user_input, settings)
                    auth = await TeamsAuth.async_from_browser_daemon(
                        session=session,
                        browser_daemon_entry_id=connection[CONF_BROWSER_DAEMON_ENTRY_ID],
                        browser_daemon_url=connection[CONF_BROWSER_DAEMON_URL],
                        profile=submitted.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE),
                        node_group=submitted.get(CONF_BROWSER_DAEMON_NODE_GROUP),
                        secret=connection[CONF_BROWSER_DAEMON_SECRET],
                        hass=self.hass,
                        rbw_entry_id=settings.get(CONF_RBW_ENTRY_ID),
                        rbw_item_id=settings.get(CONF_RBW_ITEM_ID),
                        prefer_passkey=settings.get(CONF_PREFER_PASSKEY, True),
                    )
                elif submitted.get(CONF_CREDENTIALS_FILE):
                    auth = await TeamsAuth.async_from_credentials(
                        session, submitted[CONF_CREDENTIALS_FILE]
                    )
                elif user_input.get(CONF_REFRESH_TOKEN):
                    auth = TeamsAuth(
                        session=session,
                        refresh_token=user_input[CONF_REFRESH_TOKEN],
                        tenant_id=submitted.get(CONF_TENANT_ID, "common"),
                        client_id=TEAMS_CLIENT_ID,
                    )
                    await auth.async_refresh_tokens()
                else:
                    raise TeamsAuthError("Provide a credentials file or a fresh refresh token")
                if entry.data.get("user_mri") and not _same_user_mri(
                    auth.user_mri, entry.data["user_mri"]
                ):
                    raise TeamsAuthError("The browser signed in to a different Teams account")
                # Update config entry with refreshed auth state
                new_data = {
                    **entry.data,
                    **user_input,
                    **connection,
                    **auth.export_data(),
                }
                new_data.pop(CONF_BROWSER_DAEMON_OVERRIDE, None)
                options = {
                    **entry.options,
                    **{key: value for key, value in user_input.items() if key in entry.options},
                }
                self.hass.config_entries.async_update_entry(entry, data=new_data, options=options)
                ir.async_delete_issue(self.hass, DOMAIN, f"auth_failed_{self.entry_id}")
                await self.hass.config_entries.async_reload(self.entry_id)
                return self.async_create_entry(title="", data={})
            except InvalidBrowserDaemonConnection:
                errors["base"] = "invalid_browser_daemon_connection"
            except TeamsAuthError:
                _LOGGER.warning("Teams repair authentication failed")
                errors["base"] = "auth_failed"
            except Exception:
                _LOGGER.error("Unexpected error in Teams repair flow")
                errors["base"] = "cannot_connect"

        if use_browser:
            fields = browser_daemon_fields(self.hass, settings, profile=DEFAULT_PROFILE)
        else:
            fields = {
                vol.Optional(
                    CONF_CREDENTIALS_FILE, default=settings.get(CONF_CREDENTIALS_FILE, "")
                ): str,
                vol.Optional(CONF_REFRESH_TOKEN): str,
                vol.Optional(CONF_TENANT_ID, default=settings.get(CONF_TENANT_ID, "common")): str,
            }
        schema = vol.Schema(fields)
        return self.async_show_form(step_id="reauth", data_schema=schema, errors=errors)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create flow to resolve Teams issue."""
    entry_id = data.get("entry_id") if data else issue_id.replace("auth_failed_", "")
    return TeamsAuthRepairFlow(entry_id)
