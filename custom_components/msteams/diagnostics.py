"""Diagnostics support for Microsoft Teams integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_BROWSER_DAEMON_SECRET, CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import TeamsDataUpdateCoordinator

REDACTED = "**REDACTED**"


def _redact_data(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from diagnostic data."""
    redacted = dict(data)
    for key in (
        CONF_REFRESH_TOKEN,
        CONF_BROWSER_DAEMON_SECRET,
        "skype_token",
        "spaces_token",
        "substrate_token",
        "csa_token",
    ):
        if key in redacted and redacted[key]:
            redacted[key] = REDACTED
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: TeamsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    diag_data: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": _redact_data(dict(entry.data)),
            "options": dict(entry.options),
        },
        "auth": {
            "is_authenticated": coordinator.client.auth.is_authenticated,
            "has_refresh_token": bool(coordinator.client.auth.refresh_token),
            "tokens_expires_at": coordinator.client.auth.tokens_expires_at.isoformat()
            if coordinator.client.auth.tokens_expires_at
            else None,
        },
        "coordinator_data": coordinator.data or {},
    }
    return diag_data
