"""Tests for safe Trouter event handling and lifecycle."""

import asyncio
import base64
import gzip
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.msteams.trouter import TeamsTrouter, _is_relevant


def test_event_filter_handles_read_message_and_compressed_events():
    def request(kind):
        body = json.dumps({"type": "EventMessage", "resourceType": kind})
        compressed = base64.b64encode(gzip.compress(body.encode())).decode()
        return {"url": "/messaging", "body": json.dumps({"cp": compressed})}

    assert _is_relevant(request("ConversationUpdate"))
    assert _is_relevant(request("ThreadUpdate"))
    assert not _is_relevant(request("Control/Typing"))
    assert _is_relevant({"url": "/NGCallManagerWin"})
    assert not _is_relevant({"url": "/unknown"})


@pytest.mark.asyncio
async def test_listener_start_and_stop_are_idempotent():
    auth = MagicMock()
    auth.is_authenticated = True
    listener = TeamsTrouter(auth, MagicMock())
    started = asyncio.Event()

    async def connect():
        started.set()
        await asyncio.Event().wait()

    listener._connect = AsyncMock(side_effect=connect)
    listener.start()
    listener.start()
    await started.wait()
    assert listener._connect.await_count == 1
    await listener.stop()
    await listener.stop()
    assert listener._task is None


@pytest.mark.asyncio
async def test_connect_registers_and_refreshes_on_events(monkeypatch):
    from aiohttp import ClientSession, web

    from custom_components.msteams import trouter

    registrations = []
    acknowledgements = []
    changes = []
    endpoint_ids = []
    base_url = ""

    async def allocate(request):
        endpoint_ids.append(request.query["epid"])
        return web.json_response(
            {"socketio": base_url + "/", "surl": base_url + "/push/", "connectparams": {}}
        )

    async def socket_session(request):
        return web.Response(text="test-session:180:180:websocket")

    async def register(request):
        payload = await request.json()
        registrations.append(
            (
                payload["clientDescription"]["appId"],
                payload["registrationId"],
                payload["transports"]["TROUTER"][0]["ttl"],
            )
        )
        return web.Response(status=200)

    async def websocket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("1::")
        await ws.receive()  # user.authenticate
        await ws.send_str(
            "5:::"
            + json.dumps(
                {
                    "name": "trouter.message_loss",
                    "args": [
                        {
                            "droppedIndicators": [
                                {"tag": "TeamsCDLWebWorker", "etag": "1"}
                            ]
                        }
                    ],
                }
            )
        )
        acknowledgements.append((await ws.receive()).data)  # processed_message_loss
        body = json.dumps({"type": "EventMessage", "resourceType": "ConversationUpdate"})
        await ws.send_str("3:::" + json.dumps({"id": 1, "url": "/messaging", "body": body}))
        acknowledgements.append((await ws.receive()).data)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_post("/v4/a", allocate)
    app.router.add_get("/socket.io/1/", socket_session)
    app.router.add_get("/socket.io/1/websocket/{sid}", websocket)
    app.router.add_post("/registrations", register)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(trouter, "_TROUTER", base_url)
    monkeypatch.setattr(trouter, "_REGISTRAR", base_url + "/registrations")
    try:
        async with ClientSession() as session:
            auth = MagicMock(
                session=session,
                skype_token="test",
                spaces_token="test",
                user_mri="8:orgid:00000000-0000-0000-0000-000000000001",
            )
            listener = TeamsTrouter(auth, lambda: changes.append(True))
            await listener._connect()
            await listener._connect()
    finally:
        await runner.cleanup()
    assert [r[0] for r in registrations[:3]] == [
        "NextGenCalling",
        "SkypeSpacesWeb",
        "TeamsCDLWebWorker",
    ]
    assert registrations[2][1] == endpoint_ids[0]
    assert all(r[2] == 3600 for r in registrations)
    # Reconnecting reuses the exact same deterministic registrationIds instead of leaking new ones.
    assert len(registrations) == 6
    assert [r[1] for r in registrations[:3]] == [r[1] for r in registrations[3:]]
    assert len(set(r[1] for r in registrations[:3])) == 3
    assert len(changes) == 6  # (initial + message_loss + conversation update) * 2
    assert json.loads(acknowledgements[0].split(":", 3)[3])["name"] == "trouter.processed_message_loss"
    assert json.loads(acknowledgements[1].split(":", 3)[3])["id"] == 1
