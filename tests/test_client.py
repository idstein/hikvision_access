"""Integration tests for HikAccessClient against an aiohttp test server."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessAuthError, HikAccessClient


async def _alertstream_handler(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": 'multipart/mixed; boundary="MIME_boundary"'},
    )
    await resp.prepare(request)
    body = (
        b"--MIME_boundary\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 130\r\n\r\n"
        b'{"eventType":"AccessControllerEvent","dateTime":"2026-05-26T10:00:00Z",'
        b'"AccessControllerEvent":{"majorEventType":3,"subEventType":1,"serialNo":1}}'
        b"\r\n--MIME_boundary\r\n"
    )
    await resp.write(body)
    await asyncio.sleep(0.05)
    return resp


@pytest.mark.asyncio
async def test_client_streams_one_event(aiohttp_server) -> None:
    app = web.Application()
    app.router.add_get("/ISAPI/Event/notification/alertStream", _alertstream_handler)
    server = await aiohttp_server(app)

    received: list[dict[str, Any]] = []

    client = HikAccessClient(
        host="127.0.0.1",
        port=server.port,
        username="u",
        password="p",
        ssl=False,
        verify_ssl=False,
    )

    async def collect():
        async for evt in client.events():
            received.append(evt)
            if len(received) == 1:
                await client.stop()

    await asyncio.wait_for(collect(), timeout=2)
    assert received[0]["card_no"] == ""
    assert received[0]["serial_no"] == 1


async def _unauthorized_handler(request: web.Request) -> web.Response:
    return web.Response(status=401, text="Unauthorized")


@pytest.mark.asyncio
async def test_alertstream_401_raises_hik_access_auth_error(aiohttp_server) -> None:
    app = web.Application()
    app.router.add_get("/ISAPI/Event/notification/alertStream", _unauthorized_handler)
    server = await aiohttp_server(app)

    client = HikAccessClient(
        host="127.0.0.1",
        port=server.port,
        username="u",
        password="p",
        ssl=False,
        verify_ssl=False,
    )
    try:
        with pytest.raises(HikAccessAuthError):
            async for _ in client.events():
                pass
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_reconnects_after_server_drops(aiohttp_server) -> None:
    drops = 0

    async def flaky_handler(request: web.Request) -> web.StreamResponse:
        nonlocal drops
        resp = web.StreamResponse(
            status=200, headers={"Content-Type": 'multipart/mixed; boundary="MIME_boundary"'}
        )
        await resp.prepare(request)
        if drops == 0:
            drops += 1
            return resp  # drop immediately first time
        body = (
            b"--MIME_boundary\r\nContent-Type: application/json\r\nContent-Length: 110\r\n\r\n"
            b'{"eventType":"AccessControllerEvent","AccessControllerEvent":{"majorEventType":3,"subEventType":1,"serialNo":2}}'
            b"\r\n--MIME_boundary\r\n"
        )
        await resp.write(body)
        await asyncio.sleep(0.05)
        return resp

    app = web.Application()
    app.router.add_get("/ISAPI/Event/notification/alertStream", flaky_handler)
    server = await aiohttp_server(app)

    received = []
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    client._initial_backoff = 0.01  # speed test up

    async def collect():
        async for evt in client.events():
            received.append(evt)
            await client.stop()
            break

    await asyncio.wait_for(collect(), timeout=3)
    assert received[0]["serial_no"] == 2


@pytest.mark.asyncio
async def test_two_consecutive_401s_escalates_auth_error(aiohttp_server) -> None:
    app = web.Application()
    app.router.add_get("/ISAPI/Event/notification/alertStream", _unauthorized_handler)
    server = await aiohttp_server(app)

    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    client._initial_backoff = 0.01

    try:
        with pytest.raises(HikAccessAuthError):
            async for _ in client.events():
                pass
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_auth_error_resets_when_connection_succeeds(aiohttp_server) -> None:
    """A 401 followed by a successful connect (even without events yielded)
    followed by another 401 must escalate. Reproduces the reset-on-yield bug."""
    step = {"n": 0}

    async def sequence_handler(request: web.Request) -> web.StreamResponse:
        n = step["n"]
        step["n"] += 1
        if n == 0:
            return web.Response(status=401, text="Unauthorized")
        if n == 1:
            # Connection succeeds but no AccessControllerEvent gets yielded.
            resp = web.StreamResponse(
                status=200,
                headers={"Content-Type": 'multipart/mixed; boundary="MIME_boundary"'},
            )
            await resp.prepare(request)
            heartbeat = (
                b"--MIME_boundary\r\nContent-Type: application/json\r\nContent-Length: 27\r\n\r\n"
                b'{"eventType":"videoloss"}'
                b"\r\n--MIME_boundary\r\n"
            )
            await resp.write(heartbeat)
            await asyncio.sleep(0.05)
            return resp
        # subsequent attempts -> 401 again
        return web.Response(status=401, text="Unauthorized")

    app = web.Application()
    app.router.add_get("/ISAPI/Event/notification/alertStream", sequence_handler)
    server = await aiohttp_server(app)

    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    client._initial_backoff = 0.01

    try:
        with pytest.raises(HikAccessAuthError):
            async for _ in client.events():
                pass
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_stop_closes_session_when_events_never_started() -> None:
    """Construct a client, never call events(), call stop().

    The session must be closed afterwards — otherwise consumers that build
    a client just to call get_device_info() (e.g. config_flow / setup_entry)
    would leak an aiohttp.ClientSession on every entry.
    """
    client = HikAccessClient(
        host="127.0.0.1", port=1, username="u", password="p", ssl=False, verify_ssl=False
    )
    await client.stop()
    assert client._session.closed


@pytest.mark.asyncio
async def test_alertstream_with_digest_preflight(aiohttp_server) -> None:
    """The client must preflight /ISAPI/System/deviceInfo to harvest the
    Digest challenge, then open the alertStream with Authorization set on
    the first request — otherwise streaming responses 401-and-retry mid-body."""
    import hashlib
    import re

    def _md5(s: str) -> str:
        return hashlib.md5(s.encode()).hexdigest()

    async def device_info_handler(request: web.Request) -> web.Response:
        # Always 401 — we just want the preflight to harvest the challenge.
        return web.Response(
            status=401,
            headers={
                "WWW-Authenticate": (
                    'Digest realm="Hik", qop="auth", nonce="xyz789", algorithm=MD5'
                )
            },
        )

    async def stream_handler(request: web.Request) -> web.StreamResponse:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("digest"):
            return web.Response(
                status=401,
                headers={
                    "WWW-Authenticate": (
                        'Digest realm="Hik", qop="auth", nonce="xyz789", algorithm=MD5'
                    )
                },
            )
        fields = dict(re.findall(r'(\w+)=("[^"]*"|[^,\s]+)', auth[len("Digest "):]))
        for k, v in list(fields.items()):
            fields[k] = v.strip('"')
        ha1 = _md5("admin:Hik:secret")
        ha2 = _md5(f"GET:{fields['uri']}")
        expected = _md5(
            f"{ha1}:xyz789:{fields['nc']}:{fields['cnonce']}:auth:{ha2}"
        )
        if fields.get("response") != expected:
            return web.Response(status=401, text="bad digest")
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": 'multipart/mixed; boundary="MIME_boundary"'},
        )
        await resp.prepare(request)
        body = (
            b"--MIME_boundary\r\nContent-Type: application/json\r\nContent-Length: 130\r\n\r\n"
            b'{"eventType":"AccessControllerEvent","dateTime":"2026-05-26T10:00:00Z",'
            b'"AccessControllerEvent":{"majorEventType":3,"subEventType":1,"serialNo":9}}'
            b"\r\n--MIME_boundary\r\n"
        )
        await resp.write(body)
        await asyncio.sleep(0.05)
        return resp

    app = web.Application()
    app.router.add_get("/ISAPI/System/deviceInfo", device_info_handler)
    app.router.add_get("/ISAPI/Event/notification/alertStream", stream_handler)
    server = await aiohttp_server(app)

    received: list[dict[str, Any]] = []
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="admin", password="secret", ssl=False, verify_ssl=False
    )

    async def collect():
        async for evt in client.events():
            received.append(evt)
            if len(received) == 1:
                await client.stop()

    await asyncio.wait_for(collect(), timeout=2)
    assert received[0]["serial_no"] == 9
