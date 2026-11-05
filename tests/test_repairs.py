"""Authentication repair respects the configured credential source."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.msteams.repairs import TeamsAuthRepairFlow


def repair(data, options=None):
    flow = TeamsAuthRepairFlow("entry")
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        data=data, options=options or {}
    )
    flow.hass.config_entries.async_reload = AsyncMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_show_form = MagicMock(side_effect=lambda **kwargs: kwargs)
    return flow


@pytest.mark.asyncio
async def test_file_repair_never_uses_browser():
    flow = repair({"use_browser_daemon": False, "credentials_file": "/config/teams.json"})
    auth = MagicMock(user_mri="account")
    auth.export_data.return_value = {}
    with (
        patch(
            "custom_components.msteams.repairs.TeamsAuth.async_from_credentials",
            new_callable=AsyncMock,
            return_value=auth,
        ) as credentials,
        patch(
            "custom_components.msteams.repairs.TeamsAuth.async_from_browser_daemon",
            new_callable=AsyncMock,
        ) as browser,
    ):
        result = await flow.async_step_reauth({})
    assert result["type"] == "create_entry"
    assert credentials.call_args.args[1] == "/config/teams.json"
    browser.assert_not_called()


@pytest.mark.asyncio
async def test_browser_repair_preserves_throwaway_and_group():
    flow = repair(
        {"use_browser_daemon": True, "browser_daemon_profile": "old"},
        {"browser_daemon_profile": "", "browser_daemon_node_group": "workers"},
    )
    auth = MagicMock(user_mri="account")
    auth.export_data.return_value = {}
    with patch(
        "custom_components.msteams.repairs.TeamsAuth.async_from_browser_daemon",
        new_callable=AsyncMock,
        return_value=auth,
    ) as browser:
        await flow.async_step_reauth({})
    assert browser.call_args.kwargs["profile"] == ""
    assert browser.call_args.kwargs["node_group"] == "workers"


@pytest.mark.asyncio
@pytest.mark.parametrize("browser", [False, True])
async def test_repair_schema_only_exposes_current_method_without_saved_secrets(browser):
    flow = repair({"use_browser_daemon": browser, "browser_daemon_secret": "saved"})
    result = await flow.async_step_reauth()
    validated = result["data_schema"]({})
    assert "browser_daemon_secret" not in validated
    assert "refresh_token" not in validated
    assert ("browser_daemon_profile" in validated) is browser
    assert ("credentials_file" in validated) is not browser
