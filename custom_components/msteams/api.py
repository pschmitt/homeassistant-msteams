"""Microsoft Teams and browser-daemon API client."""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    AUTHSVC_ENDPOINT,
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
    ) -> None:
        self.session = session
        self.refresh_token = refresh_token
        self.tenant_id = tenant_id or "common"
        self.client_id = client_id or TEAMS_CLIENT_ID
        self.user_mri = user_mri
        self.user_display_name = user_display_name or "User"
        self.user_email = user_email

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

    async def async_refresh_tokens(self) -> bool:
        """Refresh access tokens using the refresh token."""
        if not self.refresh_token:
            raise TeamsAuthError("No refresh token available to refresh authentication")

        token_url = TOKEN_ENDPOINT.format(tenantId=self.tenant_id)
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
                async with self.session.post(token_url, data=data, timeout=15) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        _LOGGER.warning(
                            "Token refresh failed for %s (%s): %s", key, resp.status, err_text
                        )
                        if resp.status in (400, 401):
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
        return True

    async def _async_exchange_skype_token(self, spaces_access_token: str) -> None:
        """Exchange Skype Spaces token for skypetoken via authsvc."""
        payload = {
            "claim_type": "urn:ietf:params:oauth:token-type:jwt",
            "client_type": "SkypeSpaces",
            "access_token": spaces_access_token,
        }
        try:
            async with self.session.post(AUTHSVC_ENDPOINT, json=payload, timeout=15) as resp:
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
    async def async_from_browser_daemon(
        cls,
        session: aiohttp.ClientSession,
        browser_daemon_url: str,
        profile: str = "work",
        secret: str | None = None,
    ) -> TeamsAuth:
        """Connect to browser-daemon to extract Teams session cookies & localStorage."""
        base = browser_daemon_url.rstrip("/")
        headers = {}
        if secret:
            headers["X-Browser-Cluster-Secret"] = secret

        # 1. Fetch mesh overview to find node and running sessions
        overview_url = f"{base}/api/mesh/overview"
        async with session.get(overview_url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                raise TeamsAuthError(f"browser-daemon overview returned HTTP {resp.status}")
            overview = await resp.json()

        profiles = overview.get("profiles", [])
        prof = next((p for p in profiles if p.get("name") == profile), None)
        if not prof:
            raise TeamsAuthError(f"browser-daemon profile '{profile}' not found")

        node_name = prof.get("node_name")
        node_url = (prof.get("node_url") or base).rstrip("/")
        session_id = prof.get("session_id")
        targets = overview.get("targets", [])

        # Find existing Teams page target
        target_page_id = None
        for target in targets:
            url = target.get("url", "")
            if target.get("sessionId") == session_id and (
                "teams.microsoft.com" in url or "teams.cloud.microsoft" in url
            ):
                target_page_id = target.get("id")
                break

        # If no page target, open Teams tab in session
        if not target_page_id:
            dev_prefix = (
                ""
                if node_name == overview.get("local_node", {}).get("name")
                else f"/api/peers/{quote(node_name)}"
            )
            new_tab_url = (
                f"{base}{dev_prefix}/json/new?url={quote('https://teams.microsoft.com/v2/')}"
            )
            if session_id:
                new_tab_url += f"&session_id={session_id}"
            async with session.put(new_tab_url, headers=headers, timeout=20) as resp:
                if resp.status != 200:
                    raise TeamsAuthError(
                        f"Could not open Teams tab in browser-daemon: HTTP {resp.status}"
                    )
                tab_data = await resp.json()
                target_page_id = tab_data.get("id")

        if not target_page_id:
            raise TeamsAuthError("Failed to locate or open Microsoft Teams page in browser-daemon")

        # 2. Extract auth storage from page
        auth_url = f"{node_url}/api/pages/{quote(target_page_id)}/auth"
        auth_payload = {"session_id": session_id, "urls": [], "storage": True}
        async with session.post(auth_url, headers=headers, json=auth_payload, timeout=25) as resp:
            if resp.status != 200:
                raise TeamsAuthError(
                    f"Exporting page auth from browser-daemon failed: HTTP {resp.status}"
                )
            login_data = await resp.json()

        local_storage = login_data.get("local_storage", {})
        refresh_token = None
        tenant_id = None
        client_id = TEAMS_CLIENT_ID
        user_mri = None
        user_display_name = None
        user_email = None

        for key, val_str in local_storage.items():
            try:
                item = json.loads(val_str)
                cred_type = item.get("credentialType")
                if cred_type == "RefreshToken" and item.get("secret"):
                    refresh_token = item.get("secret")
                    client_id = item.get("clientId") or client_id
                    home_account_id = item.get("homeAccountId", "")
                    if "." in home_account_id:
                        tenant_id = home_account_id.split(".")[1]
                elif cred_type == "Account":
                    user_display_name = item.get("name") or user_display_name
                    user_email = item.get("username") or user_email
                    user_mri = item.get("localAccountId") or user_mri
                    if not tenant_id and item.get("realm"):
                        tenant_id = item.get("realm")
            except Exception:
                continue

        if not refresh_token:
            raise TeamsAuthError("No RefreshToken found in browser-daemon Teams session storage")

        auth = cls(
            session=session,
            refresh_token=refresh_token,
            tenant_id=tenant_id or "common",
            client_id=client_id,
            user_mri=user_mri,
            user_display_name=user_display_name,
            user_email=user_email,
        )
        await auth.async_refresh_tokens()
        return auth


class TeamsApiClient:
    """API client for Microsoft Teams services and browser-daemon meeting controls."""

    def __init__(
        self,
        auth: TeamsAuth,
        region: str = DEFAULT_REGION,
        base_url: str = DEFAULT_TEAMS_BASE_URL,
        browser_daemon_url: str | None = None,
        browser_daemon_secret: str | None = None,
        profile: str = "work",
    ) -> None:
        self.auth = auth
        self.region = region
        self.base_url = base_url.rstrip("/")
        self.browser_daemon_url = browser_daemon_url.rstrip("/") if browser_daemon_url else None
        self.browser_daemon_secret = browser_daemon_secret
        self.profile = profile

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
        url = f"{self.base_url}/api/mt/{self.region}/v2.0/me/presence"
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

        raw_meetings = payload.get("data", {}).get("value", [])
        meetings: list[dict[str, Any]] = []
        for raw in raw_meetings:
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
                    "join_url": raw.get("skypeTeamsMeetingUrl"),
                    "thread_id": raw.get("threadId"),
                    "my_response": raw.get("myResponseType", "None"),
                }
            )
        return meetings

    async def get_unread_chats_count(self) -> int:
        """Get count of chats with unread messages."""
        await self._ensure_auth()
        url = f"{self.base_url}/api/chatsvc/{self.region}/v1/users/ME/conversations?filter=isPinned+eq+true+or+isPinned+eq+false&$top=30"
        try:
            async with self.auth.session.get(
                url, headers=self._headers("messaging"), timeout=15
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    convs = data.get("conversations", [])
                    unread_count = 0
                    for c in convs:
                        last_msg = c.get("lastMessage", {})
                        last_sync = c.get("properties", {}).get("consumptionhorizon", "")
                        last_msg_id = str(last_msg.get("id", ""))
                        if (
                            last_msg_id
                            and last_msg_id != last_sync
                            and last_msg.get("from") != self.auth.user_mri
                        ):
                            unread_count += 1
                    return unread_count
        except Exception as err:
            _LOGGER.debug("Failed to calculate unread chats count: %s", err)
        return 0

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

    # ─────────────────────────────────────────────────────────────────────────
    # Browser-Daemon Meeting Detection & Controls
    # ─────────────────────────────────────────────────────────────────────────

    async def _evaluate_on_teams_page(self, expression: str) -> Any:
        """Run a JavaScript expression on an active Teams page in browser-daemon."""
        if not self.browser_daemon_url:
            return None

        base = self.browser_daemon_url.rstrip("/")
        headers = {}
        if self.browser_daemon_secret:
            headers["X-Browser-Cluster-Secret"] = self.browser_daemon_secret

        # Fetch overview
        async with self.auth.session.get(
            f"{base}/api/mesh/overview", headers=headers, timeout=10
        ) as resp:
            if resp.status != 200:
                return None
            overview = await resp.json()

        profiles = overview.get("profiles", [])
        prof = next((p for p in profiles if p.get("name") == self.profile), None)
        session_id = prof.get("session_id") if prof else None

        # Look for active Teams page
        targets = overview.get("targets", [])
        target_page_id = None
        for target in targets:
            url = target.get("url", "")
            if (not session_id or target.get("sessionId") == session_id) and (
                "teams.microsoft.com" in url or "teams.cloud.microsoft" in url
            ):
                target_page_id = target.get("id")
                break

        if not target_page_id:
            return None

        node_name = prof.get("node_name") if prof else overview.get("local_node", {}).get("name")
        dev_prefix = (
            ""
            if node_name == overview.get("local_node", {}).get("name")
            else f"/api/peers/{quote(node_name)}"
        )
        eval_url = f"{base}{dev_prefix}/api/pages/{quote(target_page_id)}/eval"

        payload = {"expression": expression}
        async with self.auth.session.post(
            eval_url, headers=headers, json=payload, timeout=10
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("result", {}).get("value")
        return None

    async def get_live_meeting_status(self) -> dict[str, Any]:
        """Detect live meeting status, mic mute state and title via browser-daemon."""
        js_code = """
        (() => {
            const micBtn = document.querySelector('button#microphone-button, button[data-tid="microphone-button"], button[aria-label*="Mute" i], button[aria-label*="mic" i], button[aria-label*="Mikrofon" i]');
            const callScreen = document.querySelector('[data-tid="calling-screen"], [data-tid="call-control-bar"], [data-tid="calling-dock"]');
            const inMeeting = !!micBtn || !!callScreen || window.location.href.includes('meetup-join');

            let isMuted = false;
            if (micBtn) {
                const label = (micBtn.getAttribute('aria-label') || '').toLowerCase();
                isMuted = label.includes('unmute') || label.includes('aufheben') || micBtn.getAttribute('aria-pressed') === 'true';
            }

            const titleEl = document.querySelector('[data-tid="call-title"], h1');
            let meetingTitle = titleEl ? titleEl.textContent.trim() : document.title;
            meetingTitle = meetingTitle.replace(/ \\| Microsoft Teams$/i, '').trim();

            return {
                in_meeting: inMeeting,
                is_muted: isMuted,
                title: meetingTitle,
                url: window.location.href
            };
        })()
        """
        try:
            res = await self._evaluate_on_teams_page(js_code)
            if isinstance(res, dict):
                return res
        except Exception as err:
            _LOGGER.debug("Live meeting status evaluation failed: %s", err)

        return {
            "in_meeting": False,
            "is_muted": False,
            "title": "",
            "url": "",
        }

    async def toggle_meeting_mute(self) -> bool:
        """Toggle microphone mute in active Microsoft Teams meeting."""
        js_code = """
        (() => {
            const micBtn = document.querySelector('button#microphone-button, button[data-tid="microphone-button"], button[aria-label*="Mute" i], button[aria-label*="mic" i], button[aria-label*="Mikrofon" i]');
            if (micBtn) {
                micBtn.click();
                return true;
            }
            return false;
        })()
        """
        res = await self._evaluate_on_teams_page(js_code)
        return bool(res)

    async def set_meeting_mute(self, mute: bool) -> bool:
        """Set microphone mute state to muted or unmuted in active meeting."""
        js_code = f"""
        (() => {{
            const micBtn = document.querySelector('button#microphone-button, button[data-tid="microphone-button"], button[aria-label*="Mute" i], button[aria-label*="mic" i], button[aria-label*="Mikrofon" i]');
            if (!micBtn) return false;
            const label = (micBtn.getAttribute('aria-label') || '').toLowerCase();
            const currentMuted = label.includes('unmute') || label.includes('aufheben') || micBtn.getAttribute('aria-pressed') === 'true';
            const desiredMuted = {"true" if mute else "false"};
            if (currentMuted !== desiredMuted) {{
                micBtn.click();
                return true;
            }}
            return true;
        }})()
        """
        res = await self._evaluate_on_teams_page(js_code)
        return bool(res)
