"""Unit tests for Teams API client, formatting, and authentication."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.msteams.api import (
    TeamsApiClient,
    TeamsAuth,
    TeamsAuthError,
    _decode_jwt_payload,
    _generate_client_message_id,
    _markdown_to_teams_html,
    _same_user_mri,
)


def test_markdown_to_teams_html():
    """Verify Markdown is properly transformed to Teams HTML."""
    assert _markdown_to_teams_html("") == ""
    # Already HTML
    assert _markdown_to_teams_html("<p>Hello</p>") == "<p>Hello</p>"

    # Bold and italics
    md = "**bold text** and *italic text*"
    html = _markdown_to_teams_html(md)
    assert "<strong>bold text</strong>" in html
    assert "<em>italic text</em>" in html

    # Code and links
    md2 = "`code snippet` and [Google](https://google.com)"
    html2 = _markdown_to_teams_html(md2)
    assert "<code>code snippet</code>" in html2
    assert '<a href="https://google.com">Google</a>' in html2


def test_generate_client_message_id():
    """Verify generated client message id is a numeric string."""
    cid = _generate_client_message_id()
    assert cid.isdigit()
    assert len(cid) >= 18


def test_decode_jwt_payload():
    """Verify decoding unverified JWT payload."""
    encoded = (
        "header."
        + "eyJ1cG4iOiJ0ZXN0QGV4YW1wbGUuY29tIiwic2t5cGVpZCI6Ijg6b3JnaWQ6MTIzNDUifQ"
        + ".sig"
    )
    decoded = _decode_jwt_payload(encoded)
    assert decoded.get("upn") == "test@example.com"
    assert decoded.get("skypeid") == "8:orgid:12345"


@pytest.mark.parametrize("left_prefix", ["", "orgid:", "8:orgid:"])
@pytest.mark.parametrize("right_prefix", ["", "orgid:", "8:orgid:"])
def test_same_user_mri_normalizes_org_uuid_forms(left_prefix, right_prefix):
    object_id = "b1c24a5e-7834-4b4c-b523-2fd943719a11"
    assert _same_user_mri(f"{left_prefix}{object_id}", f"{right_prefix}{object_id.upper()}")
    assert not _same_user_mri(
        f"{left_prefix}{object_id}", f"{right_prefix}9d5c654c-970b-4cb2-a31b-5799e2c9b1df"
    )
    assert not _same_user_mri(f"{left_prefix}{object_id}", f"8:gcch:{object_id}")
    assert not _same_user_mri("not-an-id", "orgid:not-an-id")
    assert not _same_user_mri("not-an-id", "8:orgid:not-an-id")


@pytest.mark.asyncio
async def test_auth_refresh_tokens_success():
    """Test successful token refresh flow."""
    mock_session = MagicMock()

    # Mock Azure AD token endpoint response
    token_resp = AsyncMock()
    token_resp.status = 200
    token_resp.json = AsyncMock(
        return_value={
            "access_token": "mock_access_token",
            "refresh_token": "mock_new_refresh_token",
            "expires_in": 3600,
        }
    )

    # Mock authsvc Skype token exchange response
    skype_resp = AsyncMock()
    skype_resp.status = 200
    skype_resp.json = AsyncMock(return_value={"tokens": {"skypeToken": "mock_skype_token"}})

    mock_session.post.side_effect = [
        AsyncMock(__aenter__=AsyncMock(return_value=token_resp)),
        AsyncMock(__aenter__=AsyncMock(return_value=token_resp)),
        AsyncMock(__aenter__=AsyncMock(return_value=token_resp)),
        AsyncMock(__aenter__=AsyncMock(return_value=skype_resp)),
    ]

    auth = TeamsAuth(session=mock_session, refresh_token="initial_refresh_token")
    result = await auth.async_refresh_tokens()

    assert result is True
    assert auth.refresh_token == "mock_new_refresh_token"
    assert auth.spaces_token == "mock_access_token"
    assert auth.skype_token == "mock_skype_token"
    assert auth.is_authenticated is True


@pytest.mark.asyncio
async def test_auth_refresh_tokens_callback():
    """Test that on_tokens_refreshed callback is invoked with exported data."""
    mock_session = MagicMock()
    token_resp = AsyncMock()
    token_resp.status = 200
    token_resp.json = AsyncMock(
        return_value={
            "access_token": "mock_access_token",
            "refresh_token": "mock_new_refresh_token",
            "expires_in": 3600,
        }
    )
    skype_resp = AsyncMock()
    skype_resp.status = 200
    skype_resp.json = AsyncMock(return_value={"tokens": {"skypeToken": "mock_skype_token"}})

    mock_session.post.side_effect = [
        AsyncMock(__aenter__=AsyncMock(return_value=token_resp)),
        AsyncMock(__aenter__=AsyncMock(return_value=token_resp)),
        AsyncMock(__aenter__=AsyncMock(return_value=token_resp)),
        AsyncMock(__aenter__=AsyncMock(return_value=skype_resp)),
    ]

    callback_data = []

    def on_refresh(data):
        callback_data.append(data)

    auth = TeamsAuth(
        session=mock_session,
        refresh_token="initial_refresh_token",
        on_tokens_refreshed=on_refresh,
    )
    await auth.async_refresh_tokens()

    assert len(callback_data) == 1
    assert callback_data[0]["refresh_token"] == "mock_new_refresh_token"


@pytest.mark.asyncio
async def test_auth_refresh_tokens_failure():
    """Test handling of 401 unauthorized token refresh failure."""
    mock_session = MagicMock()
    resp = AsyncMock()
    resp.status = 401
    resp.text = AsyncMock(return_value="invalid_grant")
    mock_session.post.return_value = AsyncMock(__aenter__=AsyncMock(return_value=resp))

    auth = TeamsAuth(session=mock_session, refresh_token="bad_token")
    with pytest.raises(TeamsAuthError):
        await auth.async_refresh_tokens()


@pytest.mark.asyncio
async def test_api_get_presence():
    """Test fetching presence through TeamsApiClient."""
    mock_auth = MagicMock()
    mock_auth.is_authenticated = True
    mock_auth.skype_token = "skype123"
    mock_auth.spaces_token = "spaces123"
    mock_session = MagicMock()
    mock_auth.session = mock_session

    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(
        return_value={
            "availability": "Busy",
            "activity": "InAMeeting",
            "statusMessage": {"message": {"content": "In a client meeting"}},
        }
    )
    mock_session.get.return_value = AsyncMock(__aenter__=AsyncMock(return_value=resp))

    client = TeamsApiClient(auth=mock_auth)
    presence = await client.get_presence()

    assert presence["availability"] == "Busy"
    assert presence["activity"] == "InAMeeting"
    assert presence["status_message"] == "In a client meeting"


@pytest.mark.asyncio
async def test_api_send_message():
    """Test sending message through ChatSvc."""
    mock_auth = MagicMock()
    mock_auth.is_authenticated = True
    mock_auth.skype_token = "skype123"
    mock_auth.csa_token = "csa123"
    mock_auth.user_display_name = "Alice"
    mock_session = MagicMock()
    mock_auth.session = mock_session

    resp = AsyncMock()
    resp.status = 201
    resp.json = AsyncMock(return_value={"id": "msg987", "timestamp": "123456789"})
    mock_session.post.return_value = AsyncMock(__aenter__=AsyncMock(return_value=resp))

    client = TeamsApiClient(auth=mock_auth)
    res = await client.send_message("19:conv123", "Hello **world**!")

    assert res["id"] == "msg987"
    call_kwargs = mock_session.post.call_args.kwargs
    assert "<strong>world</strong>" in call_kwargs["json"]["content"]
    assert call_kwargs["json"]["contenttype"] == "Text"
    assert call_kwargs["json"]["imdisplayname"] == "Alice"
    assert call_kwargs["json"]["properties"] == {
        "formatVariant": "TEAMS",
        "cards": "[]",
        "links": "[]",
        "files": "[]",
        "mentions": "[]",
    }


@pytest.mark.asyncio
async def test_disabled_browser_recovery_never_contacts_browser_daemon():
    auth = TeamsAuth(
        session=MagicMock(),
        refresh_token="file-imported",
        browser_daemon_url="https://browser.example.com",
        use_browser_daemon=False,
        hass=MagicMock(),
    )
    auth.async_from_browser_daemon = AsyncMock()
    assert await auth._async_recover_via_browser_daemon() is False
    auth.async_from_browser_daemon.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_self_recovery_on_refresh_failure():
    """Verify that a rejected HTTP refresh automatically invokes browser-daemon recovery."""
    mock_session = MagicMock()
    # 400 invalid_grant response
    resp_400 = AsyncMock()
    resp_400.status = 400
    resp_400.text = AsyncMock(return_value="AADSTS700084: The refresh token was issued to a SPA")
    mock_session.post.return_value = AsyncMock(__aenter__=AsyncMock(return_value=resp_400))

    auth = TeamsAuth(
        session=mock_session,
        refresh_token="expired_token",
        browser_daemon_url="https://browser.example.com",
    )

    fake_recovered_auth = MagicMock()
    fake_recovered_auth.refresh_token = "new_recovered_token"
    fake_recovered_auth.tenant_id = "tenant-1"
    fake_recovered_auth.client_id = "cid-1"
    fake_recovered_auth.substrate_token = "sub_tok"
    fake_recovered_auth.spaces_token = "spaces_tok"
    fake_recovered_auth.csa_token = "csa_tok"
    fake_recovered_auth.skype_token = "skype_tok"
    fake_recovered_auth.tokens_expires_at = None
    fake_recovered_auth.user_mri = "user-1"
    fake_recovered_auth.user_display_name = "User One"
    fake_recovered_auth.user_email = "user@example.com"

    auth.async_from_browser_daemon = AsyncMock(return_value=fake_recovered_auth)

    success = await auth.async_refresh_tokens()
    assert success is True
    assert auth.refresh_token == "new_recovered_token"
    auth.async_from_browser_daemon.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_recovery_account_mismatch_enters_cooldown():
    auth = TeamsAuth(
        session=MagicMock(),
        refresh_token="expired",
        user_mri="expected-user",
        browser_daemon_url="https://browser.example",
    )
    recovered = MagicMock()
    recovered.user_mri = "different-user"
    auth.async_from_browser_daemon = AsyncMock(return_value=recovered)

    assert await auth._async_recover_via_browser_daemon() is False
    assert auth._browser_retry_at > 0
    assert await auth._async_recover_via_browser_daemon() is False
    auth.async_from_browser_daemon.assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_recovery_accepts_bare_uuid_for_same_org_account():
    object_id = "b1c24a5e-7834-4b4c-b523-2fd943719a11"
    auth = TeamsAuth(
        session=MagicMock(),
        refresh_token="expired",
        user_mri=object_id,
        browser_daemon_url="https://browser.example",
    )
    recovered = MagicMock()
    recovered.refresh_token = "new-refresh"
    recovered.tenant_id = "tenant"
    recovered.client_id = "client"
    recovered.substrate_token = "substrate"
    recovered.spaces_token = "spaces"
    recovered.csa_token = "csa"
    recovered.skype_token = "skype"
    recovered.tokens_expires_at = None
    recovered.user_mri = f"8:orgid:{object_id}"
    recovered.user_display_name = "User"
    recovered.user_email = "user@example.com"
    auth.async_from_browser_daemon = AsyncMock(return_value=recovered)

    assert await auth._async_recover_via_browser_daemon() is True
    assert auth.user_mri == f"8:orgid:{object_id}"


@pytest.mark.asyncio
async def test_browser_auth_retries_rejected_credentials_once(monkeypatch, caplog):
    from custom_components.msteams import api

    def response(status, payload=None):
        result = AsyncMock()
        result.status = status
        result.json = AsyncMock(return_value=payload or {})
        result.text = AsyncMock(return_value="sensitive server response")
        return AsyncMock(__aenter__=AsyncMock(return_value=result))

    session = MagicMock()
    session.post.side_effect = [
        response(400),
        response(200, {"access_token": "access", "expires_in": 3600}),
        response(200, {"access_token": "access", "expires_in": 3600}),
        response(200, {"access_token": "access", "expires_in": 3600}),
        response(200, {"tokens": {"skypeToken": "skype"}}),
    ]
    credentials = {
        "refresh_token": "candidate-token",
        "tenant_id": "tenant",
        "client_id": "client",
        "user_email": "user@example.com",
    }
    login = AsyncMock(return_value=("authenticated", "user@example.com"))
    calls = []

    async def acquire(
        _client, _profile, _parse, *, login=None, async_validate=None, node_group=None
    ):
        calls.append("acquire")
        with pytest.raises(TeamsAuthError):
            await async_validate(credentials)
        assert await login("session", "tab", "node") == "authenticated"
        await async_validate(credentials)
        return {}, credentials

    monkeypatch.setattr(api, "acquire_browser_auth", acquire)
    monkeypatch.setattr(api, "async_login", login)

    auth = await TeamsAuth.async_from_browser_daemon(
        session,
        browser_daemon_url="https://browser.example",
        hass=object(),
        rbw_entry_id="entry",
        rbw_item_id="item",
    )

    assert auth.is_authenticated
    assert calls == ["acquire"]
    login.assert_awaited_once()
    assert "sensitive server response" not in caplog.text


@pytest.mark.asyncio
async def test_browser_auth_rejects_selected_account_mismatch(monkeypatch):
    from custom_components.msteams import api
    from custom_components.msteams.browser_auth import BrowserAuthError

    def response(payload):
        result = AsyncMock()
        result.status = 200
        result.json = AsyncMock(return_value=payload)
        return AsyncMock(__aenter__=AsyncMock(return_value=result))

    session = MagicMock()
    session.post.side_effect = [
        response({"access_token": "access", "expires_in": 3600}),
        response({"access_token": "access", "expires_in": 3600}),
        response({"access_token": "access", "expires_in": 3600}),
        response({"tokens": {"skypeToken": "skype"}}),
    ]
    credentials = {
        "refresh_token": "candidate-token",
        "tenant_id": "tenant",
        "client_id": "client",
        "user_email": "different@example.com",
    }
    login = AsyncMock(return_value=("authenticated", "expected@example.com"))

    async def acquire(
        _client, _profile, _parse, *, login=None, async_validate=None, node_group=None
    ):
        await login("session", "tab", "node")
        try:
            await async_validate(credentials)
        except TeamsAuthError:
            raise BrowserAuthError("sign-in required") from None
        return {}, credentials

    monkeypatch.setattr(api, "acquire_browser_auth", acquire)
    monkeypatch.setattr(api, "async_login", login)

    with pytest.raises(TeamsAuthError, match="Complete sign-in"):
        await TeamsAuth.async_from_browser_daemon(
            session,
            browser_daemon_url="https://browser.example",
            hass=object(),
            rbw_entry_id="entry",
            rbw_item_id="item",
        )
    login.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_places_channel_subject_in_properties():
    session = MagicMock()
    response = AsyncMock()
    response.status = 201
    response.json = AsyncMock(return_value={})
    session.post.return_value = AsyncMock(__aenter__=AsyncMock(return_value=response))
    auth = MagicMock(session=session, skype_token="fake", spaces_token="fake", csa_token="fake")
    client = TeamsApiClient(auth)
    client._ensure_auth = AsyncMock()
    await client.send_message("channel@thread.tacv2", "Body", subject="Title")
    assert session.post.call_args.kwargs["json"]["properties"] == {
        "formatVariant": "TEAMS",
        "cards": "[]",
        "links": "[]",
        "files": "[]",
        "mentions": "[]",
        "subject": "Title",
    }


def _provider(monkeypatch, **attrs):
    import sys
    import types

    provider = types.ModuleType("custom_components.browser_daemon")
    provider.BrowserDaemonError = type("BrowserDaemonError", (Exception,), {})
    provider.async_get_daemon = MagicMock(return_value=MagicMock())
    for name, value in attrs.items():
        setattr(provider, name, value)
    monkeypatch.setitem(sys.modules, "custom_components.browser_daemon", provider)
    return provider


@pytest.mark.asyncio
async def test_selected_integration_provides_the_client(monkeypatch):
    from custom_components.msteams import api
    from custom_components.msteams.browser_auth import BrowserAuthError

    provider = _provider(monkeypatch)
    direct = MagicMock()
    monkeypatch.setattr(api, "TeamsBrowserDaemon", direct)
    acquire = AsyncMock(side_effect=BrowserAuthError())
    monkeypatch.setattr(api, "acquire_browser_auth", acquire)
    hass = object()

    with pytest.raises(TeamsAuthError):
        await TeamsAuth.async_from_browser_daemon(
            MagicMock(), hass=hass, browser_daemon_entry_id="bd2"
        )

    provider.async_get_daemon.assert_called_once_with(
        hass, client=api.BROWSER_DAEMON_CLIENT, cls=direct, entry_id="bd2"
    )
    direct.assert_not_called()
    assert acquire.await_args.args[0] is provider.async_get_daemon.return_value


@pytest.mark.asyncio
async def test_override_connects_directly(monkeypatch):
    from custom_components.msteams import api
    from custom_components.msteams.browser_auth import BrowserAuthError

    provider = _provider(monkeypatch)
    direct = MagicMock()
    monkeypatch.setattr(api, "TeamsBrowserDaemon", direct)
    monkeypatch.setattr(api, "acquire_browser_auth", AsyncMock(side_effect=BrowserAuthError()))
    session = MagicMock()

    with pytest.raises(TeamsAuthError):
        await TeamsAuth.async_from_browser_daemon(
            session,
            browser_daemon_url="https://own.example",
            secret="own",
            hass=object(),
            browser_daemon_entry_id="ignored",
        )

    provider.async_get_daemon.assert_not_called()
    direct.assert_called_once_with(
        session, "https://own.example", "own", client=api.BROWSER_DAEMON_CLIENT
    )


@pytest.mark.asyncio
async def test_unavailable_integration_is_an_auth_error(monkeypatch):
    provider = _provider(monkeypatch)
    provider.async_get_daemon.side_effect = provider.BrowserDaemonError("gone")

    with pytest.raises(TeamsAuthError, match="Browser Daemon integration"):
        await TeamsAuth.async_from_browser_daemon(MagicMock(), hass=object())


async def test_summarize_unread_uses_newest_incoming_message():
    """Unread chats show the newest message that is not from me, with its text."""
    from unittest.mock import MagicMock

    from custom_components.msteams.api import TeamsApiClient

    client = TeamsApiClient(MagicMock())
    conv = {"id": "19:abc@thread.v2", "lastMessage": {"id": "2", "composetime": "t"}}
    client._message_cache[conv["id"]] = {
        "marker": "2",
        "messages": [
            {"id": "2", "from_me": True, "text": "mine", "sender": "Me"},
            {"id": "1", "from_me": False, "text": "hello", "sender": "Bob"},
        ],
    }
    result = await client._summarize_unread([conv])
    assert [m["text"] for m in result] == ["hello"]
