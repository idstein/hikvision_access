"""AccessControllerEvent decoding and normalization."""

from __future__ import annotations

from typing import Any

MAJOR_LABELS: dict[int, str] = {
    0: "alarm",
    1: "exception",
    2: "operation",
    3: "event",
    5: "ungrouped",
}

# Subset of the documented minorEvent codes for v1. Expanded over time.
# Source: AcsEvent/capabilities response + ISAPI PDF section 16.
MINOR_EVENT_LABELS: dict[int, str] = {
    1: "card_swiped_valid",
    6: "card_swiped_invalid_period",
    7: "card_swiped_invalid_password",
    8: "card_swiped_expired",
    9: "card_unregistered",
    10: "card_blocked",
    11: "card_swiped_at_invalid_door",
    12: "card_authentication_failed",
    21: "fingerprint_invalid",
    22: "fingerprint_valid",
    75: "door_opened_normally",
    76: "door_closed_normally",
    81: "door_held_open",
    82: "door_forced_open",
}

DENIAL_MINOR_CODES: frozenset[int] = frozenset({6, 7, 8, 9, 10, 11, 12, 21})
DOOR_FORCED_CODES: frozenset[int] = frozenset({82})
DOOR_HELD_OPEN_CODES: frozenset[int] = frozenset({81})


def normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw alertStream chunk to a normalized payload.

    Returns ``None`` for chunks that aren't AccessControllerEvents, or whose
    ``majorEventType`` / ``subEventType`` aren't coercible to int — one bad
    chunk must not tear down the stream consumer.
    """
    if raw.get("eventType") != "AccessControllerEvent":
        return None

    ace = raw.get("AccessControllerEvent", {})
    try:
        major = int(ace.get("majorEventType", 0))
        minor = int(ace.get("subEventType", 0))
    except (TypeError, ValueError):
        return None

    return {
        "device_id": None,  # filled in by client (needs deviceInfo serial)
        "controller": ace.get("deviceName", ""),
        "reader_no": ace.get("cardReaderNo"),
        "reader_name": None,  # filled by client via discovery cache
        "door_no": None,  # filled by client via reader→door map
        "card_no": ace.get("cardNo", ""),
        "employee_no": ace.get("employeeNoString", ""),
        "name": ace.get("name", ""),
        "major": MAJOR_LABELS.get(major, f"unknown_{major}"),
        "minor": minor,
        "minor_label": MINOR_EVENT_LABELS.get(minor, f"unknown_{minor}"),
        "serial_no": ace.get("serialNo"),
        "timestamp": raw.get("dateTime"),
        "backfilled": False,
    }
