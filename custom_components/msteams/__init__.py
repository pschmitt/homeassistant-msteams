"""The Microsoft Teams integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TeamsApiClient, TeamsAuth
from .const import (
    CONF_CLIENT_ID,
    CONF_REFRESH_TOKEN,
    CONF_TENANT_ID,
    CONF_USER_DISPLAY_NAME,
    CONF_USER_EMAIL,
    CONF_USER_MRI,
    DOMAIN,
    PLATFORMS,
    TEAMS_CLIENT_ID,
)
from .coordinator import TeamsDataUpdateCoordinator
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Microsoft Teams integration from YAML (services only)."""
    hass.data.setdefault(DOMAIN, {})
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Microsoft Teams from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    session = async_get_clientsession(hass)

    # Reconstruct Auth object from stored entry data
    auth = TeamsAuth(
        session=session,
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        tenant_id=entry.data.get(CONF_TENANT_ID, "common"),
        client_id=entry.data.get(CONF_CLIENT_ID, TEAMS_CLIENT_ID),
        user_mri=entry.data.get(CONF_USER_MRI),
        user_display_name=entry.data.get(CONF_USER_DISPLAY_NAME),
        user_email=entry.data.get(CONF_USER_EMAIL),
    )

    client = TeamsApiClient(auth=auth)

    coordinator = TeamsDataUpdateCoordinator(hass, entry, client)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Forward entry setups to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Per AGENTS.md guidelines: never block async_setup_entry on initial network refresh!
    # Run the initial refresh in the background.
    entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        f"{DOMAIN}_{entry.entry_id}_refresh",
    )

    current_options = dict(entry.options)

    async def async_options_updated(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        nonlocal current_options
        if updated_entry.options != current_options:
            current_options = dict(updated_entry.options)
            await hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    if not hass.data[DOMAIN]:
        await async_unload_services(hass)

    return unload_ok
