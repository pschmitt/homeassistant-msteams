"""Config flow for Microsoft Teams integration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import probatio as vol
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import TeamsAuth, TeamsAuthError, _same_user_mri
from .const import (
    CONF_BROWSER_DAEMON_ENTRY_ID,
    CONF_BROWSER_DAEMON_NODE_GROUP,
    CONF_BROWSER_DAEMON_OVERRIDE,
    CONF_BROWSER_DAEMON_PROFILE,
    CONF_BROWSER_DAEMON_SECRET,
    CONF_BROWSER_DAEMON_URL,
    CONF_CREDENTIALS_FILE,
    CONF_DEFAULT_CONVERSATION_ID,
    CONF_ENABLE_PUSH,
    CONF_POLL_INTERVAL,
    CONF_PREFER_PASSKEY,
    CONF_RBW_ENTRY_ID,
    CONF_RBW_ITEM_ID,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_USE_BROWSER_DAEMON,
    CONF_USER_DISPLAY_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PROFILE,
    DOMAIN,
    TEAMS_CLIENT_ID,
)

_LOGGER = logging.getLogger(__name__)
# UI-only value: persisted disabled rbw settings remain empty strings.
RBW_DISABLED = "disabled"


class InvalidBrowserDaemonConnection(Exception):
    """The explicit browser-daemon connection is incomplete."""


def browser_connection(submitted: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    """Resolve the connection to store; hidden form fields are not submitted."""
    url = str(
        submitted.get(CONF_BROWSER_DAEMON_URL, saved.get(CONF_BROWSER_DAEMON_URL)) or ""
    ).strip()
    if not submitted.get(CONF_BROWSER_DAEMON_OVERRIDE, bool(url)):
        return {
            CONF_BROWSER_DAEMON_ENTRY_ID: submitted.get(
                CONF_BROWSER_DAEMON_ENTRY_ID, saved.get(CONF_BROWSER_DAEMON_ENTRY_ID)
            )
            or "",
            CONF_BROWSER_DAEMON_URL: "",
            CONF_BROWSER_DAEMON_SECRET: None,
        }
    # A blank secret keeps the saved one.
    secret = str(submitted.get(CONF_BROWSER_DAEMON_SECRET) or "").strip() or saved.get(
        CONF_BROWSER_DAEMON_SECRET
    )
    if urlsplit(url).scheme not in {"http", "https"} or not secret:
        raise InvalidBrowserDaemonConnection
    return {
        CONF_BROWSER_DAEMON_ENTRY_ID: "",
        CONF_BROWSER_DAEMON_URL: url.rstrip("/"),
        CONF_BROWSER_DAEMON_SECRET: secret,
    }


def connection_schema(
    hass: Any, defaults: dict[str, Any], *, browser_toggle: bool = True
) -> dict[Any, Any]:
    """Which Browser Daemon integration to use, or an override.

    The cluster secret is never prefilled.
    """
    # The repair form has no toggle: it only shows for browser authentication.
    browser = [{"field": CONF_USE_BROWSER_DAEMON, "value": True}] if browser_toggle else []
    saved = defaults.get(CONF_BROWSER_DAEMON_ENTRY_ID)
    daemons = {
        entry.entry_id: entry.title
        for entry in hass.config_entries.async_entries("browser_daemon")
        if not entry.disabled_by
    }
    if saved:
        daemons.setdefault(saved, "Unavailable integration")
    override = [*browser, {"field": CONF_BROWSER_DAEMON_OVERRIDE, "value": True}]
    selection = (
        {
            data_entry_flow.Optional(
                CONF_BROWSER_DAEMON_ENTRY_ID,
                default=saved or next(iter(daemons)),
                visible=[
                    *browser,
                    {"field": CONF_BROWSER_DAEMON_OVERRIDE, "operator": "not_eq", "value": True},
                ],
            ): vol.In(daemons)
        }
        if daemons
        else {}
    )
    return {
        **selection,
        data_entry_flow.Optional(
            CONF_BROWSER_DAEMON_OVERRIDE,
            default=defaults.get(
                CONF_BROWSER_DAEMON_OVERRIDE, bool(defaults.get(CONF_BROWSER_DAEMON_URL))
            )
            or not daemons,
            visible=browser or True,
        ): bool,
        data_entry_flow.Optional(
            CONF_BROWSER_DAEMON_URL,
            default=defaults.get(CONF_BROWSER_DAEMON_URL) or "",
            visible=override,
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        data_entry_flow.Optional(CONF_BROWSER_DAEMON_SECRET, visible=override): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }


def rbw_schema(
    hass: Any, defaults: dict[str, Any], *, browser_only: bool = False
) -> dict[Any, Any]:
    browser = {"field": CONF_USE_BROWSER_DAEMON, "value": True}
    selected = {"field": CONF_RBW_ENTRY_ID, "operator": "not_eq", "value": RBW_DISABLED}
    enabled = browser_only or defaults.get(CONF_USE_BROWSER_DAEMON, False)
    details = ([browser, selected] if browser_only else [selected]) if enabled else False
    entries = {RBW_DISABLED: "Disabled"}
    entries.update(
        {entry.entry_id: entry.title for entry in hass.config_entries.async_entries("rbw")}
    )
    if saved_entry := defaults.get(CONF_RBW_ENTRY_ID):
        entries.setdefault(saved_entry, "Unavailable integration")
    return {
        data_entry_flow.Optional(
            CONF_RBW_ENTRY_ID,
            default=defaults.get(CONF_RBW_ENTRY_ID) or RBW_DISABLED,
            visible=browser if browser_only else defaults.get(CONF_USE_BROWSER_DAEMON, False),
        ): vol.In(entries),
        data_entry_flow.Optional(
            CONF_RBW_ITEM_ID,
            default=defaults.get(CONF_RBW_ITEM_ID, ""),
            visible=details,
        ): str,
        data_entry_flow.Optional(
            CONF_PREFER_PASSKEY,
            default=defaults.get(CONF_PREFER_PASSKEY, True),
            visible=details,
        ): bool,
    }


def normalize_rbw_input(data: dict[str, Any]) -> dict[str, Any]:
    """Convert the UI disabled value and discard any stale hidden item id."""
    if data.get(CONF_RBW_ENTRY_ID) in ("", RBW_DISABLED):
        return {**data, CONF_RBW_ENTRY_ID: "", CONF_RBW_ITEM_ID: ""}
    return data


def rbw_config_valid(data: dict[str, Any]) -> bool:
    return bool(data.get(CONF_RBW_ENTRY_ID)) == bool(data.get(CONF_RBW_ITEM_ID))


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
            user_input = normalize_rbw_input(user_input)
        rbw_valid = not (user_input or {}).get(CONF_USE_BROWSER_DAEMON) or rbw_config_valid(
            user_input or {}
        )
        if user_input is not None and not rbw_valid:
            errors["base"] = "rbw_incomplete"
        if user_input is not None and rbw_valid:
            session = async_get_clientsession(self.hass)
            use_browser_daemon = user_input.get(CONF_USE_BROWSER_DAEMON, False)
            creds_file = user_input.get(CONF_CREDENTIALS_FILE)
            refresh_token = user_input.get(CONF_REFRESH_TOKEN)

            try:
                auth: TeamsAuth | None = None
                connection: dict[str, Any] = {}
                if use_browser_daemon:
                    profile = user_input.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE)
                    connection = browser_connection(user_input, {})
                    user_input = {
                        key: value
                        for key, value in user_input.items()
                        if key != CONF_BROWSER_DAEMON_OVERRIDE
                    }
                    auth = await TeamsAuth.async_from_browser_daemon(
                        session=session,
                        browser_daemon_entry_id=connection[CONF_BROWSER_DAEMON_ENTRY_ID],
                        browser_daemon_url=connection[CONF_BROWSER_DAEMON_URL],
                        secret=connection[CONF_BROWSER_DAEMON_SECRET],
                        profile=profile,
                        node_group=user_input.get(CONF_BROWSER_DAEMON_NODE_GROUP),
                        hass=self.hass,
                        rbw_entry_id=user_input.get(CONF_RBW_ENTRY_ID),
                        rbw_item_id=user_input.get(CONF_RBW_ITEM_ID),
                        prefer_passkey=user_input.get(CONF_PREFER_PASSKEY, True),
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
                                **connection,
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
                        **connection,
                        **auth.export_data(),
                        CONF_USE_BROWSER_DAEMON: use_browser_daemon,
                    }
                    return self.async_create_entry(title=title, data=entry_data)

            except AbortFlow:
                raise
            except InvalidBrowserDaemonConnection:
                errors["base"] = "invalid_browser_daemon_connection"
            except TeamsAuthError as err:
                _LOGGER.warning("Authentication failed during setup: %s", err)
                errors["base"] = "auth_failed"
            except Exception as err:
                _LOGGER.exception("Unexpected error during Teams setup: %s", err)
                errors["base"] = "cannot_connect"

        # An explicit connection works without the Browser Daemon integration.
        schema_dict: dict[Any, Any] = {
            vol.Optional(CONF_USE_BROWSER_DAEMON, default=has_browser_daemon): bool,
            **connection_schema(self.hass, user_input or {}),
        }
        schema_dict[
            data_entry_flow.Optional(
                CONF_BROWSER_DAEMON_PROFILE,
                default=DEFAULT_PROFILE,
                visible={"field": CONF_USE_BROWSER_DAEMON, "value": True},
            )
        ] = str
        schema_dict[
            data_entry_flow.Optional(
                CONF_BROWSER_DAEMON_NODE_GROUP,
                default="",
                visible={"field": CONF_USE_BROWSER_DAEMON, "value": True},
            )
        ] = str

        file_mode = {"field": CONF_USE_BROWSER_DAEMON, "operator": "not_eq", "value": True}
        schema_dict[data_entry_flow.Optional(CONF_CREDENTIALS_FILE, visible=file_mode)] = str
        schema_dict[data_entry_flow.Optional(CONF_REFRESH_TOKEN, visible=file_mode)] = str
        schema_dict[
            data_entry_flow.Optional(CONF_TENANT_ID, default="common", visible=file_mode)
        ] = str
        schema_dict[vol.Optional(CONF_USER_DISPLAY_NAME)] = str
        schema_dict[vol.Optional(CONF_DEFAULT_CONVERSATION_ID)] = str
        schema_dict.update(rbw_schema(self.hass, user_input or {}, browser_only=True))

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema_dict), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauthorization initiated by repairs or expired tokens."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm reauthorization."""
        return await self._async_step_authenticate(
            user_input,
            entry=self.reauth_entry,
            step_id="reauth_confirm",
            success_reason="reauth_successful",
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Reconfigure an existing Teams account."""
        entry = self._get_reconfigure_entry()
        return await self._async_step_authenticate(
            user_input,
            entry=entry,
            step_id="reconfigure",
            success_reason="reconfigure_successful",
        )

    async def _async_step_authenticate(
        self,
        user_input: dict[str, Any] | None,
        *,
        entry: config_entries.ConfigEntry | None,
        step_id: str,
        success_reason: str,
    ) -> FlowResult:
        """Authenticate and update an existing config entry."""
        errors: dict[str, str] = {}
        effective_settings = {
            **(entry.data if entry else {}),
            **(entry.options if entry else {}),
        }
        if step_id == "reconfigure":
            defaults = {**effective_settings, **(user_input or {})}
        else:
            defaults = {
                **(entry.data if entry else {}),
                **(entry.options if entry else {}),
                **(user_input or {}),
            }

        if user_input is not None:
            user_input = normalize_rbw_input(user_input)
        submitted_settings = {**effective_settings, **(user_input or {})}
        use_browser_daemon = submitted_settings.get(CONF_USE_BROWSER_DAEMON, False)
        rbw_valid = not use_browser_daemon or rbw_config_valid(submitted_settings)
        if user_input is not None and not rbw_valid:
            errors["base"] = "rbw_incomplete"
        if user_input is not None and rbw_valid:
            session = async_get_clientsession(self.hass)
            auth_settings = {**effective_settings, **user_input}
            creds_file = auth_settings.get(CONF_CREDENTIALS_FILE)
            refresh_token = user_input.get(CONF_REFRESH_TOKEN)

            try:
                auth: TeamsAuth | None = None
                connection: dict[str, Any] = {}
                if use_browser_daemon:
                    profile = user_input.get(
                        CONF_BROWSER_DAEMON_PROFILE,
                        effective_settings.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE),
                    )
                    connection = browser_connection(user_input, effective_settings)
                    user_input = {
                        key: value
                        for key, value in user_input.items()
                        if key != CONF_BROWSER_DAEMON_OVERRIDE
                    }
                    auth = await TeamsAuth.async_from_browser_daemon(
                        session=session,
                        browser_daemon_entry_id=connection[CONF_BROWSER_DAEMON_ENTRY_ID],
                        browser_daemon_url=connection[CONF_BROWSER_DAEMON_URL],
                        secret=connection[CONF_BROWSER_DAEMON_SECRET],
                        profile=profile,
                        node_group=submitted_settings.get(CONF_BROWSER_DAEMON_NODE_GROUP),
                        hass=self.hass,
                        rbw_entry_id=submitted_settings.get(CONF_RBW_ENTRY_ID),
                        rbw_item_id=submitted_settings.get(CONF_RBW_ITEM_ID),
                        prefer_passkey=submitted_settings.get(CONF_PREFER_PASSKEY, True),
                    )
                elif creds_file:
                    auth = await TeamsAuth.async_from_credentials(session, creds_file)
                elif refresh_token:
                    auth = TeamsAuth(
                        session=session,
                        refresh_token=refresh_token,
                        tenant_id=auth_settings.get(CONF_TENANT_ID, "common"),
                        client_id=TEAMS_CLIENT_ID,
                    )
                    await auth.async_refresh_tokens()
                else:
                    errors["base"] = "missing_credentials"

                if (
                    auth
                    and entry
                    and entry.data.get("user_mri")
                    and not _same_user_mri(auth.user_mri, entry.data["user_mri"])
                ):
                    raise TeamsAuthError("Authentication returned a different Teams account")

                if auth and entry:
                    updated_options = {
                        **entry.options,
                        CONF_RBW_ENTRY_ID: submitted_settings.get(CONF_RBW_ENTRY_ID, ""),
                        CONF_RBW_ITEM_ID: submitted_settings.get(CONF_RBW_ITEM_ID, ""),
                        CONF_PREFER_PASSKEY: submitted_settings.get(CONF_PREFER_PASSKEY, True),
                    }
                    updated_options.update(
                        {
                            key: user_input[key]
                            for key in (
                                CONF_USE_BROWSER_DAEMON,
                                CONF_BROWSER_DAEMON_PROFILE,
                                CONF_BROWSER_DAEMON_NODE_GROUP,
                                CONF_TENANT_ID,
                            )
                            if key in user_input and key in entry.options
                        }
                    )
                    updated_options.update(
                        {key: value for key, value in connection.items() if key in entry.options}
                    )
                    persisted_settings = {
                        key: submitted_settings[key]
                        for key in (
                            CONF_USE_BROWSER_DAEMON,
                            CONF_BROWSER_DAEMON_PROFILE,
                            CONF_BROWSER_DAEMON_NODE_GROUP,
                            CONF_TENANT_ID,
                        )
                        if key in submitted_settings
                    }
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            **persisted_settings,
                            **user_input,
                            **connection,
                            **auth.export_data(),
                            CONF_USE_BROWSER_DAEMON: use_browser_daemon,
                        },
                        options=updated_options,
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason=success_reason)

            except AbortFlow:
                raise
            except InvalidBrowserDaemonConnection:
                errors["base"] = "invalid_browser_daemon_connection"
            except TeamsAuthError as err:
                _LOGGER.warning("Authentication failed during %s: %s", step_id, err)
                errors["base"] = "auth_failed"
            except Exception as err:
                _LOGGER.exception("Unexpected error during Teams %s: %s", step_id, err)
                errors["base"] = "cannot_connect"

        default_profile = defaults.get(CONF_BROWSER_DAEMON_PROFILE, DEFAULT_PROFILE)
        schema_dict: dict[Any, Any] = {
            vol.Optional(
                CONF_USE_BROWSER_DAEMON,
                default=defaults.get(CONF_USE_BROWSER_DAEMON, False),
            ): bool,
            **connection_schema(self.hass, defaults),
        }
        schema_dict[
            data_entry_flow.Optional(
                CONF_BROWSER_DAEMON_PROFILE,
                default=default_profile,
                visible={"field": CONF_USE_BROWSER_DAEMON, "value": True},
            )
        ] = str
        schema_dict[
            data_entry_flow.Optional(
                CONF_BROWSER_DAEMON_NODE_GROUP,
                default=defaults.get(CONF_BROWSER_DAEMON_NODE_GROUP, ""),
                visible={"field": CONF_USE_BROWSER_DAEMON, "value": True},
            )
        ] = str

        file_mode = {"field": CONF_USE_BROWSER_DAEMON, "operator": "not_eq", "value": True}
        schema_dict[
            data_entry_flow.Optional(
                CONF_CREDENTIALS_FILE,
                default=defaults.get(CONF_CREDENTIALS_FILE, ""),
                visible=file_mode,
            )
        ] = str
        schema_dict[data_entry_flow.Optional(CONF_REFRESH_TOKEN, visible=file_mode)] = str
        schema_dict[
            data_entry_flow.Optional(
                CONF_TENANT_ID, default=defaults.get(CONF_TENANT_ID, "common"), visible=file_mode
            )
        ] = str
        schema_dict.update(rbw_schema(self.hass, defaults, browser_only=True))

        return self.async_show_form(
            step_id=step_id, data_schema=vol.Schema(schema_dict), errors=errors
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
        errors = {}
        if user_input is not None:
            user_input = normalize_rbw_input(user_input)
            if rbw_config_valid(user_input):
                return self.async_create_entry(title="", data=user_input)
            errors["base"] = "rbw_incomplete"

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
                    CONF_ENABLE_PUSH,
                    default=self.config_entry.options.get(CONF_ENABLE_PUSH, True),
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
        schema = schema.extend(
            rbw_schema(
                self.hass,
                {
                    **self.config_entry.data,
                    **self.config_entry.options,
                    **(user_input or {}),
                },
            )
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
