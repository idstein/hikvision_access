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
