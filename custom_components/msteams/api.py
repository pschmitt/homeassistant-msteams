"""Microsoft Teams and browser-daemon API client."""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import os
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import aiohttp

from .browser_auth import BrowserAuthError, acquire_browser_auth
from .browser_login import TeamsBrowserDaemon, async_login
from .const import (
    AUTHSVC_ENDPOINT,
    DEFAULT_PROFILE,
    DEFAULT_REGION,
    DEFAULT_TEAMS_BASE_URL,
    REACTIONS,
    TEAMS_CLIENT_ID,
    TOKEN_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

CLIENT_VERSION_HEADER = "1415/1.0.0.2025010401"
MIDDLETIER_VERSION_HEADER = "49/25113001312"
_UUID_MRI_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


BROWSER_DAEMON_CLIENT = "Home Assistant · Teams"


class TeamsAuthError(Exception):
    """Raised when authentication fails or credentials have expired."""


class TeamsApiError(Exception):
    """Raised when an API call fails."""


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode unverified JWT payload for claims (claims, tenant, user MRI)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        _LOGGER.debug("Could not decode JWT payload")
        return {}


def _same_user_mri(left: str | None, right: str | None) -> bool:
    """Compare Teams MRIs, including MSAL's bare Entra object-ID form."""

    def normalize(value: str | None) -> str | None:
        if value is None:
            return None
        object_id = value
        for prefix in ("8:orgid:", "orgid:"):
            if value.startswith(prefix):
                object_id = value[len(prefix) :]
                break
        if _UUID_MRI_RE.fullmatch(object_id):
            return object_id.lower()
        return value

    return normalize(left) == normalize(right)


def _generate_client_message_id() -> str:
    """Generate numeric client message ID as required by Teams ChatSvc."""
    random_part = str(random.randint(0, 999999)).zfill(6)
    return f"{int(time.time() * 1000)}{random_part}"


def _markdown_to_teams_html(text: str) -> str:
    """Basic conversion of Markdown formatting into Teams-compatible HTML."""
    if not text:
        return ""
    # If already HTML, return directly
    if re.search(r"<(p|div|span|blockquote|h[1-6]|ul|ol|li)[^>]*>", text, re.I):
        return text

    # Escaping HTML characters
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Bold: **text** or __text__
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)

    # Italic: *text* or _text_
    escaped = re.sub(r"\*([^*]+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<em>\1</em>", escaped)

    # Inline code: `text`
    escaped = re.sub(r"`([^`]+?)`", r"<code>\1</code>", escaped)

    # Links: [label](url)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2">\1</a>', escaped)

    # Paragraphs / Linebreaks
    paragraphs = escaped.split("\n\n")
    html_paragraphs = [f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip()]
    return "".join(html_paragraphs) if html_paragraphs else f"<p>{escaped}</p>"


def _is_teams_host(url: str | None) -> bool:
    """Return True if url belongs to Microsoft Teams or Azure AD login."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse

        host = urlparse(url).netloc.lower()
        return host in (
            "teams.microsoft.com",
            "teams.cloud.microsoft",
            "login.microsoftonline.com",
        )
    except Exception:
        return False


class TeamsAuth:
    """Handles OAuth token lifecycle, refresh and storage."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
        tenant_id: str | None = None,
        client_id: str = TEAMS_CLIENT_ID,
        user_mri: str | None = None,
        user_display_name: str | None = None,
        user_email: str | None = None,
        on_tokens_refreshed: Callable[[dict[str, Any]], Any] | None = None,
        hass: Any | None = None,
        browser_daemon_url: str | None = None,
        browser_daemon_entry_id: str | None = None,
        browser_daemon_profile: str = DEFAULT_PROFILE,
        browser_daemon_secret: str | None = None,
        browser_daemon_node_group: str | None = None,
        use_browser_daemon: bool = True,
        rbw_entry_id: str | None = None,
        rbw_item_id: str | None = None,
        prefer_passkey: bool = True,
    ) -> None:
        self.session = session
        self.refresh_token = refresh_token
        self.tenant_id = tenant_id or "common"
        self.client_id = client_id or TEAMS_CLIENT_ID
        self.user_mri = user_mri
        self.user_display_name = user_display_name or "User"
        self.user_email = user_email
        self.on_tokens_refreshed = on_tokens_refreshed
        self.hass = hass
        self.browser_daemon_url = browser_daemon_url
        self.browser_daemon_entry_id = browser_daemon_entry_id
        self.browser_daemon_profile = browser_daemon_profile
        self.browser_daemon_secret = browser_daemon_secret
        self.browser_daemon_node_group = browser_daemon_node_group
        self.use_browser_daemon = use_browser_daemon
        self._is_recovering = False
        self.rbw_entry_id = rbw_entry_id
        self.rbw_item_id = rbw_item_id
        self.prefer_passkey = prefer_passkey
        self._browser_retry_at = 0.0

        self.substrate_token: str | None = None
        self.spaces_token: str | None = None
        self.csa_token: str | None = None
        self.skype_token: str | None = None
        self.tokens_expires_at: datetime | None = None

    @property
    def is_authenticated(self) -> bool:
        """Return True if core tokens exist and are not expired."""
        if not (self.skype_token and self.spaces_token):
            return False
        if self.tokens_expires_at is None:
            return True
        return datetime.now(UTC) < self.tokens_expires_at - timedelta(minutes=5)

    def export_data(self) -> dict[str, Any]:
        """Export state for persisting in ConfigEntry."""
        return {
            "refresh_token": self.refresh_token,
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "user_mri": self.user_mri,
            "user_display_name": self.user_display_name,
            "user_email": self.user_email,
        }

    async def _async_recover_via_browser_daemon(self) -> bool:
        """Attempt to restore authentication state via browser-daemon."""
        if (
            not self.use_browser_daemon
            or self._is_recovering
            or time.monotonic() < self._browser_retry_at
        ):
            return False
        if not self.hass and not self.browser_daemon_url:
            return False

        self._is_recovering = True
        try:
            new_auth = await self.async_from_browser_daemon(
                session=self.session,
                browser_daemon_url=self.browser_daemon_url,
                browser_daemon_entry_id=self.browser_daemon_entry_id,
                profile=self.browser_daemon_profile,
                node_group=self.browser_daemon_node_group,
                secret=self.browser_daemon_secret,
                hass=self.hass,
                rbw_entry_id=self.rbw_entry_id,
                rbw_item_id=self.rbw_item_id,
                prefer_passkey=self.prefer_passkey,
            )
            if self.user_mri and not _same_user_mri(new_auth.user_mri, self.user_mri):
                raise TeamsAuthError("Browser authentication belongs to a different Teams account")
            self.refresh_token = new_auth.refresh_token
            self.tenant_id = new_auth.tenant_id
            self.client_id = new_auth.client_id
            self.substrate_token = new_auth.substrate_token
            self.spaces_token = new_auth.spaces_token
            self.csa_token = new_auth.csa_token
            self.skype_token = new_auth.skype_token
            self.tokens_expires_at = new_auth.tokens_expires_at
            if new_auth.user_mri:
                self.user_mri = new_auth.user_mri
            if new_auth.user_display_name:
                self.user_display_name = new_auth.user_display_name
            if new_auth.user_email:
                self.user_email = new_auth.user_email

            if self.on_tokens_refreshed:
                try:
                    res = self.on_tokens_refreshed(self.export_data())
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    _LOGGER.warning("Error in on_tokens_refreshed callback")

            _LOGGER.info("Successfully recovered Teams authentication via browser-daemon")
            return True
        except Exception:
            self._browser_retry_at = time.monotonic() + 300
            _LOGGER.warning(
                "Teams browser recovery unsuccessful; check the configured profile and authentication repair"
            )
            return False
        finally:
            self._is_recovering = False

    async def async_refresh_tokens(self) -> bool:
        """Refresh access tokens using the refresh token."""
        if not self.refresh_token:
            if await self._async_recover_via_browser_daemon():
                return True
            raise TeamsAuthError("No refresh token available to refresh authentication")

        token_url = TOKEN_ENDPOINT.format(tenantId=self.tenant_id)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://teams.microsoft.com",
        }
        scopes = [
            ("substrate", "https://substrate.office.com/.default offline_access"),
            ("spaces", "https://api.spaces.skype.com/.default offline_access"),
            ("csa", "https://chatsvcagg.teams.microsoft.com/.default offline_access"),
        ]

        current_refresh_token = self.refresh_token
        new_tokens: dict[str, str] = {}
        expires_in_seconds = 3600

        for key, scope in scopes:
            data = {
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": current_refresh_token,
                "scope": scope,
            }
            try:
                async with self.session.post(
                    token_url, data=data, headers=headers, timeout=15
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("Token refresh failed for %s (HTTP %s)", key, resp.status)
                        if resp.status in (400, 401):
                            if await self._async_recover_via_browser_daemon():
                                return True
                            raise TeamsAuthError("OAuth token refresh rejected")
                        continue
                    payload = await resp.json()
                    new_tokens[key] = payload.get("access_token")
                    if "refresh_token" in payload:
                        current_refresh_token = payload["refresh_token"]
                    if "expires_in" in payload:
                        expires_in_seconds = min(expires_in_seconds, int(payload["expires_in"]))
            except aiohttp.ClientError:
                raise TeamsAuthError("Network error refreshing tokens") from None

        if not new_tokens.get("spaces"):
            if await self._async_recover_via_browser_daemon():
                return True
            raise TeamsAuthError("Failed to obtain Skype Spaces access token")

        self.refresh_token = current_refresh_token
        self.substrate_token = new_tokens.get("substrate")
        self.spaces_token = new_tokens.get("spaces")
        self.csa_token = new_tokens.get("csa") or self.spaces_token
        self.tokens_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)

        # Exchange Skype Spaces token for skypetoken
        await self._async_exchange_skype_token(self.spaces_token)

        # Extract user MRI / display name if not already set
        if not self.user_mri and self.skype_token:
            jwt = _decode_jwt_payload(self.skype_token)
            self.user_mri = jwt.get("skypeid") or jwt.get("mri")
            if not self.user_email:
                self.user_email = jwt.get("upn") or jwt.get("email")

        _LOGGER.info("Successfully refreshed Microsoft Teams tokens")

        if self.on_tokens_refreshed:
            try:
                res = self.on_tokens_refreshed(self.export_data())
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                _LOGGER.warning("Error in on_tokens_refreshed callback")

        return True

    async def _async_exchange_skype_token(self, spaces_access_token: str) -> None:
        """Exchange Skype Spaces token for skypetoken via authsvc."""
        headers = {
            "Authorization": f"Bearer {spaces_access_token}",
            "Content-Type": "application/json",
        }
        try:
            async with self.session.post(
                AUTHSVC_ENDPOINT, headers=headers, json={}, timeout=15
            ) as resp:
                if resp.status != 200:
                    raise TeamsAuthError(f"Skype token exchange failed (HTTP {resp.status})")
                data = await resp.json()
                self.skype_token = data.get("tokens", {}).get("skypeToken")
                if not self.skype_token:
                    raise TeamsAuthError("Skype token exchange did not return skypeToken")
        except aiohttp.ClientError:
            raise TeamsAuthError("Network error exchanging Skype token") from None

    @classmethod
    def parse_credentials(cls, content_or_path: str | dict[str, Any]) -> dict[str, Any]:
        """Parse credentials from a file path, JSON string, or dict."""
        data: dict[str, Any]
        if isinstance(content_or_path, dict):
            data = content_or_path
        elif isinstance(content_or_path, str):
            if os.path.isfile(content_or_path):
                with open(content_or_path, encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = json.loads(content_or_path)
        else:
            raise TeamsAuthError("Invalid credentials format")

        # Flat format with refresh_token
        if "refresh_token" in data:
            return {
                "refresh_token": data["refresh_token"],
                "tenant_id": data.get("tenant_id", "common"),
                "client_id": data.get("client_id", TEAMS_CLIENT_ID),
                "user_display_name": data.get("user_display_name"),
                "user_email": data.get("user_email"),
                "user_mri": data.get("user_mri"),
            }

        # Extract from local_storage dict (browser-daemon export) or origins list (Playwright/MCP)
        raw_storage_values: list[str] = []
        if "local_storage" in data and isinstance(data["local_storage"], dict):
            raw_storage_values.extend(data["local_storage"].values())
        if "origins" in data and isinstance(data["origins"], list):
            for origin in data["origins"]:
                for item in origin.get("localStorage", []):
                    if item.get("value"):
                        raw_storage_values.append(item["value"])

        refresh_token = None
        tenant_id = None
        client_id = TEAMS_CLIENT_ID
        user_display_name = None
        user_email = None
        user_mri = None

        for val_str in raw_storage_values:
            try:
                entry = json.loads(val_str)
                cred_type = entry.get("credentialType")
                if cred_type == "RefreshToken" and entry.get("secret"):
                    refresh_token = entry.get("secret")
                    client_id = entry.get("clientId") or client_id
                elif cred_type == "Account":
                    user_display_name = entry.get("name") or user_display_name
                    user_email = entry.get("username") or user_email
                    user_mri = entry.get("localAccountId") or user_mri
                    if not tenant_id and entry.get("realm"):
                        tenant_id = entry.get("realm")
                elif cred_type == "IdToken":
                    if not tenant_id and entry.get("realm"):
                        tenant_id = entry.get("realm")
                    if entry.get("secret"):
                        id_payload = _decode_jwt_payload(entry["secret"])
                        user_display_name = id_payload.get("name") or user_display_name
                        user_email = (
                            id_payload.get("preferred_username")
                            or id_payload.get("upn")
                            or id_payload.get("email")
                            or user_email
                        )
                        user_mri = id_payload.get("oid") or id_payload.get("sub") or user_mri
                        if not tenant_id and id_payload.get("tid"):
                            tenant_id = id_payload.get("tid")
                elif cred_type == "AccessToken" and not tenant_id and entry.get("realm"):
                    tenant_id = entry.get("realm")
            except Exception:
                continue

        if refresh_token:
            return {
                "refresh_token": refresh_token,
                "tenant_id": tenant_id or "common",
                "client_id": client_id,
                "user_display_name": user_display_name,
                "user_email": user_email,
                "user_mri": user_mri,
            }

        raise TeamsAuthError("No valid refresh_token or session state found in credentials")

    @classmethod
    async def async_from_credentials(
        cls,
        session: aiohttp.ClientSession,
        content_or_path: str | dict[str, Any],
    ) -> TeamsAuth:
        """Create and authenticate TeamsAuth from a credentials file or dictionary."""
        creds = cls.parse_credentials(content_or_path)
        auth = cls(
            session=session,
            refresh_token=creds["refresh_token"],
            tenant_id=creds["tenant_id"],
            client_id=creds.get("client_id", TEAMS_CLIENT_ID),
            user_display_name=creds.get("user_display_name"),
            user_email=creds.get("user_email"),
            user_mri=creds.get("user_mri"),
        )
        await auth.async_refresh_tokens()
        return auth

    @staticmethod
    def _browser_daemon(
        hass: Any,
        session: aiohttp.ClientSession,
        url: str | None,
        secret: str | None,
        entry_id: str | None,
    ) -> TeamsBrowserDaemon:
        """The override's client, or the one the Browser Daemon integration provides."""
        if url:
            return TeamsBrowserDaemon(session, url, secret, client=BROWSER_DAEMON_CLIENT)
        message = "Select an available Browser Daemon integration or set an override"
        if hass is None:
            raise TeamsAuthError(message)
        try:
            from custom_components.browser_daemon import BrowserDaemonError, async_get_daemon
        except ImportError:
            raise TeamsAuthError(message) from None
        try:
            return async_get_daemon(
                hass,
                client=BROWSER_DAEMON_CLIENT,
                cls=TeamsBrowserDaemon,
                entry_id=entry_id or None,
            )
        except BrowserDaemonError:
            raise TeamsAuthError(message) from None

    @classmethod
    async def async_from_browser_daemon(
        cls,
        session: aiohttp.ClientSession,
        browser_daemon_url: str | None = None,
        profile: str = DEFAULT_PROFILE,
        secret: str | None = None,
        hass: Any | None = None,
        rbw_entry_id: str | None = None,
        rbw_item_id: str | None = None,
        prefer_passkey: bool = True,
        node_group: str | None = None,
        browser_daemon_entry_id: str | None = None,
    ) -> TeamsAuth:
        """Acquire credentials through the selected Browser Daemon integration or an override."""
        bd_client = cls._browser_daemon(
            hass, session, browser_daemon_url, secret, browser_daemon_entry_id
        )
        expected_username = None
        verified_auth: TeamsAuth | None = None

        async def login(session_id: str, tab_id: str, node: str | None) -> str:
            nonlocal expected_username
            outcome, expected_username = await async_login(
                hass,
                bd_client,
                session_id,
                tab_id,
                node,
                rbw_entry_id,
                rbw_item_id,
                prefer_passkey=prefer_passkey,
            )
            return outcome

        async def validate_credentials(creds: dict[str, Any]) -> None:
            nonlocal verified_auth
            candidate = cls(
                session=session,
                refresh_token=creds["refresh_token"],
                tenant_id=creds["tenant_id"],
                client_id=creds.get("client_id", TEAMS_CLIENT_ID),
                user_display_name=creds.get("user_display_name"),
                user_email=creds.get("user_email"),
                user_mri=creds.get("user_mri"),
            )
            candidate._is_recovering = True
            try:
                await candidate.async_refresh_tokens()
            finally:
                candidate._is_recovering = False
            identity = _decode_jwt_payload(candidate.skype_token or "")
            candidate.user_email = (
                identity.get("upn") or identity.get("email") or candidate.user_email
            )
            candidate.user_mri = (
                identity.get("skypeid") or identity.get("mri") or candidate.user_mri
            )
            actual_username = candidate.user_email or creds.get("user_email")
            if expected_username and (
                not actual_username or actual_username.casefold() != expected_username.casefold()
            ):
                raise TeamsAuthError(
                    "Browser sign-in did not authenticate the selected rbw account"
                )
            verified_auth = candidate

        try:
            _, _ = await acquire_browser_auth(
                bd_client,
                profile,
                cls.parse_credentials,
                login=login if hass is not None and rbw_entry_id and rbw_item_id else None,
                async_validate=validate_credentials,
                node_group=node_group,
            )
        except BrowserAuthError:
            raise TeamsAuthError(
                "Complete sign-in in the configured browser-daemon profile and retry the Teams repair"
            ) from None
        if verified_auth is None:
            raise TeamsAuthError("Browser credentials could not be validated")
        auth = verified_auth
        auth.hass = hass
        auth.browser_daemon_url = browser_daemon_url
        auth.browser_daemon_entry_id = browser_daemon_entry_id
        auth.browser_daemon_profile = profile
        auth.browser_daemon_node_group = node_group
        auth.browser_daemon_secret = secret
        auth.rbw_entry_id = rbw_entry_id
        auth.rbw_item_id = rbw_item_id
        auth.prefer_passkey = prefer_passkey
        auth._is_recovering = False
        return auth


_MESSAGE_TYPES = {"Text", "RichText", "RichText/Html"}
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<(?:br|/p|/div|/li)\s*/?>", re.IGNORECASE)
MAX_MESSAGE_CHARS = 500


def _html_to_text(content: str | None) -> str:
    """Flatten Teams HTML message content to plain text."""
    if not content:
        return ""
    text = _BR_RE.sub("\n", content)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _mri_from_url(value: str | None) -> str | None:
    """Extract the MRI from a Teams ``from`` contacts URL."""
    if not value:
        return None
    return value.rsplit("/contacts/", 1)[-1]


class TeamsApiClient:
    """API client for Microsoft Teams services."""

    def __init__(
        self,
        auth: TeamsAuth,
        region: str = DEFAULT_REGION,
        base_url: str = DEFAULT_TEAMS_BASE_URL,
    ) -> None:
        self.auth = auth
        self.region = region
        self.base_url = base_url.rstrip("/")
        self._profile_cache: dict[str, dict[str, Any]] = {}
        self._message_cache: dict[str, dict[str, Any]] = {}
        # Unread conversations from the latest get_active_call_and_unread() call.
        self.unread_chats: list[dict[str, Any]] = []

    async def _ensure_auth(self) -> None:
        """Ensure auth is valid before making an API call."""
        if not self.auth.is_authenticated:
            await self.auth.async_refresh_tokens()

    def _headers(self, auth_type: str = "messaging") -> dict[str, str]:
        """Construct request headers for various Teams private APIs."""
        headers = {
            "Accept": "application/json",
            "Authentication": f"skypetoken={self.auth.skype_token}",
        }
        if auth_type == "middletier":
            headers["Authorization"] = f"Bearer {self.auth.spaces_token}"
            headers["x-ms-client-version"] = MIDDLETIER_VERSION_HEADER
        elif auth_type == "messaging":
            headers["Authorization"] = f"Bearer {self.auth.csa_token or self.auth.spaces_token}"
            headers["X-Ms-Client-Version"] = CLIENT_VERSION_HEADER
            headers["Content-Type"] = "application/json"
        return headers

    async def get_presence(self) -> dict[str, Any]:
        """Fetch current user presence and status message."""
        await self._ensure_auth()
        url = "https://presence.teams.microsoft.com/v1/me/presence"
        try:
            async with self.auth.session.get(
                url, headers=self._headers("middletier"), timeout=15
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "availability": data.get("availability", "Available"),
                        "activity": data.get("activity", "Available"),
                        "status_message": data.get("statusMessage", {})
                        .get("message", {})
                        .get("content", ""),
                    }
                _LOGGER.debug("Presence endpoint returned status %s", resp.status)
        except Exception:
            _LOGGER.debug("Presence fetch failed")
        return {"availability": "Available", "activity": "Available", "status_message": ""}

    async def get_calendar_meetings(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch upcoming calendar meetings from middleTier."""
        await self._ensure_auth()
        now = datetime.now(UTC)
        start = start_date or now
        end = end_date or (start + timedelta(days=7))

        params = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "$top": str(limit),
            "$count": "true",
            "$skip": "0",
            "$orderby": "startTime asc",
            "$filter": "isAppointment eq false and isAllDayEvent eq false and isCancelled eq false",
        }
        url = f"{self.base_url}/api/mt/{self.region}/v2.1/me/calendars/calendarView"
        async with self.auth.session.get(
            url,
            headers=self._headers("middletier"),
            params=params,
            timeout=20,
        ) as resp:
            if resp.status != 200:
                raise TeamsApiError(f"Calendar API failed (HTTP {resp.status})")
            payload = await resp.json()

        raw_meetings = payload.get("value") or payload.get("data", {}).get("value", [])
        meetings: list[dict[str, Any]] = []
        for raw in raw_meetings:
            thread_id = raw.get("threadId")
            if not thread_id:
                skype_data = raw.get("skypeTeamsDataObject")
                if isinstance(skype_data, dict):
                    thread_id = skype_data.get("cid")
                elif isinstance(raw.get("skypeTeamsData"), str):
                    try:
                        parsed = json.loads(raw["skypeTeamsData"])
                        thread_id = parsed.get("cid")
                    except Exception:
                        pass

            body_preview = raw.get("bodyPreview") or ""
            desc = ""
            if body_preview:
                parts = re.split(r"_{10,}", body_preview)
                desc = parts[0].strip()

            meetings.append(
                {
                    "id": raw.get("objectId") or raw.get("id"),
                    "subject": raw.get("subject") or "(No subject)",
                    "start_time": raw.get("startTime"),
                    "end_time": raw.get("endTime"),
                    "organizer_name": raw.get("organizerName") or "Unknown",
                    "organizer_email": raw.get("organizerAddress") or "",
                    "location": raw.get("location") or "",
                    "is_online": raw.get("isOnlineMeeting") is True,
                    "is_organizer": raw.get("isOrganizer") is True,
                    "join_url": raw.get("skypeTeamsMeetingUrl"),
                    "thread_id": thread_id,
                    "my_response": raw.get("myResponseType", "None"),
                    "description": desc,
                }
            )
        return meetings

    async def get_thread_participants(self, thread_id: str) -> list[str]:
        """Fetch and resolve participant names for a meeting or chat thread."""
        if not thread_id:
            return []
        await self._ensure_auth()
        url = f"{self.base_url}/api/chatsvc/{self.region}/v1/threads/{quote(thread_id, safe='')}"
        try:
            async with self.auth.session.get(
                url, headers=self._headers("messaging"), timeout=15
            ) as resp:
                if resp.status != 200:
                    return []
                thread_info = await resp.json()
        except Exception:
            _LOGGER.debug("Failed to fetch thread info")
            return []

        members = thread_info.get("members", [])
        mris: list[str] = []
        for m in members:
            mid = m.get("id", "")
            if mid.startswith("8:orgid:") and mid not in self._profile_cache:
                mris.append(mid)

        if mris:
            await self._resolve_profiles(mris)

        names: list[str] = []
        for m in members:
            mid = m.get("id", "")
            if mid.startswith("8:orgid:"):
                prof = self._profile_cache.get(mid, {})
                display_name = prof.get("displayName")
                if display_name:
                    names.append(display_name)
            elif not mid.startswith("28:"):
                name = m.get("friendlyName") or m.get("displayName")
                if name:
                    names.append(name)
        return names

    async def get_organizer(self, organizer_id: str | None) -> dict[str, Any]:
        """Resolve a meeting organizer object ID to name, email and is_organizer."""
        if not organizer_id:
            return {}
        mri = f"8:orgid:{organizer_id}"
        if _same_user_mri(self.auth.user_mri, mri):
            return {
                "organizer_name": self.auth.user_display_name,
                "organizer_email": self.auth.user_email or "",
                "is_organizer": True,
            }
        if mri not in self._profile_cache:
            await self._resolve_profiles([mri])
        prof = self._profile_cache.get(mri, {})
        return {
            "organizer_name": prof.get("displayName"),
            "organizer_email": prof.get("email") or prof.get("userPrincipalName") or "",
            "is_organizer": False,
        }

    async def _resolve_profiles(self, mris: list[str]) -> None:
        """Resolve user MRIs via MiddleTier fetchShortProfile."""
        url = f"{self.base_url}/api/mt/{self.region}/beta/users/fetchShortProfile?isMailAddress=false&enableGuest=true&skypeTeamsInfo=true"
        headers = self._headers("middletier")
        headers["Content-Type"] = "application/json"
        try:
            async with self.auth.session.post(
                url, headers=headers, json=mris[:50], timeout=15
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for p in data.get("value", []):
                        mri = p.get("mri")
                        if mri:
                            self._profile_cache[mri] = p
        except Exception:
            _LOGGER.debug("Profile resolution failed")

    async def get_active_call_and_unread(self) -> tuple[dict[str, Any] | None, int]:
        """Fetch conversations to detect active call/meeting state and count unread chats."""
        await self._ensure_auth()
        url = f"{self.base_url}/api/chatsvc/{self.region}/v1/users/ME/conversations?filter=isPinned+eq+true+or+isPinned+eq+false&$top=30"
        active_call: dict[str, Any] | None = None
        unread_count = 0
        unread_chats: list[dict[str, Any]] = []
        try:
            async with self.auth.session.get(
                url, headers=self._headers("messaging"), timeout=15
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    convs = data.get("conversations", [])
                    for c in convs:
                        # 1. Check for active call awareness in threadProperties
                        tprops = c.get("threadProperties", {})
                        if not active_call:
                            for k, v in tprops.items():
                                if k.startswith("awareness_conversationLiveState") and isinstance(
                                    v, str
                                ):
                                    try:
                                        live_state = json.loads(v)
                                        if live_state.get("status") == "Active":
                                            thread_id = c.get("id") or ""
                                            topic = (
                                                tprops.get("topic")
                                                or c.get("properties", {}).get("displayName")
                                                or "Call"
                                            )
                                            meeting_info = live_state.get("meetingInfo", {})
                                            org_id = meeting_info.get("organizerId")
                                            tenant_id = (
                                                meeting_info.get("tenantId") or self.auth.tenant_id
                                            )
                                            join_url = ""
                                            if thread_id and org_id and tenant_id:
                                                quoted_thread = quote(thread_id, safe="")
                                                join_url = f"https://teams.microsoft.com/l/meetup-join/{quoted_thread}/0?context=%7b%22Tid%22%3a%22{tenant_id}%22%2c%22Oid%22%3a%22{org_id}%22%7d"

                                            meeting_data = live_state.get("meetingData", {})
                                            active_call = {
                                                "title": topic,
                                                "url": join_url,
                                                "thread_id": thread_id,
                                                "call_start_time": live_state.get("callStartTime"),
                                                "meeting_code": meeting_data.get("meetingCode"),
                                                "passcode": meeting_data.get("passcode"),
                                                "i_cal_uid": live_state.get("iCalUid"),
                                                "organizer_id": org_id,
                                            }
                                            break
                                    except Exception:
                                        pass

                        # 2. Check unread count
                        last_msg = c.get("lastMessage", {})
                        last_sync = c.get("properties", {}).get("consumptionhorizon", "")
                        last_msg_id = str(last_msg.get("id", ""))
                        if (
                            last_msg_id
                            and last_msg_id != last_sync
                            and not _same_user_mri(last_msg.get("from"), self.auth.user_mri)
                        ):
                            unread_count += 1
                            unread_chats.append(self._summarize_last_message(c, last_msg))
        except Exception:
            _LOGGER.debug("Failed to fetch conversations")

        self.unread_chats = unread_chats
        return active_call, unread_count

    def _summarize_last_message(
        self, conv: dict[str, Any], last_msg: dict[str, Any]
    ) -> dict[str, Any]:
        """Describe an unread conversation by its last message."""
        conv_id = conv.get("id") or ""
        tprops = conv.get("threadProperties") or {}
        sender = last_msg.get("imdisplayname") or ""
        if not sender:
            sender = (
                self._profile_cache.get(_mri_from_url(last_msg.get("from")) or "", {}).get(
                    "displayName"
                )
                or "Unknown"
            )
        kind = "chat"
        if conv_id.startswith("19:meeting_") or "meeting" in str(tprops.get("threadType", "")):
            kind = "meeting"
        elif conv_id.endswith("@thread.tacv2"):
            kind = "channel"
        text = _html_to_text(last_msg.get("content"))
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[: MAX_MESSAGE_CHARS - 1] + "…"
        return {
            "id": str(last_msg.get("id") or ""),
            "time": last_msg.get("originalarrivaltime") or last_msg.get("composetime"),
            "sender": sender,
            "text": text,
            "channel": tprops.get("topic")
            or (conv.get("properties") or {}).get("displayName")
            or sender,
            "conversation_id": conv_id,
            "kind": kind,
        }

    async def get_unread_chats_count(self) -> int:
        """Get count of chats with unread messages."""
        _, unread_count = await self.get_active_call_and_unread()
        return unread_count

    async def send_message(
        self,
        conversation_id: str,
        content: str,
        reply_to_message_id: str | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        """Send a message or thread reply to a conversation."""
        await self._ensure_auth()
        html_content = _markdown_to_teams_html(content)
        conversation_path = (
            f"{conversation_id};messageid={reply_to_message_id}"
            if reply_to_message_id and conversation_id.endswith("@thread.tacv2")
            else conversation_id
        )

        url = f"{self.base_url}/api/chatsvc/{self.region}/v1/users/ME/conversations/{quote(conversation_path, safe='')}/messages"
        payload = {
            "content": html_content,
            "messagetype": "RichText/Html",
            "contenttype": "Text",
            "clientmessageid": _generate_client_message_id(),
            "imdisplayname": self.auth.user_display_name,
            "properties": {
                "formatVariant": "TEAMS",
                "cards": "[]",
                "links": "[]",
                "files": "[]",
                "mentions": "[]",
            },
        }
        if subject:
            payload["properties"]["subject"] = subject

        async with self.auth.session.post(
            url,
            headers=self._headers("messaging"),
            json=payload,
            timeout=15,
        ) as resp:
            if resp.status not in (200, 201):
                raise TeamsApiError(f"Failed to send message (HTTP {resp.status})")
            return await resp.json()

    async def add_reaction(
        self,
        conversation_id: str,
        message_id: str,
        reaction: str = "like",
    ) -> bool:
        """Add a reaction to an existing message."""
        await self._ensure_auth()
        key = REACTIONS.get(reaction.lower(), reaction)
        url = (
            f"{self.base_url}/api/chatsvc/{self.region}/v1/users/ME/conversations/"
            f"{quote(conversation_id, safe='')}/messages/{quote(str(message_id), safe='')}/properties?name=emotions"
        )
        payload = {
            "emotions": [
                {
                    "key": key,
                    "users": [
                        {
                            "mri": self.auth.user_mri or "",
                            "time": int(time.time() * 1000),
                        }
                    ],
                }
            ]
        }
        async with self.auth.session.put(
            url,
            headers=self._headers("messaging"),
            json=payload,
            timeout=15,
        ) as resp:
            return resp.status in (200, 201)

    async def get_recent_messages(
        self,
        limit: int = 100,
        max_conversations: int = 25,
        per_conversation: int = 30,
    ) -> list[dict[str, Any]]:
        """Return the newest messages across the most recently active conversations.

        Messages are cached per conversation and only refetched when the
        conversation's last message changed, so push-triggered refreshes stay cheap.
        """
        await self._ensure_auth()
        url = (
            f"{self.base_url}/api/chatsvc/{self.region}/v1/users/ME/conversations"
            f"?filter=isPinned+eq+true+or+isPinned+eq+false&$top={max_conversations}"
        )
        async with self.auth.session.get(
            url, headers=self._headers("messaging"), timeout=20
        ) as resp:
            if resp.status != 200:
                raise TeamsApiError(f"Conversations API failed (HTTP {resp.status})")
            convs = (await resp.json()).get("conversations", [])

        convs = [c for c in convs if c.get("id")]
        convs.sort(
            key=lambda c: (c.get("lastMessage") or {}).get("composetime") or "", reverse=True
        )
        convs = convs[:max_conversations]

        cache = self._message_cache
        stale = [
            c
            for c in convs
            if cache.get(c["id"], {}).get("marker") != str((c.get("lastMessage") or {}).get("id"))
        ]
        results = await asyncio.gather(
            *(self._fetch_conversation_messages(c, per_conversation) for c in stale),
            return_exceptions=True,
        )
        for conv, result in zip(stale, results, strict=True):
            if isinstance(result, Exception):
                _LOGGER.debug(
                    "Fetching messages failed for a conversation: %s", type(result).__name__
                )
                continue
            cache[conv["id"]] = {
                "marker": str((conv.get("lastMessage") or {}).get("id")),
                "messages": result,
            }

        live_ids = {c["id"] for c in convs}
        for stale_id in set(cache) - live_ids:
            del cache[stale_id]

        merged = [m for c in convs for m in cache.get(c["id"], {}).get("messages", [])]
        merged.sort(key=lambda m: m.get("time") or "", reverse=True)
        return merged[:limit]

    async def _fetch_conversation_messages(
        self, conv: dict[str, Any], page_size: int
    ) -> list[dict[str, Any]]:
        """Fetch and normalise the newest messages of one conversation."""
        conv_id = conv["id"]
        url = (
            f"{self.base_url}/api/chatsvc/{self.region}/v1/users/ME/conversations/"
            f"{quote(conv_id, safe='')}/messages"
        )
        async with self.auth.session.get(
            url,
            headers=self._headers("messaging"),
            params={"startTime": "1", "pageSize": str(page_size), "view": "msnp24Equivalent"},
            timeout=20,
        ) as resp:
            if resp.status != 200:
                raise TeamsApiError(f"Messages API failed (HTTP {resp.status})")
            raw_messages = (await resp.json()).get("messages", [])

        tprops = conv.get("threadProperties") or {}
        kind = "chat"
        if conv_id.startswith("19:meeting_") or "meeting" in str(tprops.get("threadType", "")):
            kind = "meeting"
        elif conv_id.endswith("@thread.tacv2"):
            kind = "channel"
        topic = tprops.get("topic") or (conv.get("properties") or {}).get("displayName")

        out: list[dict[str, Any]] = []
        other_names: list[str] = []
        for raw in raw_messages:
            if raw.get("messagetype") not in _MESSAGE_TYPES:
                continue
            from_mri = _mri_from_url(raw.get("from"))
            mine = _same_user_mri(from_mri, self.auth.user_mri)
            sender = raw.get("imdisplayname") or ""
            if not sender and from_mri:
                sender = self._profile_cache.get(from_mri, {}).get("displayName") or ""
            if sender and not mine and sender not in other_names:
                other_names.append(sender)
            text = _html_to_text(raw.get("content"))
            if len(text) > MAX_MESSAGE_CHARS:
                text = text[: MAX_MESSAGE_CHARS - 1] + "…"
            out.append(
                {
                    "id": str(raw.get("id") or ""),
                    "time": raw.get("originalarrivaltime") or raw.get("composetime"),
                    "sender": sender or "Unknown",
                    "from_me": mine,
                    "text": text,
                    "subject": (raw.get("properties") or {}).get("subject") or None,
                    "conversation_id": conv_id,
                    "kind": kind,
                }
            )

        channel = topic or ", ".join(other_names[:3]) or "Chat"
        for msg in out:
            msg["channel"] = channel
        return out
