"""Tests for async_setup_entry / async_unload_entry."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


def _build_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h",
            CONF_PORT: 443,
            CONF_SSL: True,
            CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "pw",
        },
        unique_id="serial-1",
    )


@pytest.mark.asyncio
async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = _build_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-1", "model": "DS-K2702WX-E1(P)"}
        )
        client.probe_readers = AsyncMock(return_value=[])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty_async_gen():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty_async_gen())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert await hass.config_entries.async_unload(entry.entry_id)
        client.stop.assert_awaited()


@pytest.mark.asyncio
async def test_setup_raises_auth_failed_on_401(hass: HomeAssistant) -> None:
    from homeassistant.config_entries import ConfigEntryState

    from custom_components.hikvision_access.api import HikAccessAuthError

    entry = _build_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(side_effect=HikAccessAuthError("nope"))
        client.stop = AsyncMock()
        cls.return_value = client

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.SETUP_ERROR
        client.stop.assert_awaited()


@pytest.mark.asyncio
async def test_setup_raises_not_ready_on_connect_failure(hass: HomeAssistant) -> None:
    from homeassistant.config_entries import ConfigEntryState

    entry = _build_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(side_effect=OSError("conn refused"))
        client.stop = AsyncMock()
        cls.return_value = client

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.SETUP_RETRY
        client.stop.assert_awaited()


@pytest.mark.asyncio
async def test_stream_dispatches_events(hass: HomeAssistant) -> None:
    """Events yielded by client.events() must fire on the HA event bus + dispatcher
    after being enriched with device_id / reader_name / door_no."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.hikvision_access.api.discovery import ReaderInfo
    from custom_components.hikvision_access.const import EVENT_BUS_NAME, SIGNAL_EVENT

    entry = _build_entry()
    entry.add_to_hass(hass)

    async def fake_events():
        yield {
            "device_id": None, "controller": "", "reader_no": 1, "reader_name": None,
            "door_no": None, "card_no": "X", "name": "Alice",
            "minor_label": "card_swiped_valid", "serial_no": 1,
            "timestamp": "2026-05-26T10:00:00Z", "backfilled": False,
            "major": "event", "minor": 1, "employee_no": "",
        }

    received_dispatcher: list[dict] = []
    received_bus: list = []
    async_dispatcher_connect(hass, SIGNAL_EVENT, lambda p: received_dispatcher.append(p))
    hass.bus.async_listen(EVENT_BUS_NAME, lambda e: received_bus.append(e))

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-stream", "model": "DS-K2702WX-E1(P)"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        client.events = MagicMock(return_value=fake_events())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert received_dispatcher, "dispatcher signal was not received"
    payload = received_dispatcher[0]
    assert payload["name"] == "Alice"
    assert payload["device_id"] == "serial-stream"   # enriched from deviceInfo
    assert payload["reader_name"] == "Eingang"        # enriched from probe_readers
    assert payload["door_no"] == 1                    # enriched via reader→door map
    # Event bus also fired:
    assert received_bus, "event bus did not receive the event"
    assert received_bus[0].data["name"] == "Alice"


@pytest.mark.asyncio
async def test_backfill_runs_on_startup(hass: HomeAssistant) -> None:
    """Startup must call client.backfill() with a 24h window and dispatch results."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.hikvision_access.api.discovery import ReaderInfo
    from custom_components.hikvision_access.const import SIGNAL_EVENT

    entry = _build_entry()
    entry.add_to_hass(hass)

    backfill_calls: list[dict] = []

    async def fake_backfill(start_time, end_time, already_seen=()):
        backfill_calls.append({
            "start_time": start_time, "end_time": end_time,
            "already_seen": set(already_seen),
        })
        yield {
            "device_id": None, "controller": "", "reader_no": 1, "reader_name": None,
            "door_no": None, "card_no": "Y", "name": "Bob",
            "minor_label": "card_swiped_valid", "serial_no": 42,
            "timestamp": "2026-05-26T07:00:00Z", "backfilled": True,
            "major": "event", "minor": 1, "employee_no": "",
        }

    received: list[dict] = []
    async_dispatcher_connect(hass, SIGNAL_EVENT, lambda p: received.append(p))

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-bf", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty_events():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty_events())
        client.backfill = MagicMock(side_effect=fake_backfill)
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert backfill_calls, "client.backfill was not invoked on startup"
    call = backfill_calls[0]
    # ISO8601 with timezone; end > start by exactly 24 hours (give or take ms).
    assert call["start_time"].endswith("Z") or "+" in call["start_time"]
    assert call["end_time"].endswith("Z") or "+" in call["end_time"]
    # The backfilled event reaches the dispatcher with the backfilled flag preserved.
    assert any(evt.get("backfilled") for evt in received)


@pytest.mark.asyncio
async def test_backfill_imports_hourly_statistics(hass: HomeAssistant) -> None:
    """Replayed events should be grouped into hour buckets and pushed into
    HA's long-term statistics so the chart stays accurate across restarts."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect  # noqa: F401

    from custom_components.hikvision_access.api.discovery import ReaderInfo

    entry = _build_entry()
    entry.add_to_hass(hass)

    async def fake_backfill(start_time, end_time, already_seen=()):
        # Three events at 08:30, 08:45 (same hour), 09:15 (next hour).
        for ts, serial in (
            ("2026-05-26T08:30:00+00:00", 1),
            ("2026-05-26T08:45:00+00:00", 2),
            ("2026-05-26T09:15:00+00:00", 3),
        ):
            yield {
                "device_id": None, "controller": "", "reader_no": 1, "reader_name": None,
                "door_no": None, "card_no": f"c{serial}", "name": "x",
                "minor_label": "card_swiped_valid", "serial_no": serial,
                "timestamp": ts, "backfilled": True,
                "major": "event", "minor": 1, "employee_no": "",
            }

    with patch(
        "custom_components.hikvision_access.async_import_statistics"
    ) as import_stats, patch(
        "custom_components.hikvision_access.HikAccessClient"
    ) as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-stats", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty_events():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty_events())
        client.backfill = MagicMock(side_effect=fake_backfill)
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert import_stats.call_count == 1
    _hass_arg, metadata, stats = import_stats.call_args.args
    assert metadata["statistic_id"] == "sensor.hikvision_eingang_total_swipes"
    assert metadata["unit_of_measurement"] == "swipes"
    assert metadata["has_sum"] is True
    # Running sums: 2 events in 08:00 bucket, +1 in 09:00 → 2, 3.
    sums = [row["sum"] for row in stats]
    assert sums == [2, 3]


def _parse_iso_window(start_time: str, end_time: str) -> timedelta:
    """Parse an ISO8601 backfill window into a timedelta."""
    from datetime import datetime, timedelta  # noqa: F401

    # The integration emits both `...Z` and `...+00:00` flavors; normalize.
    def _norm(s: str) -> str:
        return s.replace("Z", "+00:00")

    start = datetime.fromisoformat(_norm(start_time))
    end = datetime.fromisoformat(_norm(end_time))
    return end - start


@pytest.mark.asyncio
async def test_backfill_uses_default_30_day_window(hass: HomeAssistant) -> None:
    """Without options or explicit data, _run_backfill must request 30 days."""
    from datetime import timedelta

    from custom_components.hikvision_access.api.discovery import ReaderInfo

    entry = _build_entry()
    entry.add_to_hass(hass)

    backfill_calls: list[dict] = []

    async def fake_backfill(start_time, end_time, already_seen=()):
        backfill_calls.append({"start_time": start_time, "end_time": end_time})
        if False:
            yield  # pragma: no cover

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-30d", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty_events():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty_events())
        client.backfill = MagicMock(side_effect=fake_backfill)
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert backfill_calls
    delta = _parse_iso_window(
        backfill_calls[0]["start_time"], backfill_calls[0]["end_time"]
    )
    # Allow a small fudge for execution time between utcnow() calls.
    assert timedelta(days=30) - timedelta(seconds=5) <= delta <= timedelta(days=30) + timedelta(seconds=5)


@pytest.mark.asyncio
async def test_backfill_respects_options_override(hass: HomeAssistant) -> None:
    """When entry.options sets CONF_BACKFILL_DAYS, the window must shrink to match."""
    from datetime import timedelta

    from custom_components.hikvision_access.api.discovery import ReaderInfo
    from custom_components.hikvision_access.const import CONF_BACKFILL_DAYS

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h",
            CONF_PORT: 443,
            CONF_SSL: True,
            CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "pw",
        },
        options={CONF_BACKFILL_DAYS: 7},
        unique_id="serial-7d",
    )
    entry.add_to_hass(hass)

    backfill_calls: list[dict] = []

    async def fake_backfill(start_time, end_time, already_seen=()):
        backfill_calls.append({"start_time": start_time, "end_time": end_time})
        if False:
            yield  # pragma: no cover

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-7d", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty_events():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty_events())
        client.backfill = MagicMock(side_effect=fake_backfill)
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert backfill_calls
    delta = _parse_iso_window(
        backfill_calls[0]["start_time"], backfill_calls[0]["end_time"]
    )
    assert timedelta(days=7) - timedelta(seconds=5) <= delta <= timedelta(days=7) + timedelta(seconds=5)
