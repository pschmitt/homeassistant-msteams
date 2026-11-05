from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.msteams import async_setup_entry, async_unload_entry
from custom_components.msteams.const import CONF_ENABLE_PUSH, DOMAIN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options,push_enabled",
    [({}, True), ({CONF_ENABLE_PUSH: True}, True), ({CONF_ENABLE_PUSH: False}, False)],
)
async def test_setup_push_option(options, push_enabled):
    hass = MagicMock(data={})
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    entry = MagicMock(entry_id="test", data={}, options=options)
    coordinator = MagicMock()
    coordinator.async_refresh = AsyncMock()
    coordinator.async_stop_push = AsyncMock()

    with patch("custom_components.msteams.TeamsDataUpdateCoordinator", return_value=coordinator):
        assert await async_setup_entry(hass, entry)

    startup = entry.async_create_background_task.call_args.args[1]
    await startup
    coordinator.async_refresh.assert_awaited_once()
    assert coordinator.trouter.start.call_count == int(push_enabled)
    assert hass.data[DOMAIN][entry.entry_id] is coordinator

    with patch("custom_components.msteams.async_unload_services", new_callable=AsyncMock):
        assert await async_unload_entry(hass, entry)
    coordinator.async_stop_push.assert_awaited_once()
