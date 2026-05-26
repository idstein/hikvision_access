"""Tests for sensor entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN, SIGNAL_EVENT


@pytest.mark.asyncio
async def test_last_event_sensor_updates_on_dispatch(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-sensor",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-sensor", "model": "DS-K2702WX-E1(P)"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
            ReaderInfo(slot=2, enabled=False, name="", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={
            "AcsWorkStatus": {
                "doorLockStatus": [0], "magneticStatus": [0], "doorStatus": [4],
                "cardReaderOnlineStatus": [1], "hostAntiDismantleStatus": "close",
                "powerSupplyStatus": "ACPowerSupply",
            }
        })

        async def empty():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_last_event")
    assert state is not None
    # Initial value is "unknown" because no event has fired yet.
    assert state.state in ("unknown", "")

    async_dispatcher_send(
        hass,
        SIGNAL_EVENT,
        {
            "controller": "C", "reader_no": 1, "reader_name": "Eingang", "door_no": 1,
            "card_no": "X", "name": "Alice", "minor_label": "card_swiped_valid",
            "serial_no": 1, "timestamp": "2026-05-26T10:00:00Z", "backfilled": False,
            "major": "event", "minor": 1, "employee_no": "",
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hikvision_eingang_last_event")
    assert state.state == "Alice"


@pytest.mark.asyncio
async def test_no_sensor_created_for_disabled_reader(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-sensor2",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-sensor2", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=2, enabled=False, name="", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # No reader was enabled → no last_event sensor created.
    assert hass.states.get("sensor.hikvision__last_event") is None
    # Reader 2's slug-from-empty-name pattern shouldn't accidentally create entities either.
    matching = [s for s in hass.states.async_all() if "last_event" in s.entity_id]
    assert matching == []


@pytest.mark.asyncio
async def test_last_event_sensor_ignores_other_readers(hass: HomeAssistant) -> None:
    """An event for reader_no=2 must not update the slot-1 sensor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-sensor3",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-sensor3", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    async_dispatcher_send(
        hass,
        SIGNAL_EVENT,
        {
            "controller": "C", "reader_no": 2, "reader_name": "Exit", "door_no": 1,
            "card_no": "Z", "name": "Bob", "minor_label": "card_swiped_valid",
            "serial_no": 99, "timestamp": "t", "backfilled": False,
            "major": "event", "minor": 1, "employee_no": "",
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_last_event")
    assert state.state in ("unknown", "")
    assert state.attributes.get("card_no") != "Z"


@pytest.mark.asyncio
async def test_total_swipes_increments_on_each_card_event(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-swipes",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-swipes", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Dispatch 3 card events; counter should land at 3.
    for serial in (1, 2, 3):
        async_dispatcher_send(
            hass, SIGNAL_EVENT,
            {
                "controller": "C", "reader_no": 1, "reader_name": "Eingang",
                "door_no": 1, "card_no": f"card{serial}", "name": "x",
                "minor_label": "card_swiped_valid", "serial_no": serial,
                "timestamp": "t", "backfilled": False, "major": "event",
                "minor": 1, "employee_no": "",
            },
        )
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_total_swipes")
    assert state is not None
    assert int(float(state.state)) == 3

    # An event without a card_no (denial, etc) must NOT count.
    async_dispatcher_send(
        hass, SIGNAL_EVENT,
        {
            "controller": "C", "reader_no": 1, "reader_name": "Eingang",
            "door_no": 1, "card_no": "", "name": "", "minor_label": "card_authentication_failed",
            "serial_no": 4, "timestamp": "t", "backfilled": False,
            "major": "event", "minor": 12, "employee_no": "",
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hikvision_eingang_total_swipes")
    assert int(float(state.state)) == 3


@pytest.mark.asyncio
async def test_unique_visitors_dedups_and_persists_cardholders(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-unique",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-unique", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # A, A, B → unique count = 2; cardholders sorted alphabetically.
    for card_no, name in (("A", "Alice"), ("A", "Alice"), ("B", "Bob")):
        async_dispatcher_send(
            hass, SIGNAL_EVENT,
            {
                "controller": "C", "reader_no": 1, "reader_name": "Eingang",
                "door_no": 1, "card_no": card_no, "name": name,
                "minor_label": "card_swiped_valid", "serial_no": 1,
                "timestamp": "t", "backfilled": False, "major": "event",
                "minor": 1, "employee_no": "",
            },
        )
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_unique_visitors_today")
    assert state is not None
    assert int(state.state) == 2
    assert state.attributes.get("cardholders") == ["Alice", "Bob"]


@pytest.mark.asyncio
async def test_total_swipes_persists_last_serial_no(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-lastserial",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-lastserial", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty())
        client.stop = AsyncMock()
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Dispatch out-of-order serial numbers — last_serial_no should track the max.
    for serial in (5, 3, 9, 7):
        async_dispatcher_send(
            hass, SIGNAL_EVENT,
            {
                "controller": "C", "reader_no": 1, "reader_name": "Eingang",
                "door_no": 1, "card_no": f"card{serial}", "name": "x",
                "minor_label": "card_swiped_valid", "serial_no": serial,
                "timestamp": "t", "backfilled": False, "major": "event",
                "minor": 1, "employee_no": "",
            },
        )
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_total_swipes")
    assert state.attributes.get("last_serial_no") == 9
