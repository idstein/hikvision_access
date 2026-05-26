"""ISAPI discovery — parsers and small static helpers (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReaderInfo:
    slot: int
    enabled: bool
    name: str
    description: str


@dataclass(frozen=True)
class WorkStatus:
    door_lock: list[bool]      # length == door_count
    door_open: list[bool]      # magneticStatus
    door_state: list[int]      # raw doorStatus codes
    reader_online: list[bool]  # length == reader_count
    tamper: bool
    power_ok: bool


def parse_card_reader_cfg(slot: int, raw: dict[str, Any]) -> ReaderInfo | None:
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
    """Hikvision convention: readers (2k-1, 2k) belong to door k."""
    return (reader_slot + 1) // 2


def parse_acs_work_status(raw: dict[str, Any], door_count: int, reader_count: int) -> WorkStatus:
    s = raw.get("AcsWorkStatus", {})
    door_lock_raw = (s.get("doorLockStatus") or [])[:door_count]
    door_mag_raw = (s.get("magneticStatus") or [])[:door_count]
    door_state_raw = (s.get("doorStatus") or [])[:door_count]
    reader_online_raw = (s.get("cardReaderOnlineStatus") or [])[:reader_count]

    return WorkStatus(
        door_lock=[bool(x) for x in door_lock_raw],
        door_open=[bool(x) for x in door_mag_raw],
        door_state=[int(x) for x in door_state_raw],
        reader_online=[bool(x) for x in reader_online_raw],
        tamper=str(s.get("hostAntiDismantleStatus", "close")) != "close",
        power_ok=str(s.get("powerSupplyStatus", "")) == "ACPowerSupply",
    )
