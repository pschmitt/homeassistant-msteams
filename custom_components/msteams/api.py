"""Microsoft Teams and browser-daemon API client."""

from __future__ import annotations

import asyncio
import base64
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

from .const import (
    AUTHSVC_ENDPOINT,
    DEFAULT_BROWSER_DAEMON_URL,
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
    except Exception as err:
        _LOGGER.debug("Could not decode JWT payload: %s", err)
        return {}


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
        browser_daemon_profile: str = DEFAULT_PROFILE,
        browser_daemon_secret: str | None = None,
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
        self.browser_daemon_profile = browser_daemon_profile
        self.browser_daemon_secret = browser_daemon_secret
        self._is_recovering = False

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
        if self._is_recovering:
            return False
        if not self.hass and not self.browser_daemon_url:
            return False

        self._is_recovering = True
        try:
            new_auth = await self.async_from_browser_daemon(
                session=self.session,
                browser_daemon_url=self.browser_daemon_url,
                profile=self.browser_daemon_profile,
                secret=self.browser_daemon_secret,
                hass=self.hass,
            )
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
                except Exception as err:
                    _LOGGER.warning("Error in on_tokens_refreshed callback: %s", err)

            _LOGGER.info(
                "Successfully recovered Teams authentication via browser-daemon for %s",
                self.user_display_name,
            )
            return True
        except Exception as err:
            _LOGGER.warning("Could not recover Teams authentication via browser-daemon: %s", err)
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
                        err_text = await resp.text()
                        _LOGGER.warning(
                            "Token refresh failed for %s (%s): %s", key, resp.status, err_text
                        )
                        if resp.status in (400, 401):
                            if await self._async_recover_via_browser_daemon():
                                return True
                            raise TeamsAuthError(f"OAuth token refresh rejected: {err_text}")
                        continue
                    payload = await resp.json()
                    new_tokens[key] = payload.get("access_token")
                    if "refresh_token" in payload:
                        current_refresh_token = payload["refresh_token"]
                    if "expires_in" in payload:
                        expires_in_seconds = min(expires_in_seconds, int(payload["expires_in"]))
            except aiohttp.ClientError as err:
                raise TeamsAuthError(f"Network error refreshing tokens: {err}") from err

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

        _LOGGER.info("Successfully refreshed Microsoft Teams tokens for %s", self.user_display_name)

        if self.on_tokens_refreshed:
            try:
                res = self.on_tokens_refreshed(self.export_data())
                if asyncio.iscoroutine(res):
                    await res
            except Exception as err:
                _LOGGER.warning("Error in on_tokens_refreshed callback: %s", err)

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
                    text = await resp.text()
                    raise TeamsAuthError(f"Skype token exchange failed ({resp.status}): {text}")
                data = await resp.json()
                self.skype_token = data.get("tokens", {}).get("skypeToken")
                if not self.skype_token:
                    raise TeamsAuthError("Skype token exchange did not return skypeToken")
        except aiohttp.ClientError as err:
            raise TeamsAuthError(f"Network error exchanging Skype token: {err}") from err

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

    @classmethod
    async def async_from_browser_daemon(
        cls,
        session: aiohttp.ClientSession,
        browser_daemon_url: str | None = None,
        profile: str = DEFAULT_PROFILE,
        secret: str | None = None,
        hass: Any | None = None,
    ) -> TeamsAuth:
        """Acquire credentials automatically via browser-daemon."""
        bd_client = None
        if hass is not None:
            try:
                from custom_components.browser_daemon import async_get_client

                bd_client = async_get_client(hass)
            except Exception as err:
                _LOGGER.debug("Could not get browser-daemon client from hass: %s", err)

        if bd_client is None:
            try:
                from custom_components.browser_daemon.api import BrowserDaemonClient
            except ImportError as err:
                raise TeamsAuthError("browser_daemon integration not available") from err

            url = browser_daemon_url or DEFAULT_BROWSER_DAEMON_URL
            bd_client = BrowserDaemonClient(
                session=session,
                base_url=url,
                cluster_secret=secret,
            )

        # 1. Fetch overview
        try:
            overview = await bd_client.async_get_overview()
        except Exception as err:
            raise TeamsAuthError(f"Failed to connect to browser-daemon: {err}") from err

        profiles = overview.get("profiles", [])
        prof = next((p for p in profiles if p.get("name") == profile), None)
        if not prof:
            raise TeamsAuthError(f"browser-daemon has no profile '{profile}'")

        node_name = prof.get("node_name")
        session_id = prof.get("session_id")
        created_session_id: str | None = None

        if not session_id:
            try:
                session_info = await bd_client.async_create_session(
                    profile=profile,
                    engine="chromium",
                    default_tabs=False,
                    node=node_name,
                )
                created_session_id = session_info["id"]
                session_id = created_session_id
            except Exception as err:
                raise TeamsAuthError(
                    f"Failed to start browser-daemon session for profile '{profile}': {err}"
                ) from err

        tab_id: str | None = None
        opened_new_tab = False
        try:
            # 2. Check overview targets for an existing Teams tab in this session
            for target in overview.get("targets", []):
                if (
                    target.get("sessionId") == session_id
                    and target.get("type") == "page"
                    and _is_teams_host(target.get("url"))
                ):
                    tab_id = target.get("id")
                    break

            if not tab_id:
                try:
                    tabs = await bd_client.async_get_tabs(node=node_name)
                    for t in tabs:
                        t_session = t.get("session_id")
                        t_url = t.get("url", "")
                        if (session_id is None or t_session == session_id) and _is_teams_host(t_url):
                            tab_id = t.get("id")
                            break
                except Exception as err:
                    _LOGGER.debug("Could not query open tabs: %s", err)

            if not tab_id:
                new_tab = await bd_client.async_open_tab(
                    DEFAULT_TEAMS_BASE_URL, session_id=session_id, node=node_name
                )
                tab_id = new_tab.get("id")
                opened_new_tab = True

            # 3. Export auth with polling to allow page navigation and storage population
            creds = None
            max_wait = 45
            start_time = time.time()
            last_err: Exception | None = None

            while time.time() - start_time < max_wait:
                try:
                    auth_data = await bd_client.async_export_auth(
                        tab_id,
                        session_id=session_id,
                        urls=[],
                        storage=True,
                        node=node_name,
                    )
                    url = auth_data.get("url", "")
                    if _is_teams_host(url) and auth_data.get("local_storage"):
                        try:
                            parsed = cls.parse_credentials(auth_data)
                            if parsed.get("refresh_token"):
                                creds = parsed
                                break
                        except TeamsAuthError:
                            pass
                except Exception as err:
                    last_err = err
                    _LOGGER.debug("Waiting for Teams auth export: %s", err)
                await asyncio.sleep(2)

            if not creds:
                raise TeamsAuthError(
                    f"Timed out waiting for Teams authentication from browser-daemon: {last_err or 'RefreshToken not found in page storage'}"
                )

        except TeamsAuthError:
            raise
        except Exception as err:
            raise TeamsAuthError(
                f"Failed to extract Teams auth from browser-daemon: {err}"
            ) from err
        finally:
            if created_session_id:
                try:
                    await bd_client.async_delete_session(created_session_id, node=node_name)
                except Exception as err:
                    _LOGGER.debug(
                        "Failed to clean up temporary browser session %s: %s",
                        created_session_id,
                        err,
                    )
            elif opened_new_tab and tab_id:
                try:
                    await bd_client.async_close_tab(tab_id, node=node_name)
                except Exception as err:
                    _LOGGER.debug("Failed to close temporary tab %s: %s", tab_id, err)

        auth = cls(
            session=session,
            refresh_token=creds["refresh_token"],
            tenant_id=creds["tenant_id"],
            client_id=creds.get("client_id", TEAMS_CLIENT_ID),
            user_display_name=creds.get("user_display_name"),
            user_email=creds.get("user_email"),
            user_mri=creds.get("user_mri"),
            hass=hass,
            browser_daemon_url=browser_daemon_url,
            browser_daemon_profile=profile,
            browser_daemon_secret=secret,
        )
        auth._is_recovering = True
        await auth.async_refresh_tokens()
        auth._is_recovering = False
        return auth


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
        except Exception as err:
            _LOGGER.debug("Presence fetch failed: %s", err)
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
                text = await resp.text()
                raise TeamsApiError(f"Calendar API failed ({resp.status}): {text}")
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
        except Exception as err:
            _LOGGER.debug("Failed to fetch thread info for %s: %s", thread_id, err)
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
        except Exception as err:
            _LOGGER.debug("Profile resolution failed: %s", err)

    async def get_active_call_and_unread(self) -> tuple[dict[str, Any] | None, int]:
        """Fetch conversations to detect active call/meeting state and count unread chats."""
        await self._ensure_auth()
        url = f"{self.base_url}/api/chatsvc/{self.region}/v1/users/ME/conversations?filter=isPinned+eq+true+or+isPinned+eq+false&$top=30"
        active_call: dict[str, Any] | None = None
        unread_count = 0
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
                            and last_msg.get("from") != self.auth.user_mri
                        ):
                            unread_count += 1
        except Exception as err:
            _LOGGER.debug("Failed to fetch conversations: %s", err)

        return active_call, unread_count

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
            "contenttype": "text",
            "clientmessageid": _generate_client_message_id(),
            "imdisplayname": self.auth.user_display_name,
        }
        if subject:
            payload["subject"] = subject

        async with self.auth.session.post(
            url,
            headers=self._headers("messaging"),
            json=payload,
            timeout=15,
        ) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise TeamsApiError(f"Failed to send message ({resp.status}): {text}")
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
