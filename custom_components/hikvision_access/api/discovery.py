"""ISAPI discovery — parsers and small static helpers (no I/O).

Convention for raw integer status arrays returned by ``/AcsWorkStatus``:

* ``doorLockStatus[k]`` — 0 = unlocked, non-zero = locked.
* ``magneticStatus[k]`` — 0 = closed (magnet engaged), non-zero = open.
* ``doorStatus[k]`` — raw vendor code; ``-1`` is the sentinel used here for
  values that the device omitted or returned as something we couldn't coerce
  to int.
* ``cardReaderOnlineStatus[i]`` — 0 = offline, non-zero = online.

All array-returning parsers PAD their outputs to the requested
``door_count`` / ``reader_count`` so downstream consumers can index by slot
without bounds checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DOOR_STATE_UNKNOWN = -1


@dataclass(frozen=True)
class ReaderInfo:
    slot: int
    enabled: bool
    name: str
    description: str


@dataclass(frozen=True)
class WorkStatus:
    door_lock: list[bool]      # length == door_count; True = locked
    door_open: list[bool]      # length == door_count; True = magnetic open
    door_state: list[int]      # length == door_count; DOOR_STATE_UNKNOWN if absent
    reader_online: list[bool]  # length == reader_count
    tamper: bool
    power_ok: bool


def parse_card_reader_cfg(slot: int, raw: dict[str, Any]) -> ReaderInfo | None:
    """Parse a ``GET /ISAPI/AccessControl/CardReaderCfg/{slot}`` response.

    Returns ``None`` when:

    * the device replies with ``{"subStatusCode": "notSupport", ...}`` — this is the
      sentinel that the slot doesn't exist; callers (e.g. probe loops) should stop.
    * the ``CardReaderCfg`` key is missing or not a dict — likewise treat as terminator.
    """
    if raw.get("subStatusCode") == "notSupport":
        return None
    cfg = raw.get("CardReaderCfg")
    if not isinstance(cfg, dict):
        return None
    return ReaderInfo(
        slot=slot,
        enabled=bool(cfg.get("enable", False)),
        name=str(cfg.get("cardReaderName") or ""),
        description=str(cfg.get("cardReaderDescription") or ""),
    )


def reader_to_door(reader_slot: int) -> int:
    """Hikvision convention: readers ``(2k-1, 2k)`` belong to door ``k``.

    Some installs wire readers differently. The options-flow will let users
    override this map — when that lands, route through a strategy instead.
    """
    # TODO(options-flow): make this configurable per controller.
    return (reader_slot + 1) // 2


def _coerce_int(x: Any) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return DOOR_STATE_UNKNOWN


def _pad(values: list[Any], length: int, fill: Any) -> list[Any]:
    if len(values) >= length:
        return values[:length]
    return values + [fill] * (length - len(values))


def parse_acs_work_status(raw: dict[str, Any], door_count: int, reader_count: int) -> WorkStatus:
    s = raw.get("AcsWorkStatus", {})
    door_lock_raw = _pad(list(s.get("doorLockStatus") or []), door_count, 0)
    door_mag_raw = _pad(list(s.get("magneticStatus") or []), door_count, 0)
    door_state_raw = _pad(
        list(s.get("doorStatus") or []), door_count, DOOR_STATE_UNKNOWN
    )
    reader_online_raw = _pad(
        list(s.get("cardReaderOnlineStatus") or []), reader_count, 0
    )

    return WorkStatus(
        door_lock=[bool(_coerce_int(x)) for x in door_lock_raw],
        door_open=[bool(_coerce_int(x)) for x in door_mag_raw],
        door_state=[_coerce_int(x) for x in door_state_raw],
        reader_online=[bool(_coerce_int(x)) for x in reader_online_raw],
        tamper=str(s.get("hostAntiDismantleStatus", "close")) != "close",
        power_ok=str(s.get("powerSupplyStatus", "")) == "ACPowerSupply",
    )
