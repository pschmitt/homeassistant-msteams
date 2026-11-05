"""Tests for shared browser-daemon session acquisition."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from browser_daemon_client import BrowserDaemon, BrowserDaemonError

from custom_components.msteams import browser_auth
from custom_components.msteams.browser_auth import (
    BrowserAuthError,
    BrowserInteractionRequired,
    acquire_browser_auth,
)


@pytest.fixture(autouse=True)
def immediate_login(monkeypatch):
    monkeypatch.setattr(browser_auth, "LOGIN_GRACE_SECONDS", 0)


@pytest.fixture
def readiness_clock(monkeypatch):
    elapsed = [0]

    async def advance(seconds):
        elapsed[0] += seconds

    monkeypatch.setattr(browser_auth.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(browser_auth.asyncio, "sleep", advance)
    monkeypatch.setattr(browser_auth, "LOGIN_GRACE_SECONDS", 10)
    return elapsed


def client_with_session(*, owned=False):
    client = MagicMock(spec=BrowserDaemon)
    client.overview = AsyncMock(
        return_value={
            "profiles": [
                {"name": "work", "node_name": "node", "session_id": None if owned else "session"}
            ],
            "targets": [],
        }
    )
    client.create_session = AsyncMock(return_value={"id": "session"})
    client.set_idle_timeout = AsyncMock()
    client.tabs = AsyncMock(return_value=[])
    client.open_tab = AsyncMock(return_value={"id": "tab"})
    client.export_auth = AsyncMock(
        return_value={"url": "https://teams.microsoft.com", "local_storage": {"k": "v"}}
    )
    client.close_tab = AsyncMock()
    client.session = AsyncMock(return_value={"client_count": 0})
    client._json = AsyncMock(return_value={"session": {"client_count": 0}, "targets": []})
    client.delete_session = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_group_placement_precedes_fresh_inventory():
    client = client_with_session()
    calls = []

    async def place(*args, **kwargs):
        calls.append("placement")
        return {"node": "node"}

    async def overview():
        calls.append("overview")
        return {
            "profiles": [{"name": "work", "node_name": "node", "session_id": "session"}],
            "targets": [],
        }

    client._json.side_effect = place
    client.overview.side_effect = overview
    await acquire_browser_auth(
        client, "work", lambda _: {"refresh_token": "fake"}, node_group=" cloud "
    )
    assert calls[:2] == ["placement", "overview"]
    client._json.assert_any_await(
        "POST",
        "/api/mesh/placement",
        json={"profile": "work", "group": "cloud", "engine": "chromium", "create": True},
        timeout=180,
    )


@pytest.mark.asyncio
async def test_failed_group_placement_never_falls_back():
    client = client_with_session()
    client._json.side_effect = BrowserDaemonError("no matching node")
    with pytest.raises(BrowserAuthError):
        await acquire_browser_auth(client, "work", lambda _: {}, node_group="cloud")
    client.overview.assert_not_awaited()
    client.create_session.assert_not_awaited()
    client.open_tab.assert_not_awaited()
    client.export_auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_tab_storage_can_populate_without_login(readiness_clock):
    client = client_with_session(owned=True)
    client.export_auth.side_effect = [
        {"url": "about:blank", "local_storage": {}},
        {"url": "https://teams.microsoft.com", "local_storage": {}},
        {"url": "https://teams.microsoft.com", "local_storage": {"k": "v"}},
    ]
    login = AsyncMock()
    _, creds = await acquire_browser_auth(
        client, "work", lambda _: {"refresh_token": "fake"}, login=login
    )
    assert creds["refresh_token"] == "fake"
    assert readiness_clock[0] == 4
    login.assert_not_awaited()
    client.close_tab.assert_awaited_once()


@pytest.mark.asyncio
async def test_silent_token_renewal_can_finish_before_login(readiness_clock):
    client = client_with_session(owned=True)
    validate = AsyncMock(side_effect=[BrowserAuthError("stale"), None])
    login = AsyncMock()
    await acquire_browser_auth(
        client,
        "work",
        lambda _: {"refresh_token": "fake"},
        login=login,
        async_validate=validate,
    )
    assert readiness_clock[0] == 2
    assert validate.await_count == 2
    login.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_grace_expires_before_one_login_attempt(readiness_clock):
    client = client_with_session(owned=True)
    client.export_auth.return_value = {"url": "about:blank", "local_storage": {}}
    login = AsyncMock(return_value="needs_interaction")
    with pytest.raises(BrowserInteractionRequired):
        await acquire_browser_auth(client, "work", lambda _: {}, login=login)
    assert readiness_clock[0] == 10
    login.assert_awaited_once_with("session", "tab", "node")
    client.close_tab.assert_not_awaited()
    client.set_idle_timeout.assert_awaited_once_with("session", 15, node="node")


@pytest.mark.asyncio
async def test_acquire_and_clean_up_owned_session():
    client = client_with_session(owned=True)
    data, creds = await acquire_browser_auth(client, "work", lambda _: {"refresh_token": "fake"})
    assert data["local_storage"]
    assert creds["refresh_token"] == "fake"
    client.close_tab.assert_awaited_once()
    client._json.assert_awaited_once_with("GET", "/api/sessions/session", node="node")
    client.delete_session.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_info",
    [
        {},
        {"session": None, "targets": []},
        {"session": {}, "targets": []},
        {"session": {"client_count": None}, "targets": []},
    ],
)
async def test_owned_session_with_missing_or_malformed_metadata_is_not_deleted(session_info):
    client = client_with_session(owned=True)
    client._json.return_value = session_info

    await acquire_browser_auth(client, "work", lambda _: {"refresh_token": "fake"})

    client._json.assert_awaited_once_with("GET", "/api/sessions/session", node="node")
    client.close_tab.assert_awaited_once()
    client.delete_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_attached_session_with_other_client_is_not_deleted():
    client = client_with_session()
    client._json.return_value = {"session": {"client_count": 1}, "targets": []}
    await acquire_browser_auth(client, "work", lambda _: {"refresh_token": "fake"})
    client.delete_session.assert_not_awaited()
    client.set_idle_timeout.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_credentials_trigger_one_login_and_revalidation():
    client = client_with_session()
    validate = AsyncMock(side_effect=[BrowserAuthError("rejected"), None])
    login = AsyncMock(return_value="authenticated")

    data, creds = await acquire_browser_auth(
        client,
        "work",
        lambda _: {"refresh_token": "fake"},
        login=login,
        async_validate=validate,
    )

    assert data["local_storage"]
    assert creds["refresh_token"] == "fake"
    login.assert_awaited_once_with("session", "tab", "node")
    assert validate.await_count == 2
    client.close_tab.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_login_keeps_own_page_for_repair():
    client = client_with_session()
    client.overview.return_value["targets"] = [
        {
            "sessionId": "session",
            "type": "page",
            "url": "https://teams.microsoft.com/",
            "id": "user-tab",
        }
    ]
    login = AsyncMock(return_value="needs_interaction")
    validate_credentials = AsyncMock(side_effect=BrowserAuthError("rejected"))

    with pytest.raises(BrowserInteractionRequired):
        await acquire_browser_auth(
            client,
            "work",
            lambda _: {"refresh_token": "fake"},
            login=login,
            async_validate=validate_credentials,
        )

    client.open_tab.assert_awaited_once()
    login.assert_awaited_once_with("session", "tab", "node")
    validate_credentials.assert_awaited_once()
    client.close_tab.assert_not_awaited()
    client.delete_session.assert_not_awaited()
    client.set_idle_timeout.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_pending_login_gets_extended_timeout():
    client = client_with_session(owned=True)
    login = AsyncMock(return_value="needs_interaction")

    with pytest.raises(BrowserInteractionRequired):
        await acquire_browser_auth(
            client,
            "work",
            lambda _: {},
            login=login,
        )

    client.set_idle_timeout.assert_awaited_once_with("session", 15, node="node")
    client.delete_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_attached_session_timeout_is_not_lowered_for_login():
    client = client_with_session()
    client.overview.return_value["targets"] = [
        {
            "sessionId": "session",
            "type": "page",
            "url": "https://teams.microsoft.com/",
            "id": "user-tab",
        }
    ]
    login = AsyncMock(return_value="authenticated")
    validate_credentials = AsyncMock(side_effect=[BrowserAuthError("rejected"), None])

    await acquire_browser_auth(
        client,
        "work",
        lambda _: {"refresh_token": "fake"},
        login=login,
        async_validate=validate_credentials,
    )

    client.open_tab.assert_awaited_once()
    login.assert_awaited_once_with("session", "tab", "node")
    assert validate_credentials.await_count == 2
    client.delete_session.assert_not_awaited()
    client.set_idle_timeout.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["ha-msteams", ""])
async def test_first_use_creates_named_or_throwaway_session(profile):
    client = client_with_session()
    await acquire_browser_auth(client, profile, lambda _: {"refresh_token": "fake"})
    client.create_session.assert_awaited_once_with(
        profile=profile or None,
        persistent=bool(profile),
        engine="chromium",
        default_tabs=False,
        node=None,
        idle_timeout_minutes=5,
    )
    client.delete_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_profile_is_refused():
    client = client_with_session()
    client.overview.return_value["profiles"] *= 2
    with pytest.raises(BrowserAuthError):
        await acquire_browser_auth(client, "work", lambda _: {})
    client.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_unreachable_daemon_maps_to_browser_auth_error():
    client = client_with_session()
    client.overview.side_effect = BrowserDaemonError("browser-daemon is unreachable")
    with pytest.raises(BrowserAuthError):
        await acquire_browser_auth(client, "work", lambda _: {})


@pytest.mark.asyncio
async def test_client_with_live_test_server():
    from aiohttp import ClientSession, web

    calls = []

    async def handler(request):
        calls.append((request.method, request.path))
        assert request.headers.get("X-Browser-Cluster-Secret") == "test-secret"
        if request.path == "/api/mesh/overview":
            return web.json_response(
                {
                    "local_node": {"name": "node"},
                    "profiles": [{"name": "work", "node_name": "node"}],
                    "targets": [],
                }
            )
        if request.path == "/api/mesh/placement":
            return web.json_response({"node": "node", "profile": "work"})
        if request.path == "/api/sessions" and request.method == "POST":
            payload = await request.json()
            assert payload["idle_timeout_minutes"] == 5
            return web.json_response({"id": "test-session"})
        if request.path == "/json/list":
            return web.json_response([])
        if request.path == "/json/new":
            return web.json_response({"id": "test-tab"})
        if request.path == "/api/pages/test-tab/auth":
            return web.json_response(
                {"url": "https://teams.microsoft.com", "local_storage": {"key": "value"}}
            )
        if request.path == "/api/sessions/test-session" and request.method == "GET":
            return web.json_response({"session": {"client_count": 0}, "targets": []})
        return web.Response(status=204)

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            adapter = BrowserDaemon(session, f"http://127.0.0.1:{port}", "test-secret")
            await acquire_browser_auth(adapter, "work", lambda _: {"refresh_token": "fake"})
    finally:
        await runner.cleanup()
    assert ("DELETE", "/api/sessions/test-session") in calls


def test_node_readiness_requires_refresh_and_unexpired_substrate():
    import base64
    import json
    import time

    from custom_components.msteams.browser_auth import _validate_refresh_token

    refresh = json.dumps({"credentialType": "RefreshToken", "secret": "fake", "clientId": "test"})
    data = {"local_storage": {"refresh": refresh}}
    with pytest.raises(BrowserAuthError):
        _validate_refresh_token(data)
    jwt_body = (
        base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 60}).encode())
        .decode()
        .rstrip("=")
    )
    jwt = f"eyJhbGciOiJub25lIn0.{jwt_body}.fake"
    data["local_storage"]["access"] = json.dumps({"target": "SubstrateSearch", "secret": jwt})
    assert _validate_refresh_token(data)["refresh_token"] == "fake"
