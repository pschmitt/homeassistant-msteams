"""Independent Teams Trouter listener for Home Assistant."""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .api import TeamsAuth

_LOGGER = logging.getLogger(__name__)
_TROUTER = "https://go.trouter.teams.microsoft.com"
_REGISTRAR = "https://teams.microsoft.com/registrar/prod/V2/registrations"
_VERSION = "49/25113001312"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0 Teams/24165.1410.2974.6689/49"
_TC = json.dumps({"cv": "2024.23.01.2", "ua": "TeamsCDL", "hr": "", "v": _VERSION})
_RETRY_SECONDS = 60
_RECONNECT_SECONDS = 45 * 60


def _decode_body(request: dict[str, Any]) -> dict[str, Any]:
    body = request.get("body") or ""
    if request.get("headers", {}).get("X-Microsoft-Skype-Content-Encoding") == "gzip":
        body = gzip.decompress(base64.b64decode(body)).decode()
    data = json.loads(body) if body else {}
    if data.get("cp"):
        data = json.loads(gzip.decompress(base64.b64decode(data["cp"])))
    elif data.get("gp"):
        data = json.loads(base64.b64decode(data["gp"]))
    return data


def _is_relevant(request: dict[str, Any]) -> bool:
    """Use push events as hints; the API remains the source of truth."""
    service = request.get("url", "").split("/")[-1]
    if service in ("NGCallManagerWin", "SkypeSpacesWeb"):
        return True
    if service != "messaging":
        return False
    body = _decode_body(request)
    return body.get("type") == "EventMessage" and body.get("resourceType") in {
        "NewMessage",
        "MessageUpdate",
        "ConversationUpdate",
        "ThreadUpdate",
    }


class TeamsTrouter:
    """Maintain a separate Trouter connection with bounded reconnection."""

    def __init__(
        self,
        auth: TeamsAuth,
        on_change: Callable[[], None],
        create_task: Callable[..., asyncio.Task] = asyncio.create_task,
    ) -> None:
        self.auth = auth
        self.on_change = on_change
        self._create_task = create_task
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = self._create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                if not self.auth.is_authenticated:
                    await self.auth.async_refresh_tokens()
                async with asyncio.timeout(_RECONNECT_SECONDS):
                    await self._connect()
            except asyncio.CancelledError:
                raise
            except Exception as err:
                # Errors may contain signed URLs or credentials. Never log them.
                _LOGGER.debug(
                    "Teams push connection failed (%s); polling continues", type(err).__name__
                )
            await asyncio.sleep(_RETRY_SECONDS)

    async def _connect(self) -> None:
        session = self.auth.session
        skype = self.auth.skype_token
        token = self.auth.spaces_token
        if not skype or not token:
            raise ValueError("Missing Teams credentials")
        epid = str(uuid.uuid4())
        async with session.post(
            f"{_TROUTER}/v4/a",
            params={"epid": epid},
            headers={"X-Skypetoken": skype},
            data=b"",
            timeout=20,
        ) as response:
            response.raise_for_status()
            info = await response.json()
        socketio = info.get("socketio") or f"{_TROUTER}/"
        query = {
            "v": "v4",
            **info.get("connectparams", {}),
            "tc": _TC,
            "con_num": f"{int(time.time() * 1000)}_1",
            "epid": epid,
            "auth": "true",
            "timeout": "40",
        }
        if info.get("ccid"):
            query["ccid"] = info["ccid"]
        async with session.get(
            f"{socketio}socket.io/1/", params=query, headers={"X-Skypetoken": skype}, timeout=20
        ) as response:
            response.raise_for_status()
            sid = (await response.text()).split(":")[0]
        ws_url = f"{socketio.replace('https://', 'wss://').replace('http://', 'ws://')}socket.io/1/websocket/{sid}?{urlencode(query)}"
        async with session.ws_connect(
            ws_url, headers={"X-Skypetoken": skype, "User-Agent": _USER_AGENT}, heartbeat=30
        ) as ws:
            ping_task: asyncio.Task | None = None
            try:
                async for message in ws:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        continue
                    frame = message.data
                    if frame.startswith("1"):
                        await ws.send_str(
                            "5:::"
                            + json.dumps(
                                {
                                    "name": "user.authenticate",
                                    "args": [
                                        {
                                            "headers": {
                                                "X-Ms-Test-User": "False",
                                                "Authorization": f"Bearer {token}",
                                                "X-MS-Migration": "True",
                                            },
                                            "connectparams": info["connectparams"],
                                        }
                                    ],
                                }
                            )
                        )
                        await self._register(info, epid, skype, token)
                        _LOGGER.info("Teams push listener connected")
                        self.on_change()  # Reconcile changes missed while disconnected.
                        ping_task = asyncio.create_task(self._ping(ws))
                    elif frame.startswith("3"):
                        request = json.loads(frame.split(":", 3)[3])
                        if "status" in request:
                            continue
                        await ws.send_str(
                            "3:::"
                            + json.dumps({"id": request.get("id"), "status": 200, "body": ""})
                        )
                        try:
                            if _is_relevant(request):
                                self.on_change()
                        except Exception:
                            _LOGGER.debug("Could not decode Teams push event")
                    elif frame.startswith("5"):
                        obj = json.loads(frame.split(":", 3)[3] or "{}")
                        if obj.get("name") == "trouter.message_loss":
                            await self._register(info, epid, skype, token)
                            self.on_change()
            finally:
                if ping_task:
                    ping_task.cancel()
                    await asyncio.gather(ping_task, return_exceptions=True)

    async def _register(self, info: dict[str, Any], epid: str, skype: str, token: str) -> None:
        for app, template, suffix in (
            ("NextGenCalling", "DesktopNgc_2.3:SkypeNgc", "NGCallManagerWin"),
            ("SkypeSpacesWeb", "SkypeSpacesWeb_2.3", "SkypeSpacesWeb"),
            ("TeamsCDLWebWorker", "TeamsCDLWebWorker_2.1", ""),
        ):
            payload = {
                "clientDescription": {
                    "appId": app,
                    "aesKey": "",
                    "languageId": "en-US",
                    "platform": "edge",
                    "templateKey": template,
                    "platformUIVersion": _VERSION,
                },
                "registrationId": epid if app == "TeamsCDLWebWorker" else str(uuid.uuid4()),
                "nodeId": "",
                "transports": {
                    "TROUTER": [{"context": "", "path": info["surl"] + suffix, "ttl": 86400}]
                },
            }
            async with self.auth.session.post(
                _REGISTRAR,
                json=payload,
                headers={"X-Skypetoken": skype, "Authorization": f"Bearer {token}"},
                timeout=20,
            ) as response:
                response.raise_for_status()

    async def _ping(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        sequence = 1
        while True:
            await asyncio.sleep(30)
            await ws.send_str(f'5:{sequence}+::{{"name":"ping"}}')
            sequence += 1
