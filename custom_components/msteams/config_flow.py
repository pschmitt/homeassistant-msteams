"""Config flow for Microsoft Teams integration."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow, FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
from .setup_form import (
    CREDENTIALS_PROVIDER,
    PROVIDER_RBW,
    SECTION_BROWSER_DAEMON,
    SECTION_RBW,
    browser_daemon_fields,
    choice,
    credentials_provider_field,
    flatten_sections,
    rbw_fields,
    rbw_item_options,
    resolve_credentials,
    section,
)

_LOGGER = logging.getLogger(__name__)


SECTION_IMPORT = "import"
SIGN_IN_METHOD = "sign_in_method"
METHOD_BROWSER = "browser_daemon"
METHOD_IMPORT = "import"


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


def setup_schema(
    hass: Any,
    defaults: dict[str, Any],
    *,
    use_default: bool,
    manual_saved: bool = False,
    items: list[Any] | None = None,
    own: dict[Any, Any] | None = None,
) -> vol.Schema:
    """The Teams fields, then how to sign in, which decides what else shows."""
    browser = {"field": SIGN_IN_METHOD, "value": METHOD_BROWSER}
    return vol.Schema(
        {
            **(own or {}),
            vol.Optional(
                SIGN_IN_METHOD, default=METHOD_BROWSER if use_default else METHOD_IMPORT
            ): choice([METHOD_BROWSER, METHOD_IMPORT], SIGN_IN_METHOD),
            **section(
                SECTION_BROWSER_DAEMON,
                browser_daemon_fields(hass, defaults, profile=DEFAULT_PROFILE),
                visible=browser,
            ),
            **credentials_provider_field(
                hass, defaults, manual_saved=manual_saved, visible=browser
            ),
            **section(
                SECTION_RBW,
                {
                    **rbw_fields(hass, defaults, items or []),
                    vol.Optional(
                        CONF_PREFER_PASSKEY, default=defaults.get(CONF_PREFER_PASSKEY, True)
                    ): bool,
                },
                visible=[browser, {"field": CREDENTIALS_PROVIDER, "value": PROVIDER_RBW}],
            ),
            **section(
                SECTION_IMPORT,
                {
                    vol.Optional(
                        CONF_CREDENTIALS_FILE, default=defaults.get(CONF_CREDENTIALS_FILE, "")
                    ): str,
                    vol.Optional(CONF_REFRESH_TOKEN): str,
                    vol.Optional(
                        CONF_TENANT_ID, default=defaults.get(CONF_TENANT_ID, "common")
                    ): str,
                },
                visible={"field": SIGN_IN_METHOD, "value": METHOD_IMPORT},
            ),
        }
    )


def read_form(user_input: dict[str, Any]) -> dict[str, Any]:
    """The submitted values without sections, and the sign-in method as it is saved."""
    form = flatten_sections(user_input)
    method = form.pop(SIGN_IN_METHOD, None)
    if method:
        form[CONF_USE_BROWSER_DAEMON] = method == METHOD_BROWSER
    return form


def rbw_error(data: dict[str, Any]) -> str | None:
    """rbw signs in through the browser, so it needs Browser Daemon, and an item."""
    if data.get(CONF_RBW_ENTRY_ID) and not data.get(CONF_USE_BROWSER_DAEMON):
        return "rbw_needs_browser_daemon"
    return None if rbw_config_valid(data) else "rbw_incomplete"


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

        rbw_invalid = None
        form_values: dict[str, Any] = {}
        if user_input is not None:
            form_values = read_form(user_input)
            user_input, rbw_invalid = resolve_credentials(form_values)
            rbw_invalid = rbw_invalid or rbw_error(user_input)
            if rbw_invalid:
                errors["base"] = rbw_invalid
        if user_input is not None and not rbw_invalid:
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

        return self.async_show_form(
            step_id="user",
            data_schema=setup_schema(
                self.hass,
                form_values,
                use_default=form_values.get(CONF_USE_BROWSER_DAEMON, has_browser_daemon),
                items=await rbw_item_options(self.hass, form_values),
                own={
                    vol.Optional(CONF_USER_DISPLAY_NAME): str,
                    vol.Optional(CONF_DEFAULT_CONVERSATION_ID): str,
                },
            ),
            errors=errors,
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
        rbw_invalid = None
        form_values = read_form(user_input) if user_input is not None else {}
        defaults = {**effective_settings, **form_values}
        if user_input is not None:
            user_input, rbw_invalid = resolve_credentials(form_values)
            rbw_invalid = rbw_invalid or rbw_error({**effective_settings, **user_input})
            if rbw_invalid:
                errors["base"] = rbw_invalid
        submitted_settings = {**effective_settings, **(user_input or {})}
        use_browser_daemon = submitted_settings.get(CONF_USE_BROWSER_DAEMON, False)
        if user_input is not None and not rbw_invalid:
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

        return self.async_show_form(
            step_id=step_id,
            data_schema=setup_schema(
                self.hass,
                defaults,
                use_default=defaults.get(CONF_USE_BROWSER_DAEMON, False),
                manual_saved=True,
                items=await rbw_item_options(self.hass, defaults),
            ),
            errors=errors,
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
            # Sign-in settings are in the reconfigure dialog: keep what is saved here.
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )

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
        return self.async_show_form(step_id="init", data_schema=schema)
