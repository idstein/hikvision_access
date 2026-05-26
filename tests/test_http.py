"""Tests for ISAPI request helpers (excluding alertStream)."""

from __future__ import annotations

import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessClient


@pytest.mark.asyncio
async def test_get_device_info(aiohttp_server) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(
            text="""<?xml version="1.0"?>
<DeviceInfo><deviceName>X</deviceName><model>DS-K2702WX-E1(P)</model>
<serialNumber>abc123</serialNumber><macAddress>00:11:22:33:44:55</macAddress>
<firmwareVersion>V1.7.4</firmwareVersion></DeviceInfo>""",
            content_type="application/xml",
        )

    app = web.Application()
    app.router.add_get("/ISAPI/System/deviceInfo", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    info = await client.get_device_info()
    assert info["model"] == "DS-K2702WX-E1(P)"
    assert info["serial_number"] == "abc123"
    await client.stop()


@pytest.mark.asyncio
async def test_probe_readers_stops_at_notsupport(aiohttp_server) -> None:
    async def handler(request: web.Request) -> web.Response:
        slot = request.match_info["slot"]
        if slot in {"1", "2"}:
            return web.json_response({"CardReaderCfg": {"enable": slot == "1", "cardReaderName": f"R{slot}"}})
        return web.json_response({"statusCode": 4, "subStatusCode": "notSupport"})

    app = web.Application()
    app.router.add_get("/ISAPI/AccessControl/CardReaderCfg/{slot}", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    readers = await client.probe_readers(max_slots=8)
    assert [r.slot for r in readers] == [1, 2]
    await client.stop()
