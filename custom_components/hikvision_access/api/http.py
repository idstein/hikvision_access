"""ISAPI helpers that aren't long-lived streams."""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import fromstring

import aiohttp

from .discovery import ReaderInfo, parse_card_reader_cfg


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


async def fetch_device_info(session: aiohttp.ClientSession, base: str, auth: aiohttp.BasicAuth | None) -> dict[str, str]:
    async with session.get(f"{base}/ISAPI/System/deviceInfo", auth=auth) as r:
        r.raise_for_status()
        body = await r.text()
    root = fromstring(body)
    out: dict[str, str] = {}
    for child in root:
        out[_camel_to_snake(_strip_ns(child.tag))] = (child.text or "").strip()
    return out


def _camel_to_snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


async def probe_card_readers(
    session: aiohttp.ClientSession,
    base: str,
    auth: aiohttp.BasicAuth | None,
    max_slots: int,
) -> list[ReaderInfo]:
    readers: list[ReaderInfo] = []
    for slot in range(1, max_slots + 1):
        url = f"{base}/ISAPI/AccessControl/CardReaderCfg/{slot}?format=json"
        async with session.get(url, auth=auth) as r:
            data = await r.json(content_type=None)
        if isinstance(data, dict) and data.get("subStatusCode") == "notSupport":
            break
        info = parse_card_reader_cfg(slot, data)
        if info is None:
            break
        readers.append(info)
    return readers


async def fetch_acs_work_status(
    session: aiohttp.ClientSession, base: str, auth: aiohttp.BasicAuth | None
) -> dict[str, Any]:
    async with session.get(f"{base}/ISAPI/AccessControl/AcsWorkStatus?format=json", auth=auth) as r:
        r.raise_for_status()
        return await r.json(content_type=None)
