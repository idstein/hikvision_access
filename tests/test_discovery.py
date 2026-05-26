"""Tests for discovery parsers."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.hikvision_access.api.discovery import (
    parse_acs_work_status,
    parse_card_reader_cfg,
    reader_to_door,
)


def test_parse_enabled_reader(fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "card_reader_1.json").read_text())
    info = parse_card_reader_cfg(slot=1, raw=raw)
    assert info is not None
    assert info.slot == 1
    assert info.enabled is True
    assert info.name == "Eingang"


def test_parse_disabled_reader(fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "card_reader_disabled.json").read_text())
    info = parse_card_reader_cfg(slot=2, raw=raw)
    assert info is not None
    assert info.enabled is False


def test_reader_to_door_default_convention() -> None:
    assert reader_to_door(1) == 1
    assert reader_to_door(2) == 1
    assert reader_to_door(3) == 2
    assert reader_to_door(4) == 2


def test_parse_acs_work_status(fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "acs_work_status.json").read_text())
    status = parse_acs_work_status(raw, door_count=2, reader_count=1)
    assert status.door_lock[0] is False  # 0 = unlocked false in our convention
    assert status.door_open == [False, False]
    assert status.reader_online == [True]
    assert status.tamper is False
