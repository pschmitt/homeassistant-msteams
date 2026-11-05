"""Tests for Teams browser login without accessing Home Assistant or a vault."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from browser_daemon_client import BrowserDaemonError, RunResult

from custom_components.msteams import browser_login

INERT_USERNAME = "person@example.test"
INERT_PASSWORD = "not-a-real-password"
INERT_TOTP = "123456"


def test_passkey_script_checks_exact_origins_and_can_select_password():
    assert "allowed.has(currentUrl.origin)" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert "currentUrl.href === 'about:blank'" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert "page.getByRole('link', { name: methodNames })" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert "page.getByRole('button', { name: passkeyNames })" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert "!passkeyAvailable && !passkeySelected" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert (
        'input[type="password"]:not([aria-hidden="true"], [aria-hidden="true"] *, [inert], [inert] *)'
        in browser_login.PASSKEY_LOGIN_SCRIPT
    )
    assert "#idA_PWD_SwitchToPassword" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert (
        "currentUrl.origin === 'https://login.microsoftonline.com'"
        in browser_login.PASSKEY_LOGIN_SCRIPT
    )
    assert "page.getByText(/stay signed in\\?/i)" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert (
        "page.getByRole('button', { name: 'Yes', exact: true })"
        in browser_login.PASSKEY_LOGIN_SCRIPT
    )
    assert "reason: 'human_approval_required'" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert "|| 'passkey_script_deadline'" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert "kmsi_yes_clicked: 'deadline_after_kmsi'" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert "currentUrl.pathname === '/common/bridge/fido'" in browser_login.PASSKEY_LOGIN_SCRIPT
    assert (
        "passkey_redirect_seen: 'deadline_after_passkey_redirect'"
        in browser_login.PASSKEY_LOGIN_SCRIPT
    )


@pytest.mark.asyncio
async def test_session_info_works_with_real_aiohttp_session_attribute():
    from aiohttp import ClientSession, web

    calls = []

    async def handler(request):
        calls.append(
            (request.method, request.path, request.headers.get("X-Browser-Cluster-Secret"))
        )
        return web.json_response({"session": {"client_count": 0}, "targets": []})

    app = web.Application()
    app.router.add_get("/api/sessions/test-session", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as http_session:
            client = browser_login.TeamsBrowserDaemon(
                http_session,
                f"http://127.0.0.1:{port}",
                "inert-test-secret",
            )
            assert client.session is http_session
            info = await client.session_info("test-session")
    finally:
        await runner.cleanup()

    assert info == {"session": {"client_count": 0}, "targets": []}
    assert calls == [("GET", "/api/sessions/test-session", "inert-test-secret")]


@pytest.mark.asyncio
async def test_password_login_uses_published_client_rest_api_and_current_totp(monkeypatch):
    from aiohttp import ClientSession, web

    requests = []

    async def handler(request):
        requests.append((request.path, await request.json()))
        outcome = "needs_totp" if len(requests) == 1 else "authenticated"
        return web.json_response({"outcome": outcome})

    app = web.Application()
    app.router.add_post("/api/sessions/test-session/login", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        fields = {
            "username": INERT_USERNAME,
            "password": INERT_PASSWORD,
            "totp": INERT_TOTP,
        }
        reads = []

        async def get_field(_hass, _entry_id, _item_id, field):
            reads.append(field)
            return {"value": fields[field]}

        rbw = types.ModuleType("custom_components.rbw")
        rbw.async_get_field = get_field
        monkeypatch.setitem(sys.modules, "custom_components.rbw", rbw)

        async with ClientSession() as http_session:
            client = browser_login.TeamsBrowserDaemon(
                http_session,
                f"http://127.0.0.1:{port}",
                "inert-test-secret",
            )
            assert callable(client.password_login)
            result = await browser_login.async_login(
                object(),
                client,
                "test-session",
                "test-tab",
                None,
                "entry",
                "item",
                prefer_passkey=False,
            )
    finally:
        await runner.cleanup()

    assert result == ("authenticated", INERT_USERNAME)
    assert reads == ["username", "password", "totp"]
    expected = {
        "tab": "test-tab",
        "username": INERT_USERNAME,
        "password": INERT_PASSWORD,
        "allowed_origins": list(browser_login.MICROSOFT_ORIGINS),
        "success_selector": '[data-tid="app-bar"], [data-tid="app-layout-area--main"]',
        "timeout_seconds": 30,
    }
    assert requests == [
        ("/api/sessions/test-session/login", expected),
        (
            "/api/sessions/test-session/login",
            {**expected, "totp_code": INERT_TOTP},
        ),
    ]


def setup_login(monkeypatch, fields):
    reads = []

    async def get_field(_hass, _entry_id, _item_id, field):
        reads.append(field)
        value = fields[field]
        if isinstance(value, BaseException):
            raise value
        return {"value": value}

    rbw = types.ModuleType("custom_components.rbw")
    rbw.async_get_field = get_field
    monkeypatch.setitem(sys.modules, "custom_components.rbw", rbw)

    client = MagicMock(spec=browser_login.TeamsBrowserDaemon)
    client.session_info = AsyncMock(return_value={"session": {"client_count": 0}, "targets": []})
    client.arm_passkeys = AsyncMock()
    client.run = AsyncMock(return_value=RunResult(ok=True, result={"outcome": "authenticated"}))
    client.password_login = AsyncMock(return_value="authenticated")
    return client, reads


@pytest.mark.asyncio
async def test_passkey_success_does_not_read_password_or_totp(monkeypatch):
    client, reads = setup_login(monkeypatch, {"username": INERT_USERNAME})

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("authenticated", INERT_USERNAME)
    assert reads == ["username"]
    client.session_info.assert_awaited_once_with("session", node=None)
    assert client.login_diagnostic_reason == "script_authenticated"
    assert client.login_diagnostic_exception_type == "none"
    assert [call.kwargs["enabled"] for call in client.arm_passkeys.await_args_list] == [True, False]
    client.password_login.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_info",
    [
        {},
        {"session": None, "targets": []},
        {"session": {}, "targets": []},
        {"session": {"client_count": None}, "targets": []},
        {"client_count": 0},
    ],
)
async def test_malformed_session_metadata_fails_closed_for_passkeys(monkeypatch, session_info):
    client, reads = setup_login(monkeypatch, {"username": INERT_USERNAME})
    client.session_info.return_value = session_info

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("needs_interaction", INERT_USERNAME)
    assert reads == ["username"]
    client.arm_passkeys.assert_not_awaited()
    client.run.assert_not_awaited()
    client.password_login.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_client_in_session_envelope_prevents_passkey_arming(monkeypatch):
    client, reads = setup_login(monkeypatch, {"username": INERT_USERNAME})
    client.session_info.return_value = {"session": {"client_count": 1}, "targets": []}

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("needs_interaction", INERT_USERNAME)
    assert reads == ["username"]
    client.arm_passkeys.assert_not_awaited()
    client.run.assert_not_awaited()
    client.password_login.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("run failed"), asyncio.CancelledError()])
async def test_passkey_run_failure_or_cancellation_disarms(monkeypatch, failure):
    client, _reads = setup_login(monkeypatch, {"username": INERT_USERNAME})
    client.run.side_effect = failure

    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await browser_login.async_login(
                object(), client, "session", "tab", None, "entry", "item"
            )
    else:
        # Unexpected run failures are converted to handoff without exception text.
        result = await browser_login.async_login(
            object(), client, "session", "tab", None, "entry", "item"
        )
        assert result == ("needs_interaction", INERT_USERNAME)
        assert client.login_diagnostic_reason == "passkey_run_exception"
        assert client.login_diagnostic_exception_type == "RuntimeError"

    assert [call.kwargs["enabled"] for call in client.arm_passkeys.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_password_fallback_reads_password_fresh_after_chooser(monkeypatch):
    client, reads = setup_login(
        monkeypatch, {"username": INERT_USERNAME, "password": INERT_PASSWORD}
    )
    client.run.return_value = RunResult(ok=True, result={"outcome": "password_fallback"})

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("authenticated", INERT_USERNAME)
    assert reads == ["username", "password"]
    assert client.password_login.await_args.args[2:4] == (INERT_USERNAME, INERT_PASSWORD)
    assert [call.kwargs["enabled"] for call in client.arm_passkeys.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_unavailable_passkey_service_falls_back_and_disarms(monkeypatch):
    client, reads = setup_login(
        monkeypatch, {"username": INERT_USERNAME, "password": INERT_PASSWORD}
    )
    client.arm_passkeys.side_effect = [BrowserDaemonError("unavailable", 503), None]

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("authenticated", INERT_USERNAME)
    assert reads == ["username", "password"]
    assert [call.kwargs["enabled"] for call in client.arm_passkeys.await_args_list] == [True, False]
    client.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_disarm_error_does_not_replace_passkey_success(monkeypatch):
    client, _reads = setup_login(monkeypatch, {"username": INERT_USERNAME})
    client.arm_passkeys.side_effect = [None, RuntimeError("disarm failed")]

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("authenticated", INERT_USERNAME)


@pytest.mark.asyncio
async def test_totp_is_read_only_after_password_login_requests_it(monkeypatch):
    client, reads = setup_login(
        monkeypatch,
        {"username": INERT_USERNAME, "password": INERT_PASSWORD, "totp": INERT_TOTP},
    )
    client.password_login.side_effect = ["needs_totp", "authenticated"]

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item", prefer_passkey=False
    )

    assert result == ("authenticated", INERT_USERNAME)
    assert reads == ["username", "password", "totp"]
    assert client.password_login.await_args.kwargs["totp_code"] == INERT_TOTP


@pytest.mark.asyncio
async def test_rbw_error_propagates_without_logging_or_browser_calls(monkeypatch):
    error = RuntimeError("vault read failed")
    client, _reads = setup_login(monkeypatch, {"username": error})

    with pytest.raises(RuntimeError, match="vault read failed"):
        await browser_login.async_login(object(), client, "session", "tab", None, "entry", "item")

    client.arm_passkeys.assert_not_awaited()
    client.password_login.assert_not_awaited()


@pytest.mark.asyncio
async def test_human_handoff_does_not_read_password_or_totp(monkeypatch):
    client, reads = setup_login(
        monkeypatch,
        {"username": INERT_USERNAME, "password": INERT_PASSWORD, "totp": INERT_TOTP},
    )
    client.run.return_value = RunResult(
        ok=True,
        result={"outcome": "needs_interaction", "reason": "human_approval_required"},
    )

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("needs_interaction", INERT_USERNAME)
    assert reads == ["username"]
    assert client.login_diagnostic_reason == "human_approval_required"
    client.password_login.assert_not_awaited()
    assert [call.kwargs["enabled"] for call in client.arm_passkeys.await_args_list] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason_code", ["deadline_after_kmsi", "deadline_after_passkey_redirect"])
async def test_script_deadline_reason_is_reported_without_dynamic_data(monkeypatch, reason_code):
    client, _reads = setup_login(monkeypatch, {"username": INERT_USERNAME})
    client.run.return_value = RunResult(
        ok=True,
        result={"outcome": "needs_interaction", "reason": reason_code},
    )

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("needs_interaction", INERT_USERNAME)
    assert client.login_diagnostic_reason == reason_code
    assert client.login_diagnostic_exception_type == "none"


@pytest.mark.asyncio
async def test_daemon_run_timeout_has_a_fixed_diagnostic(monkeypatch):
    client, _reads = setup_login(monkeypatch, {"username": INERT_USERNAME})
    client.run.return_value = RunResult(ok=False, timed_out=True)

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("needs_interaction", INERT_USERNAME)
    assert client.login_diagnostic_reason == "passkey_run_timeout"
    assert client.login_diagnostic_exception_type == "none"


@pytest.mark.asyncio
async def test_timeout_covers_passkey_run_and_still_disarms(monkeypatch):
    client, reads = setup_login(monkeypatch, {"username": INERT_USERNAME})

    async def slow_run(**_kwargs):
        await asyncio.sleep(1)

    client.run.side_effect = slow_run
    monkeypatch.setattr(browser_login, "LOGIN_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        await browser_login.async_login(object(), client, "session", "tab", None, "entry", "item")

    assert reads == ["username"]
    assert [call.kwargs["enabled"] for call in client.arm_passkeys.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_unexpected_run_result_type_does_not_trigger_password_fallback(monkeypatch):
    client, reads = setup_login(
        monkeypatch, {"username": INERT_USERNAME, "password": INERT_PASSWORD}
    )
    client.run.return_value = {"result": {"outcome": "password_fallback"}}

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("needs_interaction", INERT_USERNAME)
    assert reads == ["username"]
    assert client.login_diagnostic_reason == "passkey_run_result_type"
    client.password_login.assert_not_awaited()
    assert [call.kwargs["enabled"] for call in client.arm_passkeys.await_args_list] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [{}, {"outcome": None}, {"outcome": "unexpected"}, [], "authenticated", None],
)
async def test_password_login_malformed_outcomes_fail_closed(monkeypatch, response):
    client = browser_login.TeamsBrowserDaemon(MagicMock(), "http://localhost", "inert")
    client._json = AsyncMock(return_value=response)

    outcome = await client.password_login(
        "test-session", "test-tab", INERT_USERNAME, INERT_PASSWORD
    )

    assert outcome == "needs_interaction"
    client._json.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_session_client_prevents_session_wide_arming(monkeypatch):
    client, reads = setup_login(monkeypatch, {"username": INERT_USERNAME})
    client.session_info.return_value = {"session": {"client_count": 1}, "targets": []}

    result = await browser_login.async_login(
        object(), client, "session", "tab", None, "entry", "item"
    )

    assert result == ("needs_interaction", INERT_USERNAME)
    assert reads == ["username"]
    client.arm_passkeys.assert_not_awaited()
    client.run.assert_not_awaited()
