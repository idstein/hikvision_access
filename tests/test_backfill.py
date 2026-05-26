"""Tests for AcsEvent backfill replay."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.hikvision_access.api.backfill import replay_page


def test_replay_yields_normalized_with_backfilled_flag(fixtures_dir: Path) -> None:
    page = json.loads((fixtures_dir / "acs_event_page.json").read_text())
    events = list(replay_page(page))
    assert len(events) == 2
    assert events[0]["serial_no"] == 100
    assert events[0]["name"] == "Alice"
    assert events[0]["backfilled"] is True


def test_replay_dedups_by_serial(fixtures_dir: Path) -> None:
    page = json.loads((fixtures_dir / "acs_event_page.json").read_text())
    events = list(replay_page(page, already_seen={100}))
    assert [e["serial_no"] for e in events] == [101]
