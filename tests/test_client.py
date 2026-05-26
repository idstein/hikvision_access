"""Integration tests for HikAccessClient against an aiohttp test server."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessClient


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
        host=f"127.0.0.1",
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
