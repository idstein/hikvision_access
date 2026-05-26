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
async def test_digest_challenge_cached_across_requests(aiohttp_server) -> None:
    """After the first 401, subsequent requests must include Authorization
    on the first try — proves the challenge cache is shared per client."""
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
        # Return a usable CardReaderCfg so the probe loop keeps going.
        slot = request.match_info["slot"]
        return web.json_response(
            {"CardReaderCfg": {"enable": True, "cardReaderName": f"R{slot}"}}
        )

    app = web.Application()
    app.router.add_get("/ISAPI/AccessControl/CardReaderCfg/{slot}", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="admin", password="secret", ssl=False, verify_ssl=False
    )
    await client.probe_readers(max_slots=3)
    # First request: no Authorization → 401 + retry with auth.
    # Subsequent requests: Authorization on first try (cached challenge).
    assert request_log[0] is None
    assert request_log[1] is not None
    assert request_log[1].startswith("Digest")
    # After the first round-trip, slot=2 sends the Authorization header
    # on the FIRST try (no preliminary 401-and-retry).
    assert request_log[2] is not None
    assert request_log[2].startswith("Digest")
    await client.stop()
