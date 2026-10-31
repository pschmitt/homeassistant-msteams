"""Tests for shared browser-daemon session acquisition."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.msteams.browser_auth import BrowserAuthError, acquire_browser_auth


def client_with_session(*, owned=False):
    client = MagicMock()
    client.async_get_overview = AsyncMock(
        return_value={
            "profiles": [
                {"name": "work", "node_name": "node", "session_id": None if owned else "session"}
            ],
            "targets": [],
        }
    )
    client.async_create_session = AsyncMock(return_value={"id": "session"})
    client.async_set_idle_timeout = AsyncMock()
    client.async_get_tabs = AsyncMock(return_value=[])
    client.async_open_tab = AsyncMock(return_value={"id": "tab"})
    client.async_export_auth = AsyncMock(
        return_value={"url": "https://teams.microsoft.com", "local_storage": {"k": "v"}}
    )
    client.async_close_tab = AsyncMock()
    client.async_get_session = AsyncMock(return_value={"client_count": 0})
    client.async_delete_session = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_acquire_and_clean_up_owned_session():
    client = client_with_session(owned=True)
    data, creds = await acquire_browser_auth(client, "work", lambda _: {"refresh_token": "fake"})
    assert data["local_storage"]
    assert creds["refresh_token"] == "fake"
    client.async_close_tab.assert_awaited_once()
    client.async_delete_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_attached_session_with_other_client_is_not_deleted():
    client = client_with_session()
    client.async_get_session.return_value = {"client_count": 1}
    await acquire_browser_auth(client, "work", lambda _: {"refresh_token": "fake"})
    client.async_delete_session.assert_not_awaited()
    assert client.async_set_idle_timeout.await_count == 2


@pytest.mark.asyncio
async def test_missing_profile_fails_without_creating_session():
    client = client_with_session()
    with pytest.raises(BrowserAuthError):
        await acquire_browser_auth(client, "absent", lambda _: {})
    client.async_create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_adapter_with_live_test_server():
    from aiohttp import ClientSession, web

    from custom_components.msteams.browser_auth import BrowserDaemonClient

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
            return web.json_response({"client_count": 0})
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
            adapter = BrowserDaemonClient(session, f"http://127.0.0.1:{port}", "test-secret")
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
