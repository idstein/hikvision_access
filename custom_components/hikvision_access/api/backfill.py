"""AcsEvent backfill — replay historical events the controller buffered while HA was down."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from .events import MAJOR_LABELS, MINOR_EVENT_LABELS


def replay_page(page: dict[str, Any], already_seen: Iterable[int] = ()) -> Iterator[dict[str, Any]]:
    """Yield normalized events from an AcsEvent search page, skipping seen serials."""
    seen = set(already_seen)
    info_list = page.get("AcsEvent", {}).get("InfoList", [])
    for item in info_list:
        serial = item.get("serialNo")
        if serial in seen:
            continue
        major = int(item.get("major", 0))
        minor = int(item.get("minor", 0))
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
