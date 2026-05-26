"""Tests for binary sensor entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


@pytest.mark.asyncio
async def test_door_and_reader_entities_created(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-bs",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-bs", "model": "DS-K2702WX-E1(P)"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
            ReaderInfo(slot=2, enabled=False, name="", description=""),
        ])
        # cardReaderOnlineStatus has 1 entry: online; door arrays cover 1 door.
        client.get_acs_work_status = AsyncMock(return_value={
            "AcsWorkStatus": {
                "doorLockStatus": [0],
                "magneticStatus": [0],
                "doorStatus": [4],
                "cardReaderOnlineStatus": [1],
                "hostAntiDismantleStatus": "close",
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

    # One door (1 enabled reader → 1 door); door entities use door number.
    door_open = hass.states.get("binary_sensor.hikvision_door_1_open")
    door_lock = hass.states.get("binary_sensor.hikvision_door_1_lock")
    reader_online = hass.states.get("binary_sensor.hikvision_eingang_online")
    tamper = hass.states.get("binary_sensor.hikvision_controller_tamper")

    assert door_open is not None
    assert door_lock is not None
    assert reader_online is not None
    assert tamper is not None

    # magneticStatus=[0] → door not open → "off"
    assert door_open.state == "off"
    # doorLockStatus=[0] → unlocked = is_on (BinarySensorDeviceClass.LOCK semantics)
    assert door_lock.state == "on"
    # cardReaderOnlineStatus=[1] → online
    assert reader_online.state == "on"
    # hostAntiDismantleStatus="close" → not tampered → off
    assert tamper.state == "off"


@pytest.mark.asyncio
async def test_door_open_state_reflects_magnetic_status(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-bs2",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-bs2", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
        ])
        # magneticStatus=[1] → door OPEN
        client.get_acs_work_status = AsyncMock(return_value={
            "AcsWorkStatus": {
                "doorLockStatus": [1], "magneticStatus": [1], "doorStatus": [4],
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

    door_open = hass.states.get("binary_sensor.hikvision_door_1_open")
    door_lock = hass.states.get("binary_sensor.hikvision_door_1_lock")
    assert door_open.state == "on"   # magneticStatus[0]=1 → open
    assert door_lock.state == "off"  # doorLockStatus[0]=1 → locked → not is_on
