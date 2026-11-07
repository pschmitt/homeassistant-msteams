"""Unit tests for config flow and options flow."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import probatio as vol
import pytest

from custom_components.msteams.api import TeamsAuthError
from custom_components.msteams.config_flow import (
    TeamsConfigFlow,
    TeamsOptionsFlow,
    rbw_schema,
)
from custom_components.msteams.const import (
    CONF_BROWSER_DAEMON_NODE_GROUP,
    CONF_BROWSER_DAEMON_PROFILE,
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
    CONF_USER_MRI,
)


@pytest.mark.asyncio
async def test_browser_fields_have_native_visibility_and_named_default():
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.components = {"browser_daemon"}
    flow.hass.config_entries.async_entries.return_value = []
    result = await flow.async_step_user()
    markers = {key.schema: key for key in result["data_schema"].schema}
    assert markers[CONF_BROWSER_DAEMON_PROFILE].default() == "ha-msteams"
    assert markers[CONF_BROWSER_DAEMON_PROFILE].visible == {
        "field": CONF_USE_BROWSER_DAEMON,
        "value": True,
    }
    assert markers[CONF_CREDENTIALS_FILE].visible == {
        "field": CONF_USE_BROWSER_DAEMON,
        "operator": "not_eq",
        "value": True,
    }
    assert CONF_RBW_ITEM_ID not in markers
    assert markers[CONF_PREFER_PASSKEY].visible == [
        {"field": CONF_USE_BROWSER_DAEMON, "value": True},
        {"field": CONF_RBW_ENTRY_ID, "operator": "exists"},
    ]


@pytest.mark.parametrize("browser_only", [False, True])
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({}, None),
        ({CONF_RBW_ENTRY_ID: ""}, None),
        ({CONF_RBW_ENTRY_ID: None}, None),
        ({CONF_RBW_ENTRY_ID: "saved-vault"}, "saved-vault"),
    ],
)
def test_rbw_picker_only_suggests_a_saved_vault(browser_only, stored, expected):
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [MagicMock(entry_id="vault")]
    schema = vol.Schema(
        rbw_schema(hass, {CONF_USE_BROWSER_DAEMON: True, **stored}, browser_only=browser_only)
    )
    markers = {key.schema: key for key in schema.schema}
    assert markers[CONF_RBW_ENTRY_ID].description == {"suggested_value": expected}
    # Clearing the picker must not bring a saved vault back.
    assert CONF_RBW_ENTRY_ID not in schema({})
    selected = {"field": CONF_RBW_ENTRY_ID, "operator": "exists"}
    conditions = [selected]
    if browser_only:
        conditions.insert(0, {"field": CONF_USE_BROWSER_DAEMON, "value": True})
    assert markers[CONF_PREFER_PASSKEY].visible == conditions


def test_rbw_picker_needs_an_rbw_integration():
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []
    assert CONF_RBW_ENTRY_ID not in {
        str(key) for key in rbw_schema(hass, {CONF_USE_BROWSER_DAEMON: True})
    }


def test_native_rbw_visibility_serializes_and_strips_hidden_fields():
    """Exercise real HA when preloaded instead of the lightweight test stubs."""
    from homeassistant import data_entry_flow
    from homeassistant.helpers import config_validation as cv

    if not hasattr(data_entry_flow, "_strip_hidden_fields"):
        pytest.skip("Requires real Home Assistant to be imported before tests/conftest.py")
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [MagicMock(entry_id="vault", title="Vault")]
    schema = vol.Schema(
        {
            vol.Optional(CONF_USE_BROWSER_DAEMON, default=True): bool,
            **rbw_schema(hass, {}, browser_only=True),
        }
    )
    fields = vol.to_field_list(schema, custom_serializer=cv.custom_serializer)
    data_entry_flow.add_visible_conditions_to_serialized_schema(schema, fields)
    passkey = next(field for field in fields if field["name"] == CONF_PREFER_PASSKEY)
    assert passkey["visible"] == [
        {"field": CONF_USE_BROWSER_DAEMON, "value": True},
        {"field": CONF_RBW_ENTRY_ID, "operator": "exists"},
    ]
    for browser, selected, expected_hidden in (
        (True, "", {CONF_PREFER_PASSKEY}),
        (False, "vault", {CONF_RBW_ENTRY_ID, CONF_PREFER_PASSKEY}),
        (True, "vault", set()),
    ):
        visible_schema, submitted = data_entry_flow._strip_hidden_fields(
            schema,
            {
                CONF_USE_BROWSER_DAEMON: browser,
                CONF_RBW_ENTRY_ID: selected,
                CONF_PREFER_PASSKEY: False,
            },
        )
        validated = visible_schema(submitted)
        assert not expected_hidden.intersection(validated)
        if browser and selected == "vault":
            assert validated[CONF_PREFER_PASSKEY] is False


@pytest.mark.parametrize("cleared", [{}, {CONF_RBW_ENTRY_ID: ""}])
@pytest.mark.parametrize("hidden", [{}, {CONF_RBW_ITEM_ID: "stale-item"}])
@pytest.mark.asyncio
async def test_user_normalizes_cleared_picker_before_authentication(cleared, hidden):
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.context = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_set_unique_id = AsyncMock()
    auth = MagicMock()
    auth.user_mri = "8:orgid:alice"
    auth.export_data.return_value = {CONF_USER_MRI: auth.user_mri}
    supplied = {CONF_USE_BROWSER_DAEMON: True, **cleared, **hidden}
    with patch(
        "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
        new=AsyncMock(return_value=auth),
    ) as authenticate:
        result = await flow.async_step_user(supplied)
    assert result["type"] == "create_entry"
    assert authenticate.await_args.kwargs[CONF_RBW_ENTRY_ID] == ""
    assert authenticate.await_args.kwargs[CONF_RBW_ITEM_ID] == ""
    assert result["data"][CONF_RBW_ENTRY_ID] == ""
    assert result["data"][CONF_RBW_ITEM_ID] == ""
    assert supplied.get(CONF_RBW_ENTRY_ID) == cleared.get(CONF_RBW_ENTRY_ID)


@pytest.mark.parametrize("cleared", [{}, {CONF_RBW_ENTRY_ID: ""}])
@pytest.mark.parametrize("hidden", [{}, {CONF_RBW_ITEM_ID: "stale-item"}])
@pytest.mark.asyncio
async def test_options_normalizes_cleared_picker_and_clears_old_item(cleared, hidden):
    entry = MagicMock()
    entry.data = {CONF_USE_BROWSER_DAEMON: True}
    entry.options = {CONF_RBW_ENTRY_ID: "old-vault", CONF_RBW_ITEM_ID: "old-item"}
    flow = TeamsOptionsFlow()
    flow.handler = "test_entry"
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_known_entry.return_value = entry
    supplied = {**cleared, **hidden}
    result = await flow.async_step_init(supplied)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_RBW_ENTRY_ID] == ""
    assert result["data"][CONF_RBW_ITEM_ID] == ""
    assert supplied.get(CONF_RBW_ENTRY_ID) == cleared.get(CONF_RBW_ENTRY_ID)


@pytest.mark.asyncio
async def test_config_flow_manual_success():
    """Test successful configuration via manual refresh token."""
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.context = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_set_unique_id = AsyncMock()

    with patch(
        "custom_components.msteams.config_flow.TeamsAuth.async_refresh_tokens",
        new=AsyncMock(return_value=True),
    ):
        result = await flow.async_step_user(
            {
                CONF_REFRESH_TOKEN: "valid_token_123",
                CONF_TENANT_ID: "tenant-uuid",
            }
        )

    assert result["type"] == "create_entry"
    assert result["data"]["refresh_token"] == "valid_token_123"


@pytest.mark.parametrize("rbw", [{}, {CONF_RBW_ENTRY_ID: "unused-vault"}])
@pytest.mark.asyncio
async def test_config_flow_credentials_file_success(rbw):
    """Test successful configuration via credentials file."""
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.context = {}
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_set_unique_id = AsyncMock()

    mock_auth = MagicMock()
    mock_auth.user_mri = "8:orgid:alice"
    mock_auth.user_display_name = "Alice Smith"
    mock_auth.user_email = "alice@example.com"
    mock_auth.export_data.return_value = {
        "refresh_token": "token123",
        "tenant_id": "tenant123",
        "client_id": "client123",
        "user_mri": "8:orgid:alice",
        "user_display_name": "Alice Smith",
        "user_email": "alice@example.com",
    }

    with patch(
        "custom_components.msteams.config_flow.TeamsAuth.async_from_credentials",
        new=AsyncMock(return_value=mock_auth),
    ):
        result = await flow.async_step_user(
            {
                CONF_CREDENTIALS_FILE: "/tmp/fake_creds.json",
                CONF_USE_BROWSER_DAEMON: False,
                **rbw,
            }
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "Alice Smith"
    assert result["data"]["refresh_token"] == "token123"
    assert result["data"][CONF_USE_BROWSER_DAEMON] is False


@pytest.mark.asyncio
async def test_options_flow():
    """Test updating options."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {CONF_POLL_INTERVAL: 30}
    entry.options = {}

    options_flow = TeamsOptionsFlow()
    options_flow.handler = "test_entry"
    hass = MagicMock()
    hass.config_entries.async_get_known_entry.return_value = entry
    options_flow.hass = hass

    # Show form
    form = await options_flow.async_step_init()
    assert form["type"] == "form"

    # Submit form
    res = await options_flow.async_step_init(
        {
            CONF_POLL_INTERVAL: 45,
            CONF_ENABLE_PUSH: False,
            CONF_DEFAULT_CONVERSATION_ID: "19:conv456",
        }
    )
    assert res["type"] == "create_entry"
    assert res["data"][CONF_POLL_INTERVAL] == 45
    assert res["data"][CONF_ENABLE_PUSH] is False


@pytest.mark.parametrize("returned_prefix", ["8:orgid:", "orgid:"])
@pytest.mark.asyncio
async def test_reauth_persists_submitted_rbw_settings_to_options(returned_prefix):
    """Reauth settings override prior option values used during setup."""
    object_id = "b1c24a5e-7834-4b4c-b523-2fd943719a11"
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {CONF_USER_MRI: object_id, CONF_RBW_ENTRY_ID: "old_entry"}
    entry.options = {
        CONF_RBW_ENTRY_ID: "old_entry",
        CONF_RBW_ITEM_ID: "old_item",
        CONF_PREFER_PASSKEY: True,
    }

    flow = TeamsConfigFlow()
    flow.reauth_entry = entry
    flow.hass = MagicMock()
    flow.hass.config.components = set()
    flow.hass.data = {}
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.hass.config_entries.async_reload = AsyncMock()

    auth = MagicMock()
    auth.user_mri = f"{returned_prefix}{object_id}"
    auth.export_data.return_value = {
        CONF_USER_MRI: f"{returned_prefix}{object_id}",
        "refresh_token": "new",
    }
    user_input = {
        CONF_USE_BROWSER_DAEMON: True,
        CONF_BROWSER_DAEMON_PROFILE: "work",
        CONF_BROWSER_DAEMON_NODE_GROUP: "cloud",
        CONF_RBW_ENTRY_ID: "new_entry",
        CONF_RBW_ITEM_ID: "new_item",
        CONF_PREFER_PASSKEY: False,
    }

    with (
        patch(
            "custom_components.msteams.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(return_value=auth),
        ) as auth_from_browser,
    ):
        result = await flow.async_step_reauth_confirm(user_input)

    assert result["type"] == "abort"
    auth_from_browser.assert_awaited_once_with(
        session=auth_from_browser.await_args.kwargs["session"],
        browser_daemon_entry_id="",
        browser_daemon_url="",
        secret=None,
        profile="work",
        node_group="cloud",
        hass=flow.hass,
        rbw_entry_id="new_entry",
        rbw_item_id="new_item",
        prefer_passkey=False,
    )
    flow.hass.config_entries.async_update_entry.assert_called_once()
    assert flow.hass.config_entries.async_update_entry.call_args.kwargs["options"] == {
        CONF_RBW_ENTRY_ID: "new_entry",
        CONF_RBW_ITEM_ID: "new_item",
        CONF_PREFER_PASSKEY: False,
    }


@pytest.mark.asyncio
async def test_reauth_rejects_different_account():
    """A reauth must stay bound to the account already configured."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {CONF_USER_MRI: "8:orgid:alice"}
    entry.options = {}

    flow = TeamsConfigFlow()
    flow.reauth_entry = entry
    flow.hass = MagicMock()
    flow.hass.config.components = set()
    flow.hass.data = {}
    flow.hass.config_entries.async_update_entry = MagicMock()

    auth = MagicMock()
    auth.user_mri = "8:orgid:bob"
    user_input = {CONF_USE_BROWSER_DAEMON: True}

    with (
        patch(
            "custom_components.msteams.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(return_value=auth),
        ),
    ):
        result = await flow.async_step_reauth_confirm(user_input)

    assert result["type"] == "form"
    assert result["errors"] == {"base": "auth_failed"}
    flow.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_choosing_vault_asks_for_the_item_by_name(monkeypatch):
    listed = AsyncMock(
        return_value=[
            {"id": "b-id", "name": "zeta", "fields": []},
            {"id": "a-id", "name": "Alpha", "fields": []},
            {"id": "locked-id", "name": None, "fields": []},
        ]
    )
    monkeypatch.setitem(
        sys.modules, "custom_components.rbw", types.SimpleNamespace(async_list_items=listed)
    )
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.components = set()
    flow.hass.data = {}
    result = await flow.async_step_user(
        {CONF_RBW_ENTRY_ID: "chosen_vault", CONF_USE_BROWSER_DAEMON: True}
    )
    assert result["step_id"] == "rbw_item"
    listed.assert_awaited_once_with(flow.hass, "chosen_vault")
    marker, selector = next(iter(result["data_schema"].schema.items()))
    assert str(marker) == CONF_RBW_ITEM_ID
    assert selector.config["custom_value"] is True
    assert [(o["value"], o["label"]) for o in selector.config["options"]] == [
        ("a-id", "Alpha"),
        ("locked-id", "locked-id"),
        ("b-id", "zeta"),
    ]


@pytest.mark.asyncio
async def test_item_step_takes_a_typed_id_when_rbw_is_unavailable(monkeypatch):
    from homeassistant.exceptions import HomeAssistantError

    monkeypatch.setitem(
        sys.modules,
        "custom_components.rbw",
        types.SimpleNamespace(async_list_items=AsyncMock(side_effect=HomeAssistantError("off"))),
    )
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.components = set()
    flow.hass.data = {}
    result = await flow.async_step_user(
        {CONF_RBW_ENTRY_ID: "chosen_vault", CONF_USE_BROWSER_DAEMON: True}
    )
    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["options"] == []
    assert selector.config["custom_value"] is True


@pytest.mark.parametrize(
    ("chosen", "expected"), [("saved_vault", "saved_item"), ("other_vault", "only")]
)
@pytest.mark.asyncio
async def test_options_item_step_preselects_and_returns_to_the_form(monkeypatch, chosen, expected):
    monkeypatch.setitem(
        sys.modules,
        "custom_components.rbw",
        types.SimpleNamespace(
            async_list_items=AsyncMock(return_value=[{"id": "only", "name": "Only", "fields": []}])
        ),
    )
    entry = MagicMock()
    entry.data = {CONF_USE_BROWSER_DAEMON: True}
    entry.options = {CONF_RBW_ENTRY_ID: "saved_vault", CONF_RBW_ITEM_ID: "saved_item"}
    flow = TeamsOptionsFlow()
    flow.handler = "test_entry"
    flow.hass = MagicMock()
    flow.hass.config_entries.async_get_known_entry.return_value = entry
    form = await flow.async_step_init({CONF_RBW_ENTRY_ID: chosen, CONF_ENABLE_PUSH: False})
    assert form["step_id"] == "rbw_item"
    assert next(iter(form["data_schema"].schema)).default() == expected
    result = await flow.async_step_rbw_item({CONF_RBW_ITEM_ID: expected})
    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_RBW_ENTRY_ID: chosen,
        CONF_RBW_ITEM_ID: expected,
        CONF_ENABLE_PUSH: False,
    }


@pytest.mark.asyncio
async def test_options_blank_item_keeps_the_chosen_vault():
    entry = MagicMock()
    entry.data = {CONF_USE_BROWSER_DAEMON: True}
    entry.options = {}
    options_flow = TeamsOptionsFlow()
    options_flow.handler = "test_entry"
    options_flow.hass = MagicMock()
    options_flow.hass.config_entries.async_get_known_entry.return_value = entry

    result = await options_flow.async_step_init(
        {CONF_RBW_ENTRY_ID: "chosen_vault", CONF_RBW_ITEM_ID: ""}
    )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "rbw_incomplete"}
    assert _suggested_vault(result) == "chosen_vault"


@pytest.mark.asyncio
async def test_reauth_item_step_keeps_effective_existing_settings():
    """With the vault still chosen, the saved item is preselected and details are kept."""
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {CONF_USER_MRI: "8:orgid:alice", CONF_RBW_ENTRY_ID: "stored_entry"}
    entry.options = {
        CONF_RBW_ENTRY_ID: "configured_entry",
        CONF_RBW_ITEM_ID: "configured_item",
        CONF_PREFER_PASSKEY: False,
    }

    flow = TeamsConfigFlow()
    flow.reauth_entry = entry
    flow.hass = MagicMock()
    flow.hass.config.components = set()
    flow.hass.data = {}
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.hass.config_entries.async_reload = AsyncMock()

    auth = MagicMock()
    auth.user_mri = "8:orgid:alice"
    auth.export_data.return_value = {CONF_USER_MRI: "8:orgid:alice"}

    with (
        patch(
            "custom_components.msteams.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(return_value=auth),
        ) as auth_from_browser,
    ):
        form = await flow.async_step_reauth_confirm(
            {CONF_USE_BROWSER_DAEMON: True, CONF_RBW_ENTRY_ID: "configured_entry"}
        )
        marker = next(iter(form["data_schema"].schema))
        assert marker.default() == "configured_item"
        await flow.async_step_rbw_item({CONF_RBW_ITEM_ID: "configured_item"})

    assert auth_from_browser.await_args.kwargs["rbw_entry_id"] == "configured_entry"
    assert auth_from_browser.await_args.kwargs["rbw_item_id"] == "configured_item"
    assert auth_from_browser.await_args.kwargs["prefer_passkey"] is False


def _suggested_vault(result):
    markers = {key.schema: key for key in result["data_schema"].schema}
    return markers[CONF_RBW_ENTRY_ID].description["suggested_value"]


def reconfigure_setup(entry):
    flow = TeamsConfigFlow()
    flow.context = {"entry_id": entry.entry_id}
    flow.hass = MagicMock()
    flow.hass.config.components = set()
    flow.hass.data = {}
    flow.hass.config_entries.async_get_entry.return_value = entry
    rbw_entry_id = entry.options.get(CONF_RBW_ENTRY_ID)
    rbw_entries = []
    if rbw_entry_id:
        rbw_entry = MagicMock()
        rbw_entry.entry_id = rbw_entry_id
        rbw_entry.title = "rbw"
        rbw_entries.append(rbw_entry)
    flow.hass.config_entries.async_entries.return_value = rbw_entries
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.hass.config_entries.async_reload = AsyncMock()
    return flow


@pytest.mark.parametrize("step", ["reconfigure", "reauth_confirm", "options"])
@pytest.mark.asyncio
async def test_existing_flow_shows_stored_empty_vault_as_no_vault(step):
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {
        CONF_USE_BROWSER_DAEMON: True,
        CONF_RBW_ENTRY_ID: "old-vault",
        CONF_RBW_ITEM_ID: "old-item",
    }
    entry.options = {CONF_RBW_ENTRY_ID: "", CONF_RBW_ITEM_ID: ""}
    if step == "options":
        flow = TeamsOptionsFlow()
        flow.handler = entry.entry_id
        flow.hass = MagicMock()
        flow.hass.config_entries.async_get_known_entry.return_value = entry
        result = await flow.async_step_init()
    else:
        flow = reconfigure_setup(entry)
        flow.reauth_entry = entry
        flow.hass.config_entries.async_entries.return_value = [MagicMock(entry_id="vault")]
        result = await getattr(flow, f"async_step_{step}")()
    markers = {key.schema: key for key in result["data_schema"].schema}
    assert markers[CONF_RBW_ENTRY_ID].description == {"suggested_value": None}
    assert CONF_RBW_ENTRY_ID not in result["data_schema"]({})
    assert entry.options[CONF_RBW_ENTRY_ID] == ""


@pytest.mark.asyncio
async def test_reconfigure_form_uses_effective_defaults_without_refresh_token():
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {
        CONF_USER_MRI: "8:orgid:alice",
        CONF_USE_BROWSER_DAEMON: False,
        CONF_BROWSER_DAEMON_PROFILE: "data_profile",
        CONF_TENANT_ID: "data_tenant",
        CONF_REFRESH_TOKEN: "stored-token",
    }
    entry.options = {
        CONF_USE_BROWSER_DAEMON: True,
        CONF_BROWSER_DAEMON_PROFILE: "options_profile",
        CONF_TENANT_ID: "options_tenant",
        CONF_RBW_ENTRY_ID: "rbw_entry",
        CONF_RBW_ITEM_ID: "rbw_item",
        CONF_PREFER_PASSKEY: False,
    }
    flow = reconfigure_setup(entry)

    result = await flow.async_step_reconfigure()

    defaults = result["data_schema"]({})
    assert result["step_id"] == "reconfigure"
    assert defaults[CONF_USE_BROWSER_DAEMON] is True
    assert defaults[CONF_BROWSER_DAEMON_PROFILE] == "options_profile"
    assert defaults[CONF_TENANT_ID] == "options_tenant"
    assert _suggested_vault(result) == "rbw_entry"
    assert defaults[CONF_PREFER_PASSKEY] is False
    assert CONF_REFRESH_TOKEN not in defaults


@pytest.mark.asyncio
async def test_reconfigure_updates_existing_entry_and_preserves_options():
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {
        CONF_USER_MRI: "8:orgid:alice",
        CONF_USE_BROWSER_DAEMON: True,
        CONF_BROWSER_DAEMON_PROFILE: "old_profile",
    }
    entry.options = {
        CONF_RBW_ENTRY_ID: "rbw_entry",
        CONF_RBW_ITEM_ID: "rbw_item",
        CONF_PREFER_PASSKEY: False,
        CONF_POLL_INTERVAL: 45,
        CONF_ENABLE_PUSH: False,
        CONF_DEFAULT_CONVERSATION_ID: "existing_chat",
    }
    flow = reconfigure_setup(entry)
    auth = MagicMock()
    auth.user_mri = "8:orgid:alice"
    auth.export_data.return_value = {CONF_USER_MRI: "8:orgid:alice", "refresh_token": "fresh"}
    user_input = {
        CONF_USE_BROWSER_DAEMON: True,
        CONF_BROWSER_DAEMON_PROFILE: "new_profile",
        CONF_TENANT_ID: "new_tenant",
        CONF_RBW_ENTRY_ID: "rbw_entry",
        CONF_RBW_ITEM_ID: "rbw_item",
    }

    with (
        patch(
            "custom_components.msteams.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(return_value=auth),
        ) as auth_from_browser,
    ):
        result = await flow.async_step_reconfigure(user_input)

    assert result == {"type": "abort", "reason": "reconfigure_successful"}
    assert auth_from_browser.await_args.kwargs["profile"] == "new_profile"
    assert auth_from_browser.await_args.kwargs["rbw_entry_id"] == "rbw_entry"
    assert auth_from_browser.await_args.kwargs["rbw_item_id"] == "rbw_item"
    assert auth_from_browser.await_args.kwargs["prefer_passkey"] is False
    flow.hass.config_entries.async_update_entry.assert_called_once()
    call = flow.hass.config_entries.async_update_entry.call_args
    assert call.args[0] is entry
    assert call.kwargs["data"][CONF_BROWSER_DAEMON_PROFILE] == "new_profile"
    assert call.kwargs["data"][CONF_TENANT_ID] == "new_tenant"
    assert call.kwargs["data"][CONF_USER_MRI] == "8:orgid:alice"
    assert call.kwargs["data"]["refresh_token"] == "fresh"
    assert call.kwargs["options"] == entry.options
    flow.hass.config_entries.async_reload.assert_awaited_once_with("existing_entry")


@pytest.mark.parametrize("returned_mri", ["8:orgid:bob", None])
@pytest.mark.asyncio
async def test_reconfigure_rejects_different_or_missing_account(returned_mri):
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {CONF_USER_MRI: "8:orgid:alice", CONF_USE_BROWSER_DAEMON: True}
    entry.options = {}
    flow = reconfigure_setup(entry)
    auth = MagicMock()
    auth.user_mri = returned_mri
    user_input = {CONF_USE_BROWSER_DAEMON: True}

    with (
        patch(
            "custom_components.msteams.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(return_value=auth),
        ),
    ):
        result = await flow.async_step_reconfigure(user_input)

    assert result["errors"] == {"base": "auth_failed"}
    flow.hass.config_entries.async_update_entry.assert_not_called()
    flow.hass.config_entries.async_reload.assert_not_awaited()


@pytest.mark.parametrize(
    ("auth_error", "expected_error"),
    [(TeamsAuthError("invalid"), "auth_failed"), (RuntimeError("offline"), "cannot_connect")],
)
@pytest.mark.asyncio
async def test_reconfigure_errors_keep_submitted_nonsecret_values(auth_error, expected_error):
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {CONF_USER_MRI: "8:orgid:alice"}
    entry.options = {}
    flow = reconfigure_setup(entry)
    user_input = {
        CONF_USE_BROWSER_DAEMON: True,
        CONF_BROWSER_DAEMON_PROFILE: "entered_profile",
        CONF_TENANT_ID: "entered_tenant",
    }

    with (
        patch(
            "custom_components.msteams.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(side_effect=auth_error),
        ),
    ):
        result = await flow.async_step_reconfigure(user_input)

    defaults = result["data_schema"]({})
    assert result["errors"] == {"base": expected_error}
    assert defaults[CONF_BROWSER_DAEMON_PROFILE] == "entered_profile"
    assert defaults[CONF_TENANT_ID] == "entered_tenant"
    flow.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_reconfigure_rejects_half_rbw_pair_from_effective_settings():
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {CONF_USER_MRI: "8:orgid:alice"}
    entry.options = {CONF_RBW_ENTRY_ID: "rbw_entry", CONF_RBW_ITEM_ID: "rbw_item"}
    flow = reconfigure_setup(entry)

    result = await flow.async_step_reconfigure(
        {CONF_RBW_ENTRY_ID: "rbw_entry", CONF_RBW_ITEM_ID: "", CONF_USE_BROWSER_DAEMON: True}
    )

    defaults = result["data_schema"]({})
    assert result["errors"] == {"base": "rbw_incomplete"}
    assert _suggested_vault(result) == "rbw_entry"
    flow.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.parametrize("step", ["reconfigure", "reauth_confirm"])
@pytest.mark.parametrize("cleared", [{}, {CONF_RBW_ENTRY_ID: ""}])
@pytest.mark.parametrize("hidden", [{}, {CONF_RBW_ITEM_ID: "stale-item"}])
@pytest.mark.asyncio
async def test_authentication_clears_rbw_pair_and_keeps_omitted_options(step, cleared, hidden):
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {CONF_USER_MRI: "8:orgid:alice", CONF_USE_BROWSER_DAEMON: True}
    entry.options = {
        CONF_RBW_ENTRY_ID: "rbw_entry",
        CONF_RBW_ITEM_ID: "rbw_item",
        CONF_PREFER_PASSKEY: False,
        CONF_POLL_INTERVAL: 45,
        CONF_ENABLE_PUSH: False,
        CONF_DEFAULT_CONVERSATION_ID: "existing_chat",
    }
    flow = reconfigure_setup(entry)
    auth = MagicMock()
    auth.user_mri = "8:orgid:alice"
    auth.export_data.return_value = {CONF_USER_MRI: "8:orgid:alice"}

    with (
        patch(
            "custom_components.msteams.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(return_value=auth),
        ) as auth_from_browser,
    ):
        flow.reauth_entry = entry
        result = await getattr(flow, f"async_step_{step}")(
            {CONF_USE_BROWSER_DAEMON: True, **cleared, **hidden}
        )

    expected_reason = "reconfigure_successful" if step == "reconfigure" else "reauth_successful"
    assert result["reason"] == expected_reason
    assert auth_from_browser.await_args.kwargs["rbw_entry_id"] == ""
    assert auth_from_browser.await_args.kwargs["rbw_item_id"] == ""
    update = flow.hass.config_entries.async_update_entry.call_args.kwargs
    assert update["data"][CONF_RBW_ENTRY_ID] == ""
    assert update["data"][CONF_RBW_ITEM_ID] == ""
    assert update["options"] == {
        CONF_RBW_ENTRY_ID: "",
        CONF_RBW_ITEM_ID: "",
        CONF_PREFER_PASSKEY: False,
        CONF_POLL_INTERVAL: 45,
        CONF_ENABLE_PUSH: False,
        CONF_DEFAULT_CONVERSATION_ID: "existing_chat",
    }
    flow.hass.config_entries.async_reload.assert_awaited_once_with("existing_entry")


@pytest.mark.asyncio
async def test_file_reconfigure_does_not_enable_installed_browser_integration():
    entry = MagicMock()
    entry.entry_id = "existing_entry"
    entry.data = {
        CONF_USER_MRI: "8:orgid:alice",
        CONF_BROWSER_DAEMON_PROFILE: "unused_profile",
        CONF_CREDENTIALS_FILE: "/tmp/fake_creds.json",
        CONF_RBW_ENTRY_ID: "unused_vault",
    }
    entry.options = {}
    flow = reconfigure_setup(entry)
    flow.hass.config.components = {"browser_daemon"}
    form = await flow.async_step_reconfigure()
    assert form["data_schema"]({})[CONF_USE_BROWSER_DAEMON] is False
    auth = MagicMock()
    auth.user_mri = "8:orgid:alice"
    auth.export_data.return_value = {CONF_USER_MRI: auth.user_mri}
    with (
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_credentials",
            new=AsyncMock(return_value=auth),
        ) as from_file,
        patch(
            "custom_components.msteams.config_flow.TeamsAuth.async_from_browser_daemon",
            new=AsyncMock(),
        ) as from_browser,
    ):
        result = await flow.async_step_reconfigure({})
    assert result["reason"] == "reconfigure_successful"
    from_file.assert_awaited_once()
    from_browser.assert_not_awaited()
    update = flow.hass.config_entries.async_update_entry.call_args.kwargs
    assert update["data"][CONF_USE_BROWSER_DAEMON] is False


@pytest.mark.parametrize("language", ["en", "de"])
def test_authentication_fields_have_translated_labels(language):
    root = Path(__file__).resolve().parents[1] / "custom_components" / "msteams"
    translated = json.loads((root / "translations" / f"{language}.json").read_text())
    fields = {CONF_RBW_ENTRY_ID, CONF_PREFER_PASSKEY}
    for section in ("config", "options"):
        assert CONF_RBW_ITEM_ID in translated[section]["step"]["rbw_item"]["data"]
    for step in ("user", "reauth_confirm", "reconfigure"):
        assert fields <= translated["config"]["step"][step]["data"].keys()
    assert fields | {CONF_ENABLE_PUSH} <= translated["options"]["step"]["init"]["data"].keys()
    for section in ("config", "options"):
        assert translated[section]["error"]["rbw_incomplete"]
    assert translated["config"]["abort"]["reconfigure_successful"]


def _daemon_entry(entry_id="bd1", title="Browser Daemon (a)", disabled_by=None):
    entry = MagicMock(entry_id=entry_id, disabled_by=disabled_by)
    entry.title = title
    return entry


@pytest.mark.asyncio
async def test_connection_form_selects_integration_with_override_behind_checkbox():
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.components = {"browser_daemon"}
    flow.hass.data = {}
    flow.hass.config_entries.async_entries.side_effect = lambda domain: (
        [_daemon_entry(), _daemon_entry("off", "Off", disabled_by="user")]
        if domain == "browser_daemon"
        else []
    )
    result = await flow.async_step_user()
    markers = {key.schema: key for key in result["data_schema"].schema}
    browser = {"field": CONF_USE_BROWSER_DAEMON, "value": True}
    override = {"field": "browser_daemon_override", "value": True}
    picker = result["data_schema"].schema[markers["browser_daemon_entry_id"]]
    assert picker.config["integration"] == "browser_daemon"
    assert markers["browser_daemon_entry_id"].default() == "bd1"
    assert markers["browser_daemon_override"].default() is False
    assert markers["browser_daemon_url"].visible == [browser, override]
    assert markers["browser_daemon_secret"].visible == [browser, override]
    # The cluster secret is never prefilled.
    assert "browser_daemon_secret" not in result["data_schema"]({})


@pytest.mark.asyncio
async def test_connection_form_without_integration_only_offers_override():
    flow = TeamsConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.components = set()
    flow.hass.data = {}
    flow.hass.config_entries.async_entries.return_value = []
    result = await flow.async_step_user()
    markers = {key.schema: key for key in result["data_schema"].schema}
    assert "browser_daemon_entry_id" not in markers
    assert markers["browser_daemon_override"].default() is True


@pytest.mark.parametrize(
    ("submitted", "saved", "expected"),
    [
        # Hidden fields are not submitted: the checkbox decides, stale values go.
        (
            {"browser_daemon_override": False, "browser_daemon_entry_id": "bd2"},
            {"browser_daemon_url": "https://old", "browser_daemon_secret": "old"},
            ("bd2", "", None),
        ),
        ({}, {"browser_daemon_entry_id": "bd1"}, ("bd1", "", None)),
        (
            {
                "browser_daemon_override": True,
                "browser_daemon_url": "https://bd.example/",
                "browser_daemon_secret": " new ",
            },
            {"browser_daemon_entry_id": "bd1"},
            ("", "https://bd.example", "new"),
        ),
        (
            {"browser_daemon_override": True, "browser_daemon_url": "https://bd.example"},
            {"browser_daemon_secret": "old"},
            ("", "https://bd.example", "old"),
        ),
    ],
)
def test_browser_connection_resolves(submitted, saved, expected):
    from custom_components.msteams.config_flow import browser_connection

    result = browser_connection(submitted, saved)
    assert (
        result["browser_daemon_entry_id"],
        result["browser_daemon_url"],
        result["browser_daemon_secret"],
    ) == expected


@pytest.mark.parametrize(
    "submitted",
    [
        {"browser_daemon_override": True},
        {"browser_daemon_override": True, "browser_daemon_url": "https://bd.example"},
        {
            "browser_daemon_override": True,
            "browser_daemon_url": "bd.example",
            "browser_daemon_secret": "s",
        },
    ],
)
def test_browser_connection_rejects_incomplete_override(submitted):
    from custom_components.msteams.config_flow import (
        InvalidBrowserDaemonConnection,
        browser_connection,
    )

    with pytest.raises(InvalidBrowserDaemonConnection):
        browser_connection(submitted, {})
