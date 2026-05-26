"""AcsEvent backfill — replay historical events the controller buffered while HA was down."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from typing import Any

import aiohttp

from .events import MAJOR_LABELS, MINOR_EVENT_LABELS

_LOGGER = logging.getLogger(__name__)


def replay_page(page: dict[str, Any], already_seen: Iterable[int] = ()) -> Iterator[dict[str, Any]]:
    """Yield normalized events from an AcsEvent search page, skipping seen serials.

    Items with a missing ``serialNo`` or with non-coercible ``major`` / ``minor``
    fields are dropped (with a debug log) — one malformed item must not abort
    replay of the rest of the page.
    """
    seen = set(already_seen)
    info_list = page.get("AcsEvent", {}).get("InfoList", [])
    for item in info_list:
        serial = item.get("serialNo")
        if serial is None:
            _LOGGER.debug("replay_page: dropping item with no serialNo: %r", item)
            continue
        if serial in seen:
            continue
        try:
            major = int(item.get("major", 0))
            minor = int(item.get("minor", 0))
        except (TypeError, ValueError):
            _LOGGER.debug(
                "replay_page: dropping item with non-coercible major/minor: serial=%r", serial
            )
            continue
        yield {
            "device_id": None,
            "controller": "",
            "reader_no": item.get("cardReaderNo"),
            "reader_name": None,
            "door_no": None,
            "card_no": item.get("cardNo", ""),
            "employee_no": item.get("employeeNoString", ""),
            "name": item.get("name", ""),
            "major": MAJOR_LABELS.get(major, f"unknown_{major}"),
            "minor": minor,
            "minor_label": MINOR_EVENT_LABELS.get(minor, f"unknown_{minor}"),
            "serial_no": serial,
            "timestamp": item.get("time"),
            "backfilled": True,
        }


async def fetch_pages(
    session: aiohttp.ClientSession,
    base: str,
    auth: aiohttp.BasicAuth | None,
    start_time: str,
    end_time: str,
    page_size: int = 30,
):
    """Yield raw AcsEvent pages until the device returns an empty InfoList."""
    position = 0
    search_id = "ha-backfill"
    while True:
        body = {
            "AcsEventCond": {
                "searchID": search_id,
                "searchResultPosition": position,
                "maxResults": page_size,
                "startTime": start_time,
                "endTime": end_time,
            }
        }
        async with session.post(
            f"{base}/ISAPI/AccessControl/AcsEvent?format=json",
            json=body,
            auth=auth,
        ) as r:
            r.raise_for_status()
            page = await r.json(content_type=None)
        info_list = page.get("AcsEvent", {}).get("InfoList", [])
        if not info_list:
            return
        yield page
        position += len(info_list)
