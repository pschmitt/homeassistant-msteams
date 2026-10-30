"""Config flow for Microsoft Teams integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TeamsAuth, TeamsAuthError
from .const import (
    CONF_BROWSER_DAEMON_PROFILE,
    CONF_BROWSER_DAEMON_SECRET,
    CONF_BROWSER_DAEMON_URL,
    CONF_DEFAULT_CONVERSATION_ID,
    CONF_ENABLE_MEETING_CONTROLS,
    CONF_POLL_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_USER_DISPLAY_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PROFILE,
    DOMAIN,
    TEAMS_CLIENT_ID,
)

_LOGGER = logging.getLogger(__name__)


class TeamsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Microsoft Teams."""

    VERSION = 1

    def __init__(self) -> None:
        self.reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step where user chooses authentication mode."""
        return await self.async_step_browser_daemon(user_input)

    async def async_step_browser_daemon(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure Teams using browser-daemon session export."""
        errors: dict[str, str] = {}

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
                )

                unique_id = auth.user_mri or auth.user_email or "teams_user"
                if self.reauth_entry:
                    self.hass.config_entries.async_update_entry(
                        self.reauth_entry,
                        data={
                            **self.reauth_entry.data,
                            **user_input,
                            **auth.export_data(),
                        },
                    )
                    await self.hass.config_entries.async_reload(self.reauth_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = auth.user_display_name or auth.user_email or "Teams"
                entry_data = {
                    **user_input,
                    **auth.export_data(),
                }
                return self.async_create_entry(title=title, data=entry_data)

            except TeamsAuthError as err:
                _LOGGER.warning("Authentication failed during setup: %s", err)
                errors["base"] = "auth_failed"
            except Exception as err:
                _LOGGER.exception("Unexpected error during Teams setup: %s", err)
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BROWSER_DAEMON_URL,
                    default="https://browser.rofl-14.netbird.cloud",
                ): str,
                vol.Optional(
                    CONF_BROWSER_DAEMON_PROFILE,
                    default=DEFAULT_PROFILE,
                ): str,
                vol.Optional(CONF_BROWSER_DAEMON_SECRET): str,
                vol.Optional(CONF_DEFAULT_CONVERSATION_ID): str,
            }
        )
        return self.async_show_form(step_id="browser_daemon", data_schema=schema, errors=errors)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manual token entry flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            auth = TeamsAuth(
                session=session,
                refresh_token=user_input[CONF_REFRESH_TOKEN],
                tenant_id=user_input.get(CONF_TENANT_ID, "common"),
                client_id=TEAMS_CLIENT_ID,
                user_display_name=user_input.get(CONF_USER_DISPLAY_NAME),
            )
            try:
                await auth.async_refresh_tokens()
                unique_id = auth.user_mri or auth.user_email or "teams_user"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = auth.user_display_name or "Teams Account"
                entry_data = {
                    **user_input,
                    **auth.export_data(),
                }
                return self.async_create_entry(title=title, data=entry_data)
            except Exception as err:
                _LOGGER.warning("Manual token validation failed: %s", err)
                errors["base"] = "auth_failed"

        schema = vol.Schema(
            {
                vol.Required(CONF_REFRESH_TOKEN): str,
                vol.Optional(CONF_TENANT_ID, default="common"): str,
                vol.Optional(CONF_USER_DISPLAY_NAME, default="User"): str,
                vol.Optional(CONF_DEFAULT_CONVERSATION_ID): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauthorization initiated by repairs or expired tokens."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_browser_daemon()

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return TeamsOptionsFlow(config_entry)


class TeamsOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Microsoft Teams."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_POLL_INTERVAL,
                        self.config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                vol.Optional(
                    CONF_ENABLE_MEETING_CONTROLS,
                    default=self.config_entry.options.get(
                        CONF_ENABLE_MEETING_CONTROLS,
                        self.config_entry.data.get(CONF_ENABLE_MEETING_CONTROLS, True),
                    ),
                ): bool,
                vol.Optional(
                    CONF_DEFAULT_CONVERSATION_ID,
                    default=self.config_entry.options.get(
                        CONF_DEFAULT_CONVERSATION_ID,
                        self.config_entry.data.get(CONF_DEFAULT_CONVERSATION_ID, ""),
                    ),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
