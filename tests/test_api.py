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
    assert call_kwargs["json"]["imdisplayname"] == "Alice"
