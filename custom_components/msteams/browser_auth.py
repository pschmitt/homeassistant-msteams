"""Shared browser-daemon Teams session acquisition."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

_LOGGER = logging.getLogger(__name__)
DEFAULT_TEAMS_BASE_URL = "https://teams.microsoft.com"


class BrowserAuthError(Exception):
    """Browser session cannot provide Teams authentication."""


def _is_teams_host(url: str | None) -> bool:
    if not url:
        return False
    return urlparse(url).netloc.lower() in (
        "teams.microsoft.com",
        "teams.cloud.microsoft",
        "login.microsoftonline.com",
    )


async def acquire_browser_auth(
    bd_client: Any, profile: str, validate: Callable[[dict[str, Any]], dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Acquire auth state via a browser-daemon client and release owned resources."""
    # 1. Fetch overview
    try:
        overview = await bd_client.async_get_overview()
    except Exception as err:
        raise BrowserAuthError(f"Failed to connect to browser-daemon: {err}") from err

    profiles = overview.get("profiles", [])
    prof = next((p for p in profiles if p.get("name") == profile), None)
    if not prof:
        raise BrowserAuthError(f"browser-daemon has no profile '{profile}'")

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
                idle_timeout_minutes=5,
            )
            created_session_id = session_info["id"]
            session_id = created_session_id
        except Exception as err:
            raise BrowserAuthError(
                f"Failed to start browser-daemon session for profile '{profile}': {err}"
            ) from err
    else:
        if hasattr(bd_client, "async_set_idle_timeout"):
            try:
                await bd_client.async_set_idle_timeout(session_id, minutes=5, node=node_name)
            except Exception as err:
                _LOGGER.debug(
                    "Could not set idle timeout on attached session %s: %s",
                    session_id,
                    err,
                )

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
                        parsed = validate(auth_data)
                        if parsed.get("refresh_token"):
                            creds = parsed
                            break
                    except Exception:
                        pass
            except Exception as err:
                last_err = err
                _LOGGER.debug("Waiting for Teams auth export: %s", err)
            await asyncio.sleep(2)

        if not creds:
            raise BrowserAuthError(
                f"Timed out waiting for Teams authentication from browser-daemon: {last_err or 'RefreshToken not found in page storage'}"
            )

    except BrowserAuthError:
        raise
    except Exception as err:
        raise BrowserAuthError(f"Failed to extract Teams auth from browser-daemon: {err}") from err
    finally:
        if opened_new_tab and tab_id:
            try:
                await bd_client.async_close_tab(tab_id, node=node_name)
            except Exception as err:
                _LOGGER.debug("Failed to close temporary tab %s: %s", tab_id, err)

        target_sid = created_session_id or session_id
        if target_sid:
            in_use = False
            try:
                s_info = await bd_client.async_get_session(target_sid, node=node_name)
                if (
                    s_info.get("client_count", 0) > 0
                    or s_info.get("clients")
                    or s_info.get("viewer_count", 0) > 0
                ):
                    in_use = True
                tabs = await bd_client.async_get_tabs(node=node_name)
                if any(
                    (t.get("sessionId") or t.get("session_id")) == target_sid and t.get("owner")
                    for t in tabs
                ):
                    in_use = True
            except Exception as err:
                _LOGGER.debug("Could not check usage for browser session %s: %s", target_sid, err)
                in_use = not bool(created_session_id)

            if not in_use:
                try:
                    await bd_client.async_delete_session(target_sid, node=node_name)
                except Exception as err:
                    _LOGGER.debug("Failed to delete unused browser session %s: %s", target_sid, err)
            elif hasattr(bd_client, "async_set_idle_timeout"):
                try:
                    await bd_client.async_set_idle_timeout(target_sid, minutes=5, node=node_name)
                except Exception as err:
                    _LOGGER.debug(
                        "Failed to set idle timeout on in-use session %s: %s",
                        target_sid,
                        err,
                    )

    return auth_data, creds


class BrowserDaemonClient:
    """Minimal async browser-daemon adapter usable outside Home Assistant."""

    def __init__(self, session: Any, base_url: str, secret: str | None = None) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.local_node: str | None = None

    async def _request(
        self, method: str, path: str, *, node: str | None = None, **kwargs: Any
    ) -> Any:
        from urllib.parse import quote

        if node and node != self.local_node:
            path = f"/api/peers/{quote(node, safe='')}/{path.lstrip('/')}"
        headers = {"X-Browser-Cluster-Secret": self.secret} if self.secret else {}
        async with self.session.request(
            method, self.base_url + path, headers=headers, timeout=30, **kwargs
        ) as response:
            if response.status not in (200, 201, 204):
                raise BrowserAuthError(f"browser-daemon returned HTTP {response.status}")
            if response.status == 204:
                return None
            return await response.json()

    async def async_get_overview(self) -> dict[str, Any]:
        data = await self._request("GET", "/api/mesh/overview")
        self.local_node = (data.get("local_node") or {}).get("name")
        return data

    async def async_create_session(
        self, *, node: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/sessions", node=node, json={"persistent": True, **kwargs}
        )

    async def async_set_idle_timeout(
        self, session_id: str, minutes: int, node: str | None = None
    ) -> Any:
        from urllib.parse import quote

        return await self._request(
            "POST",
            f"/api/sessions/{quote(session_id, safe='')}/idle-timeout",
            node=node,
            params={"minutes": minutes},
        )

    async def async_delete_session(self, session_id: str, node: str | None = None) -> Any:
        from urllib.parse import quote

        return await self._request(
            "DELETE", f"/api/sessions/{quote(session_id, safe='')}", node=node
        )

    async def async_get_session(self, session_id: str, node: str | None = None) -> dict[str, Any]:
        from urllib.parse import quote

        return await self._request("GET", f"/api/sessions/{quote(session_id, safe='')}", node=node)

    async def async_get_tabs(self, node: str | None = None) -> list[dict[str, Any]]:
        return await self._request("GET", "/json/list", node=node)

    async def async_open_tab(
        self, url: str, *, session_id: str, node: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PUT", "/json/new", node=node, params={"url": url, "session_id": session_id}
        )

    async def async_close_tab(self, tab_id: str, node: str | None = None) -> Any:
        from urllib.parse import quote

        return await self._request("PUT", f"/json/close/{quote(tab_id, safe='')}", node=node)

    async def async_export_auth(
        self,
        page_id: str,
        *,
        session_id: str,
        urls: list[str],
        storage: bool,
        node: str | None = None,
    ) -> dict[str, Any]:
        from urllib.parse import quote

        return await self._request(
            "POST",
            f"/api/pages/{quote(page_id, safe='')}/auth",
            node=node,
            json={"session_id": session_id, "urls": urls, "storage": storage},
        )


def _validate_refresh_token(data: dict[str, Any]) -> dict[str, Any]:
    """Wait for both the refresh credential and an unexpired Substrate JWT."""
    import base64
    import json

    refresh_token = None
    valid_substrate = False
    for value in (data.get("local_storage") or {}).values():
        try:
            entry = json.loads(value)
            if (
                entry.get("credentialType") == "RefreshToken"
                and entry.get("secret")
                and entry.get("clientId")
            ):
                refresh_token = entry["secret"]
            if "SubstrateSearch" in entry.get("target", ""):
                token = entry.get("secret", "")
                if not token.startswith("ey"):
                    continue
                payload = token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
                if isinstance(claims.get("exp"), (int, float)) and claims["exp"] > time.time():
                    valid_substrate = True
        except (ValueError, AttributeError, TypeError, IndexError):
            continue
    if not refresh_token or not valid_substrate:
        raise BrowserAuthError("Teams session is not ready")
    return {"refresh_token": refresh_token}


async def _main() -> int:
    """CLI writes session material only to the caller's stdout pipe."""
    import json
    import os
    import sys

    import aiohttp

    url = os.environ.get("TEAMS_MCP_BROWSER_DAEMON_URL")
    if not url:
        return 2
    secret_path = os.environ.get(
        "BROWSER_DAEMON_SECRET_FILE", "/run/secrets/browser-daemon/cluster-secret"
    )
    try:
        with open(secret_path, encoding="utf-8") as secret_file:
            secret = secret_file.read().strip()
    except OSError:
        secret = None
    async with aiohttp.ClientSession() as session:
        if not secret:
            async with session.get(url.rstrip("/") + "/", timeout=10):
                pass
        client = BrowserDaemonClient(session, url, secret)
        data, _ = await acquire_browser_auth(
            client,
            os.environ.get("TEAMS_MCP_BROWSER_DAEMON_PROFILE", "work"),
            _validate_refresh_token,
        )
        json.dump(data, sys.stdout)
    return 0


if __name__ == "__main__":
    import sys

    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception:
        print("Teams browser authentication failed", file=sys.stderr)
        raise SystemExit(1) from None
