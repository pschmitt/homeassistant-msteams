"""Base entity for Microsoft Teams integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TeamsDataUpdateCoordinator


class TeamsEntity(CoordinatorEntity[TeamsDataUpdateCoordinator]):
    """Base class for all Microsoft Teams entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TeamsDataUpdateCoordinator,
        entity_key: str,
    ) -> None:
        """Initialize the Teams entity."""
        super().__init__(coordinator)
        self.entity_key = entity_key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{entity_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for this Teams account."""
        display_name = (
            (self.coordinator.data or {}).get("user_display_name")
            or self.coordinator.entry.title
            or "Microsoft Teams"
        )
        email = (self.coordinator.data or {}).get("user_email")
        model = f"Teams User ({email})" if email else "Teams Account"

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name=f"Microsoft Teams ({display_name})",
            manufacturer="Microsoft",
            model=model,
            entry_type=DeviceEntryType.SERVICE,
        )
