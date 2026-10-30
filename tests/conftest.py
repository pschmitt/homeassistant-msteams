"""Test fixtures and lightweight Home Assistant stubs for unit testing."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

# Stub homeassistant modules if homeassistant is not installed in test runner
if "homeassistant" not in sys.modules:
    ha = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha

    # const
    const = types.ModuleType("homeassistant.const")

    class Platform:
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        EVENT = "event"
        NOTIFY = "notify"
        BUTTON = "button"

    const.Platform = Platform
    sys.modules["homeassistant.const"] = const
    ha.const = const

    # core
    core = types.ModuleType("homeassistant.core")

    def callback(func):
        return func

    core.callback = callback
    core.HomeAssistant = MagicMock
    core.ServiceCall = MagicMock
    sys.modules["homeassistant.core"] = core
    ha.core = core

    # exceptions
    exceptions = types.ModuleType("homeassistant.exceptions")

    class ConfigEntryAuthFailed(Exception):
        pass

    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    sys.modules["homeassistant.exceptions"] = exceptions
    ha.exceptions = exceptions

    # config_entries
    config_entries = types.ModuleType("homeassistant.config_entries")

    class ConfigEntry:
        pass

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.DOMAIN = domain

        async def async_set_unique_id(self, unique_id):
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            pass

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(self, step_id, data_schema=None, errors=None):
            return {"type": "form", "step_id": step_id, "errors": errors}

        def async_abort(self, reason):
            return {"type": "abort", "reason": reason}

    class OptionsFlow:
        @property
        def config_entry(self):
            return self.hass.config_entries.async_get_known_entry(self.handler)

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(self, step_id, data_schema=None, errors=None):
            return {"type": "form", "step_id": step_id, "errors": errors}

    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow
    sys.modules["homeassistant.config_entries"] = config_entries
    ha.config_entries = config_entries

    # data_entry_flow
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")

    class AbortFlow(Exception):
        pass

    data_entry_flow.AbortFlow = AbortFlow
    data_entry_flow.FlowResult = dict
    sys.modules["homeassistant.data_entry_flow"] = data_entry_flow
    ha.data_entry_flow = data_entry_flow

    # helpers
    helpers = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = helpers
    ha.helpers = helpers

    # helpers.update_coordinator
    import typing

    T = typing.TypeVar("T")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator[T]:
        def __init__(
            self, hass, logger, *, name=None, update_interval=None, config_entry=None, **kwargs
        ):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.config_entry = config_entry
            self.data = None
            self.last_update_success = True

        async def async_request_refresh(self):
            pass

        async def async_refresh(self):
            pass

    class CoordinatorEntity[T]:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self) -> bool:
            return self.coordinator.last_update_success

        async def async_added_to_hass(self) -> None:
            pass

        async def async_will_remove_from_hass(self) -> None:
            pass

    update_coordinator.UpdateFailed = UpdateFailed
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = update_coordinator
    helpers.update_coordinator = update_coordinator

    # helpers.device_registry
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")

    class DeviceEntryType:
        SERVICE = "service"

    class DeviceInfo:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    device_registry.DeviceEntryType = DeviceEntryType
    device_registry.DeviceInfo = DeviceInfo
    sys.modules["homeassistant.helpers.device_registry"] = device_registry
    helpers.device_registry = device_registry

    # helpers.entity_platform
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = MagicMock
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    helpers.entity_platform = entity_platform

    # helpers.issue_registry
    issue_registry = types.ModuleType("homeassistant.helpers.issue_registry")

    class IssueSeverity:
        ERROR = "error"
        WARNING = "warning"

    issue_registry.IssueSeverity = IssueSeverity
    issue_registry.async_create_issue = MagicMock()
    issue_registry.async_delete_issue = MagicMock()
    sys.modules["homeassistant.helpers.issue_registry"] = issue_registry
    helpers.issue_registry = issue_registry

    # helpers.aiohttp_client
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = MagicMock()
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client
    helpers.aiohttp_client = aiohttp_client

    # helpers.config_validation
    config_validation = types.ModuleType("homeassistant.helpers.config_validation")
    config_validation.string = str
    config_validation.boolean = bool
    config_validation.empty_config_schema = lambda domain: lambda x: x
    config_validation.config_entry_only_config_schema = lambda domain: lambda x: x
    sys.modules["homeassistant.helpers.config_validation"] = config_validation
    helpers.config_validation = config_validation

    # components
    components = types.ModuleType("homeassistant.components")
    sys.modules["homeassistant.components"] = components
    ha.components = components

    # components.sensor
    sensor = types.ModuleType("homeassistant.components.sensor")

    class SensorEntity:
        pass

    sensor.SensorEntity = SensorEntity
    sys.modules["homeassistant.components.sensor"] = sensor
    components.sensor = sensor

    # components.binary_sensor
    binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass:
        CONNECTIVITY = "connectivity"

    class BinarySensorEntity:
        pass

    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor.BinarySensorEntity = BinarySensorEntity
    sys.modules["homeassistant.components.binary_sensor"] = binary_sensor
    components.binary_sensor = binary_sensor

    # components.event
    event = types.ModuleType("homeassistant.components.event")

    class EventEntity:
        def _trigger_event(self, event_type, event_data=None):
            self._last_event_type = event_type
            self._last_event_data = event_data

        def async_write_ha_state(self):
            pass

    event.EventEntity = EventEntity
    sys.modules["homeassistant.components.event"] = event
    components.event = event

    # components.notify
    notify = types.ModuleType("homeassistant.components.notify")

    class NotifyEntity:
        pass

    notify.NotifyEntity = NotifyEntity
    sys.modules["homeassistant.components.notify"] = notify
    components.notify = notify

    # components.button
    button = types.ModuleType("homeassistant.components.button")

    class ButtonEntity:
        pass

    button.ButtonEntity = ButtonEntity
    sys.modules["homeassistant.components.button"] = button
    components.button = button

    # components.repairs
    repairs = types.ModuleType("homeassistant.components.repairs")

    class RepairsFlow:
        pass

    repairs.RepairsFlow = RepairsFlow
    sys.modules["homeassistant.components.repairs"] = repairs
    components.repairs = repairs
