"""Shared browser-daemon Teams session acquisition."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
from browser_daemon_client import (
    DEFAULT_SECRET_FILE,
    BrowserDaemon,
    BrowserDaemonError,
    read_secret,
)

_LOGGER = logging.getLogger(__name__)
DEFAULT_TEAMS_BASE_URL = "https://teams.microsoft.com"
LOGIN_GRACE_SECONDS = 10
PROFILE_LABEL = "ha"
PROFILE_LABEL_ICON = {"kind": "mdi", "name": "home-assistant"}
PROFILE_ICON = Path(__file__).parent / "brand" / "icon.png"


async def _brand_profile(
    bd_client: BrowserDaemon, profile: str, node: str | None, current: dict[str, Any]
) -> None:
    """Give a profile without a picture the Teams logo, and one without labels the
    `ha` label (what the user set is kept). Never raises."""
    if not profile:
        return
    try:
        if not current:
            current = await bd_client.find_profile(profile) or {}
            node = current.get("node_name") or node
        if not current:
            return
        name = quote(profile, safe="")
        if not current.get("icon_rev"):
            data = await asyncio.to_thread(PROFILE_ICON.read_bytes)
            await bd_client._request(
                "PUT",
                f"/api/profiles/{name}/icon",
                node=node,
                data=aiohttp.BytesPayload(data, content_type="image/png"),
            )
        if not current.get("labels"):
            await bd_client._json(
                "PATCH", f"/api/profiles/{name}", node=node, json={"labels": [PROFILE_LABEL]}
            )
            # The `ha` label gets the Home Assistant icon, unless it was ever defined.
            registry = await bd_client._json("GET", "/api/labels")
            if PROFILE_LABEL not in (registry or {}).get("defs", {}):
                await bd_client._json(
                    "PUT", f"/api/labels/{PROFILE_LABEL}", json={"icon": PROFILE_LABEL_ICON}
                )
    except (BrowserDaemonError, OSError):
        _LOGGER.debug("Could not brand the browser-daemon profile", exc_info=True)


class BrowserAuthError(Exception):
    """Browser session cannot provide Teams authentication."""


class BrowserInteractionRequired(BrowserAuthError):
    """The retained browser page needs attention through browser-daemon."""


def _is_teams_host(url: str | None) -> bool:
    if not url:
        return False
    return urlparse(url).netloc.lower() in (
        "teams.microsoft.com",
        "teams.cloud.microsoft",
        "login.microsoftonline.com",
    )


async def acquire_browser_auth(
    bd_client: BrowserDaemon,
    profile: str,
    validate: Callable[[dict[str, Any]], dict[str, Any]],
    login: Callable[[str, str, str | None], Awaitable[str]] | None = None,
    async_validate: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    node_group: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Acquire auth state via a browser-daemon client and release owned resources."""
    profile = profile.strip()
    placement = {}
    try:
        if node_group and node_group.strip():
            placement = await bd_client._json(
                "POST",
                "/api/mesh/placement",
                json={
                    "profile": profile or None,
                    "group": node_group.strip(),
                    "engine": "chromium",
                    "create": True,
                },
                timeout=180,
            )
        overview = await bd_client.overview()
    except BrowserDaemonError:
        raise BrowserAuthError("Failed to connect to browser-daemon") from None

    profiles = [p for p in overview.get("profiles", []) if profile and p.get("name") == profile]
    if len(profiles) > 1:
        raise BrowserAuthError("browser-daemon has duplicate copies of the configured profile")
    prof = profiles[0] if profiles else {}
    node_name = prof.get("node_name") or placement.get("node")
    session_id = prof.get("session_id")
    created_session_id: str | None = None

    if not session_id:
        try:
            session_info = await bd_client.create_session(
                profile=profile or None,
                persistent=bool(profile),
                engine="chromium",
                default_tabs=False,
                node=node_name,
                idle_timeout_minutes=5,
            )
            created_session_id = session_info["id"]
            session_id = created_session_id
        except BrowserDaemonError:
            raise BrowserAuthError("Failed to start browser-daemon session") from None
    await _brand_profile(bd_client, profile, node_name, prof if not created_session_id else {})
    tab_id: str | None = None
    opened_new_tab = False
    retain_for_login = False
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
                tabs = await bd_client.tabs(node=node_name)
                for t in tabs:
                    t_session = t.get("session_id")
                    t_url = t.get("url", "")
                    if (session_id is None or t_session == session_id) and _is_teams_host(t_url):
                        tab_id = t.get("id")
                        break
            except Exception:
                _LOGGER.debug("Could not query open tabs")

        if not tab_id:
            new_tab = await bd_client.open_tab(
                DEFAULT_TEAMS_BASE_URL, session_id=session_id, node=node_name
            )
            tab_id = new_tab.get("id")
            opened_new_tab = True

        # Interactive login must use a tab owned by this acquisition, never a user's tab.
        if login is not None and not opened_new_tab:
            new_tab = await bd_client.open_tab(
                DEFAULT_TEAMS_BASE_URL, session_id=session_id, node=node_name
            )
            tab_id = new_tab.get("id")
            opened_new_tab = True

        # 3. Export auth with polling to allow page navigation and storage population
        creds = None
        max_wait = 45
        start_time = time.monotonic()
        login_ready_at = start_time + LOGIN_GRACE_SECONDS
        login_attempted = False

        while time.monotonic() - start_time < max_wait:
            try:
                auth_data = await bd_client.export_auth(
                    tab_id,
                    session_id=session_id,
                    urls=[],
                    storage=True,
                    node=node_name,
                )
                url = auth_data.get("url", "")
                if _is_teams_host(url) and auth_data.get("local_storage"):
                    try:
                        parsed = validate(auth_data)
                        if parsed.get("refresh_token") or parsed.get("spaces_token"):
                            if async_validate is not None:
                                await async_validate(parsed)
                            creds = parsed
                            retain_for_login = False
                            break
                    except Exception:
                        pass
            except Exception:
                _LOGGER.debug("Waiting for Teams authentication export")
            if login is not None and not login_attempted and time.monotonic() >= login_ready_at:
                login_attempted = True
                retain_for_login = True
                outcome = await login(session_id, tab_id, node_name)
                if outcome != "authenticated":
                    raise BrowserInteractionRequired(
                        "Complete sign-in in the configured browser-daemon profile, then retry the Teams repair"
                    )
                start_time = time.monotonic()
            await asyncio.sleep(2)

        if not creds:
            raise BrowserAuthError("Timed out waiting for Teams authentication from browser-daemon")

    except BrowserAuthError:
        raise
    except Exception:
        raise BrowserAuthError("Failed to acquire Teams browser authentication") from None
    finally:
        if retain_for_login and created_session_id:
            try:
                await bd_client.set_idle_timeout(session_id, 15, node=node_name)
            except BrowserDaemonError:
                _LOGGER.debug("Could not extend the login session timeout")
        if opened_new_tab and tab_id and not retain_for_login:
            try:
                await bd_client.close_tab(tab_id, node=node_name)
            except Exception:
                _LOGGER.debug("Failed to close temporary Teams tab")

        target_sid = created_session_id or session_id
        if target_sid and not retain_for_login:
            in_use = not bool(created_session_id)
            try:
                # BrowserDaemon.__init__ stores the HTTP ClientSession on
                # `session`, shadowing its method of the same name.
                response = await BrowserDaemon.session(bd_client, target_sid, node=node_name)
                s_info = response["session"]
                if (
                    s_info.get("client_count") != 0
                    or s_info.get("clients")
                    or s_info.get("viewer_count", 0) > 0
                ):
                    in_use = True
                tabs = await bd_client.tabs(node=node_name)
                if any(
                    (t.get("sessionId") or t.get("session_id")) == target_sid and t.get("owner")
                    for t in tabs
                ):
                    in_use = True
            except Exception:
                _LOGGER.debug("Could not check browser session usage")
                in_use = True

            if not in_use:
                try:
                    await bd_client.delete_session(target_sid, node=node_name)
                except Exception:
                    _LOGGER.debug("Failed to delete unused browser session")
            # Attached sessions belong to the user. Leave their timeout unchanged.

    return auth_data, creds


def _validate_refresh_token(data: dict[str, Any]) -> dict[str, Any]:
    """Wait for either the refresh credential or unexpired access tokens."""
    import base64
    import json

    refresh_token = None
    refresh_expires = None
    valid_substrate = False
    valid_spaces = False
    spaces_token = None
    for value in (data.get("local_storage") or {}).values():
        try:
            entry = json.loads(value)
            if (
                entry.get("credentialType") == "RefreshToken"
                and entry.get("secret")
                and entry.get("clientId")
            ):
                refresh_token = entry["secret"]
                refresh_expires = entry.get("expiresOn")
            target = entry.get("target", "")
            if "SubstrateSearch" in target:
                token = entry.get("secret", "")
                if not token.startswith("ey"):
                    continue
                payload = token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                if isinstance(claims.get("exp"), (int, float)) and claims["exp"] > time.time():
                    valid_substrate = True
            if "spaces.skype.com" in target:
                token = entry.get("secret", "")
                if not token.startswith("ey"):
                    continue
                payload = token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                if isinstance(claims.get("exp"), (int, float)) and claims["exp"] > time.time():
                    valid_spaces = True
                    spaces_token = token
        except (ValueError, AttributeError, TypeError, IndexError):
            continue

    if refresh_token and valid_substrate:
        try:
            if refresh_expires is not None and float(refresh_expires) < time.time() + 3600:
                raise BrowserAuthError(
                    "Teams refresh token is (almost) expired; waiting for renewal"
                )
        except (TypeError, ValueError):
            pass
        return {"refresh_token": refresh_token, "spaces_token": spaces_token}

    if valid_substrate and valid_spaces and spaces_token:
        # Cache encrypted by MSAL Browser, but unexpired access tokens are available.
        return {"refresh_token": "", "spaces_token": spaces_token}

    raise BrowserAuthError("Teams session is not ready")


async def _main() -> int:
    """CLI writes session material only to the caller's stdout pipe."""
    import json
    import os
    import sys
    from urllib.parse import quote

    import aiohttp

    url = os.environ.get("TEAMS_MCP_BROWSER_DAEMON_URL")
    if not url:
        return 2
    secret = read_secret(os.environ.get("BROWSER_DAEMON_SECRET_FILE", DEFAULT_SECRET_FILE))
    async with aiohttp.ClientSession() as session:
        if not secret:
            async with session.get(url.rstrip("/") + "/", timeout=10):
                pass
        client = BrowserDaemon(session, url, secret, client="Home Assistant · Teams")
        data, creds = await acquire_browser_auth(
            client,
            os.environ.get("TEAMS_MCP_BROWSER_DAEMON_PROFILE", "work"),
            _validate_refresh_token,
        )

        spaces_token = creds.get("spaces_token")
        cookies = data.setdefault("cookies", [])
        has_skype = any(c.get("name") == "skypetoken_asm" for c in cookies)

        if not has_skype and spaces_token:
            try:
                async with session.post(
                    "https://authsvc.teams.microsoft.com/v1.0/authz",
                    headers={
                        "Authorization": f"Bearer {spaces_token}",
                        "Content-Type": "application/json",
                    },
                    json={},
                    timeout=15,
                ) as auth_res:
                    if auth_res.status == 200:
                        auth_json = await auth_res.json()
                        skype_token = auth_json.get("tokens", {}).get("skypeToken")
                        if skype_token:
                            now_sec = time.time()
                            for domain in (
                                ".asyncgw.teams.microsoft.com",
                                ".asm.skype.com",
                                "teams.microsoft.com",
                                "teams.cloud.microsoft",
                            ):
                                cookies.append(
                                    {
                                        "name": "skypetoken_asm",
                                        "value": skype_token,
                                        "domain": domain,
                                        "path": "/",
                                        "expires": now_sec + 86400,
                                        "httpOnly": True,
                                        "secure": True,
                                        "sameSite": "None",
                                    }
                                )
                            cookies.append(
                                {
                                    "name": "authtoken",
                                    "value": f"Bearer%3D{quote(spaces_token)}",
                                    "domain": "teams.microsoft.com",
                                    "path": "/",
                                    "expires": now_sec + 86400,
                                    "httpOnly": False,
                                    "secure": True,
                                    "sameSite": "None",
                                }
                            )
            except Exception as err:
                _LOGGER.debug("Could not derive skypeToken via authsvc: %s", err)

        json.dump(data, sys.stdout)
    return 0


if __name__ == "__main__":
    import sys

    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception:
        print("Teams browser authentication failed", file=sys.stderr)
        raise SystemExit(1) from None
