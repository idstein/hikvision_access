"""AcsEvent backfill — replay historical events the controller buffered while HA was down."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

import aiohttp

from .events import MAJOR_LABELS, MINOR_EVENT_LABELS

_LOGGER = logging.getLogger(__name__)

# Hard cap so a stuck cursor (firmware bug, lying ``totalMatches``) can't
# spin the loop forever. 1000 pages × 30 events/page = up to 30k events
# replayed on startup, which is well past any practical HA downtime.
MAX_PAGES = 1000


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
    """Yield raw AcsEvent pages until the device returns an empty InfoList.

    Notes:
    * ``page_size`` defaults to 30 because that's the max the device reports
      in ``/ISAPI/AccessControl/AcsEvent/capabilities`` (``maxResults @max=30``).
    * ``searchID`` is a fresh UUID per invocation — Hikvision treats it as a
      server-side session token, so a shared literal would let two concurrent
      backfills against the same controller interleave cursors.
    * Iteration stops at ``MAX_PAGES`` to guard against a stuck cursor or
      lying ``totalMatches`` field.
    """
    position = 0
    search_id = uuid.uuid4().hex
    for _ in range(MAX_PAGES):
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
    _LOGGER.warning(
        "AcsEvent backfill hit MAX_PAGES=%d cap (searchID=%s); stopping to avoid an infinite loop",
        MAX_PAGES,
        search_id,
    )
