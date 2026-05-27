"""AcsEvent backfill — replay historical events the controller buffered while HA was down."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import TYPE_CHECKING, Any

import aiohttp

from .events import MAJOR_LABELS, MINOR_EVENT_LABELS

if TYPE_CHECKING:
    from . import HikAccessClient

_LOGGER = logging.getLogger(__name__)

# Hard cap so a stuck cursor (firmware bug, lying ``totalMatches``) can't
# spin the loop forever. 1000 pages x 30 events/page = up to 30k events
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
        # Coerce cardReaderNo so it always matches the int slot used by entities.
        raw_reader = item.get("cardReaderNo")
        try:
            reader_no = int(raw_reader) if raw_reader not in (None, "") else None
        except (TypeError, ValueError):
            reader_no = None
        yield {
            "device_id": None,
            "controller": "",
            "reader_no": reader_no,
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
    client: HikAccessClient,
    base: str,
    start_time: str,
    end_time: str,
    page_size: int = 30,
) -> AsyncIterator[dict[str, Any]]:
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
                # major=0 covers alarm events. Without an explicit major filter
                # some Hikvision firmwares 400; sending it as an int is the
                # documented contract from the device's own AcsEvent capabilities
                # response (``@opt: "0,1,2,3,5"``). We chase events with this
                # major plus the per-major minor=0 wildcard for "any sub-type".
                "major": 0,
                "minor": 0,
                "startTime": start_time,
                "endTime": end_time,
            }
        }
        async with client.request_ctx(
            "POST",
            f"{base}/ISAPI/AccessControl/AcsEvent?format=json",
            json=body,
        ) as r:
            if r.status >= 400:
                # Pull a snippet of the error body for the log. Hikvision often
                # slams the connection shut on a 4xx, so reading the body can
                # itself raise — don't let that mask the status code.
                try:
                    body_preview = (await r.text())[:500]
                except (aiohttp.ClientError, TimeoutError):
                    body_preview = "<connection closed before body could be read>"
                _LOGGER.warning(
                    "AcsEvent search returned %d; body=%s; request=%r",
                    r.status, body_preview, body,
                )
                r.raise_for_status()  # surface so the caller can choose to abort
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
