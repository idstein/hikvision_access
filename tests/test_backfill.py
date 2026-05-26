"""Tests for AcsEvent backfill replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessClient
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


def test_replay_skips_items_with_uncoercible_major_or_minor() -> None:
    page = {
        "AcsEvent": {
            "InfoList": [
                {"serialNo": 1, "major": "x", "minor": 1, "time": "t"},  # bad major
                {"serialNo": 2, "major": 3, "minor": None, "time": "t"},  # bad minor
                {"serialNo": 3, "major": 3, "minor": 1, "time": "t"},     # good
            ]
        }
    }
    events = list(replay_page(page))
    assert [e["serial_no"] for e in events] == [3]


def test_replay_skips_items_with_missing_serial() -> None:
    page = {
        "AcsEvent": {
            "InfoList": [
                {"major": 3, "minor": 1, "time": "t"},                  # no serialNo
                {"serialNo": 7, "major": 3, "minor": 1, "time": "t"},  # good
            ]
        }
    }
    events = list(replay_page(page))
    assert [e["serial_no"] for e in events] == [7]


def test_replay_handles_empty_or_missing_acs_event_key() -> None:
    assert list(replay_page({})) == []
    assert list(replay_page({"AcsEvent": {}})) == []
    assert list(replay_page({"AcsEvent": {"InfoList": []}})) == []


@pytest.mark.asyncio
async def test_backfill_paginates_until_empty(aiohttp_server) -> None:
    pages = [
        {"AcsEvent": {"numOfMatches": 1, "totalMatches": 2, "InfoList": [{"serialNo": 1, "major": 3, "minor": 1, "time": "t"}]}},
        {"AcsEvent": {"numOfMatches": 1, "totalMatches": 2, "InfoList": [{"serialNo": 2, "major": 3, "minor": 1, "time": "t"}]}},
        {"AcsEvent": {"numOfMatches": 0, "totalMatches": 2, "InfoList": []}},
    ]
    counter = {"i": 0}

    async def handler(request: web.Request) -> web.Response:
        page = pages[counter["i"]]
        counter["i"] += 1
        return web.json_response(page)

    app = web.Application()
    app.router.add_post("/ISAPI/AccessControl/AcsEvent", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    events = []
    async for e in client.backfill(start_time="2026-01-01T00:00:00Z", end_time="2026-12-31T00:00:00Z"):
        events.append(e)
    assert [e["serial_no"] for e in events] == [1, 2]
    await client.stop()
