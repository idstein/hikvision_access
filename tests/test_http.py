"""Tests for ISAPI request helpers (excluding alertStream)."""

from __future__ import annotations

import hashlib
import re

import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessClient


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


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


@pytest.mark.asyncio
async def test_get_device_info_digest_auth(aiohttp_server) -> None:
    """Server requires HTTP Digest; client must perform the 401 → retry dance."""
    body = """<?xml version="1.0"?>
<DeviceInfo><deviceName>X</deviceName><model>DS-K2702WX-E1(P)</model>
<serialNumber>abc123</serialNumber></DeviceInfo>"""

    async def handler(request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("digest"):
            return web.Response(
                status=401,
                headers={
                    "WWW-Authenticate": (
                        'Digest realm="Hik", qop="auth", nonce="abc123", '
                        'opaque="op1", algorithm=MD5'
                    )
                },
            )
        fields = dict(re.findall(r'(\w+)=("[^"]*"|[^,\s]+)', auth[len("Digest "):]))
        for k, v in list(fields.items()):
            fields[k] = v.strip('"')
        uri = fields["uri"]
        nc = fields["nc"]
        cnonce = fields["cnonce"]
        ha1 = _md5("admin:Hik:secret")
        ha2 = _md5(f"GET:{uri}")
        expected = _md5(f"{ha1}:abc123:{nc}:{cnonce}:auth:{ha2}")
        if fields.get("response") != expected:
            return web.Response(status=401, text="bad digest")
        return web.Response(text=body, content_type="application/xml")

    app = web.Application()
    app.router.add_get("/ISAPI/System/deviceInfo", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="admin", password="secret", ssl=False, verify_ssl=False
    )
    info = await client.get_device_info()
    assert info["model"] == "DS-K2702WX-E1(P)"
    await client.stop()


@pytest.mark.asyncio
async def test_digest_renegotiates_every_request(aiohttp_server) -> None:
    """Each successful request must re-do the 401-then-retry handshake.

    Hikvision firmware rotates the nonce per request or per TCP connection,
    so caching the challenge between calls reliably 401s. The client's
    ``__aexit__`` therefore invalidates the cache after every non-401
    response. This test confirms the pattern: for N probe calls we expect
    exactly 2N requests on the wire — N unauthenticated 401s followed by N
    authenticated 200s.
    """
    request_log: list[str | None] = []

    async def handler(request: web.Request) -> web.Response:
        request_log.append(request.headers.get("Authorization"))
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("digest"):
            return web.Response(
                status=401,
                headers={
                    "WWW-Authenticate": (
                        'Digest realm="Hik", qop="auth", nonce="abc123", algorithm=MD5'
                    )
                },
            )
        slot = request.match_info["slot"]
        return web.json_response(
            {"CardReaderCfg": {"enable": True, "cardReaderName": f"R{slot}"}}
        )

    app = web.Application()
    app.router.add_get("/ISAPI/AccessControl/CardReaderCfg/{slot}", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="admin",
        password="secret", ssl=False, verify_ssl=False,
    )
    await client.probe_readers(max_slots=3)
    assert len(request_log) == 6  # 3 probe calls × (unauth + auth) pair
    assert request_log[0::2] == [None, None, None]
    for auth_header in request_log[1::2]:
        assert auth_header is not None
        assert auth_header.startswith("Digest")
    await client.stop()
