"""Diagnostics endpoint for the Hikvision Access Control integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HikAccessConfigEntry

REDACTED = "**REDACTED**"
ENTRY_REDACT_KEYS = {"password"}
DEVICE_INFO_REDACT_KEYS = {"mac_address", "serial_number"}


def _redact(d: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {k: (REDACTED if k in keys else v) for k, v in d.items()}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HikAccessConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    coord = data.coordinator
    return {
        "entry": _redact(dict(entry.data), ENTRY_REDACT_KEYS),
        "device_info": _redact(dict(data.device_info), DEVICE_INFO_REDACT_KEYS),
        "readers": [
            {"slot": r.slot, "enabled": r.enabled, "name": r.name} for r in data.readers
        ],
        "door_count": data.door_count,
        "coordinator_last": (
            {
                "door_lock": coord.data.door_lock,
                "door_open": coord.data.door_open,
                "door_state": coord.data.door_state,
                "reader_online": coord.data.reader_online,
                "tamper": coord.data.tamper,
                "power_ok": coord.data.power_ok,
            }
            if coord is not None and coord.data is not None
            else None
        ),
    }
