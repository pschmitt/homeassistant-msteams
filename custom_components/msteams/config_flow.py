"""Config flow for Microsoft Teams integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TeamsAuth, TeamsAuthError
from .const import (
    CONF_BROWSER_DAEMON_PROFILE,
    CONF_CREDENTIALS_FILE,
    CONF_DEFAULT_CONVERSATION_ID,
    CONF_POLL_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_USER_DISPLAY_NAME,
    CONF_USE_BROWSER_DAEMON,
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
        """Handle initial user setup."""
        errors: dict[str, str] = {}
        has_browser_daemon = "browser_daemon" in self.hass.config.components or bool(
            self.hass.data.get("browser_daemon")
        )

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            use_browser_daemon = user_input.get(CONF_USE_BROWSER_DAEMON, False)
            creds_file = user_input.get(CONF_CREDENTIALS_FILE)
            refresh_token = user_input.get(CONF_REFRESH_TOKEN)

            try:
                auth: TeamsAuth | None = None
                if use_browser_daemon:
                    profile = user_input.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE)
                    auth = await TeamsAuth.async_from_browser_daemon(
                        session=session,
                        profile=profile,
                        hass=self.hass,
                    )
                elif creds_file:
                    auth = await TeamsAuth.async_from_credentials(session, creds_file)
                elif refresh_token:
                    auth = TeamsAuth(
                        session=session,
                        refresh_token=refresh_token,
                        tenant_id=user_input.get(CONF_TENANT_ID, "common"),
                        client_id=TEAMS_CLIENT_ID,
                        user_display_name=user_input.get(CONF_USER_DISPLAY_NAME),
                    )
                    await auth.async_refresh_tokens()
                else:
                    errors["base"] = "missing_credentials"

                if auth:
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

                    title = auth.user_display_name or auth.user_email or "Teams Account"
                    entry_data = {
                        **user_input,
                        **auth.export_data(),
                    }
                    return self.async_create_entry(title=title, data=entry_data)

            except AbortFlow:
                raise
            except TeamsAuthError as err:
                _LOGGER.warning("Authentication failed during setup: %s", err)
                errors["base"] = "auth_failed"
            except Exception as err:
                _LOGGER.exception("Unexpected error during Teams setup: %s", err)
                errors["base"] = "cannot_connect"

        schema_dict: dict[Any, Any] = {}
        if has_browser_daemon:
            schema_dict[vol.Optional(CONF_USE_BROWSER_DAEMON, default=True)] = bool
            schema_dict[vol.Optional(CONF_BROWSER_DAEMON_PROFILE, default=DEFAULT_PROFILE)] = str

        schema_dict[vol.Optional(CONF_CREDENTIALS_FILE)] = str
        schema_dict[vol.Optional(CONF_REFRESH_TOKEN)] = str
        schema_dict[vol.Optional(CONF_TENANT_ID, default="common")] = str
        schema_dict[vol.Optional(CONF_USER_DISPLAY_NAME)] = str
        schema_dict[vol.Optional(CONF_DEFAULT_CONVERSATION_ID)] = str

        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema_dict), errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauthorization initiated by repairs or expired tokens."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm reauthorization."""
        errors: dict[str, str] = {}
        has_browser_daemon = "browser_daemon" in self.hass.config.components or bool(
            self.hass.data.get("browser_daemon")
        )

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            use_browser_daemon = user_input.get(CONF_USE_BROWSER_DAEMON, has_browser_daemon)
            creds_file = user_input.get(CONF_CREDENTIALS_FILE)
            refresh_token = user_input.get(CONF_REFRESH_TOKEN)

            try:
                auth: TeamsAuth | None = None
                if use_browser_daemon:
                    profile = user_input.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE)
                    auth = await TeamsAuth.async_from_browser_daemon(
                        session=session,
                        profile=profile,
                        hass=self.hass,
                    )
                elif creds_file:
                    auth = await TeamsAuth.async_from_credentials(session, creds_file)
                elif refresh_token:
                    auth = TeamsAuth(
                        session=session,
                        refresh_token=refresh_token,
                        tenant_id=user_input.get(CONF_TENANT_ID, "common"),
                        client_id=TEAMS_CLIENT_ID,
                    )
                    await auth.async_refresh_tokens()
                else:
                    errors["base"] = "missing_credentials"

                if auth and self.reauth_entry:
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

            except AbortFlow:
                raise
            except TeamsAuthError as err:
                _LOGGER.warning("Authentication failed during reauth: %s", err)
                errors["base"] = "auth_failed"
            except Exception as err:
                _LOGGER.exception("Unexpected error during Teams reauth: %s", err)
                errors["base"] = "cannot_connect"

        default_profile = (
            self.reauth_entry.data.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE)
            if self.reauth_entry
            else DEFAULT_PROFILE
        )
        schema_dict: dict[Any, Any] = {}
        if has_browser_daemon:
            schema_dict[vol.Optional(CONF_USE_BROWSER_DAEMON, default=True)] = bool
            schema_dict[vol.Optional(CONF_BROWSER_DAEMON_PROFILE, default=default_profile)] = str

        schema_dict[vol.Optional(CONF_CREDENTIALS_FILE)] = str
        schema_dict[vol.Optional(CONF_REFRESH_TOKEN)] = str
        schema_dict[vol.Optional(CONF_TENANT_ID, default="common")] = str

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema(schema_dict), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return TeamsOptionsFlow()


class TeamsOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Microsoft Teams."""

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
                    CONF_DEFAULT_CONVERSATION_ID,
                    default=self.config_entry.options.get(
                        CONF_DEFAULT_CONVERSATION_ID,
                        self.config_entry.data.get(CONF_DEFAULT_CONVERSATION_ID, ""),
                    ),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
