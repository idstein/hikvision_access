"""ISAPI helpers that aren't long-lived streams."""

from __future__ import annotations

import logging
from typing import Any
from xml.etree.ElementTree import fromstring

import aiohttp

from .discovery import ReaderInfo, parse_card_reader_cfg

_LOGGER = logging.getLogger(__name__)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _camel_to_snake(name: str) -> str:
    """Convert ``lowerCamelCase`` to ``snake_case``.

    Assumes Hikvision-style ``lowerCamelCase`` keys (no leading acronym).
    Acronyms inside the string are NOT split (``XMLConfig`` → ``xmlconfig``),
    but the DeviceInfo schema doesn't contain such keys today.
    """
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


async def fetch_device_info(
    session: aiohttp.ClientSession, base: str, auth: aiohttp.BasicAuth | None
) -> dict[str, str]:
    async with session.get(f"{base}/ISAPI/System/deviceInfo", auth=auth) as r:
        r.raise_for_status()
        body = await r.text()
    # Device is LAN-local and authenticated; defusedxml is overkill here.
    # Revisit if exposure changes (e.g. unauthenticated probe across the WAN).
    root = fromstring(body)
    out: dict[str, str] = {}
    for child in root:
        out[_camel_to_snake(_strip_ns(child.tag))] = (child.text or "").strip()
    return out


async def probe_card_readers(
    session: aiohttp.ClientSession,
    base: str,
    auth: aiohttp.BasicAuth | None,
    max_slots: int,
) -> list[ReaderInfo]:
    readers: list[ReaderInfo] = []
    for slot in range(1, max_slots + 1):
        url = f"{base}/ISAPI/AccessControl/CardReaderCfg/{slot}?format=json"
        try:
            async with session.get(url, auth=auth) as r:
                # content_type=None: some Hikvision endpoints return wrong
                # Content-Type headers; we still want the JSON body.
                data = await r.json(content_type=None)
        except (aiohttp.ClientError, ValueError) as err:
            # TODO(phase-3): the discovery shouldn't die on a transient 5xx
            # or an HTML error page. Log and stop, treating it as terminator.
            _LOGGER.debug("probe_card_readers stopped at slot %d: %s", slot, err)
            break
        if isinstance(data, dict) and data.get("subStatusCode") == "notSupport":
            _LOGGER.debug("probe_card_readers reached notSupport at slot %d", slot)
            break
        info = parse_card_reader_cfg(slot, data)
        if info is None:
            _LOGGER.debug("probe_card_readers got unparseable response at slot %d", slot)
            break
        readers.append(info)
    return readers


async def fetch_acs_work_status(
    session: aiohttp.ClientSession, base: str, auth: aiohttp.BasicAuth | None
) -> dict[str, Any]:
    async with session.get(
        f"{base}/ISAPI/AccessControl/AcsWorkStatus?format=json", auth=auth
    ) as r:
        r.raise_for_status()
        # content_type=None: see probe_card_readers note above.
        return await r.json(content_type=None)
