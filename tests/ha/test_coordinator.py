"""Smoke tests for the AcsWorkStatusCoordinator wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo, WorkStatus
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


def _build_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-coord",
    )


@pytest.mark.asyncio
async def test_coordinator_first_refresh_populates_data(hass: HomeAssistant) -> None:
    entry = _build_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.hikvision_access.HikAccessClient"
    ) as setup_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-coord", "model": "DS-K2702WX-E1(P)"}
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
        setup_cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    data = entry.runtime_data
    assert data.coordinator is not None
    assert isinstance(data.coordinator.data, WorkStatus)
    assert data.coordinator.data.reader_online == [True]
    assert data.door_count == 1  # one enabled reader → one door


@pytest.mark.asyncio
async def test_coordinator_failure_blocks_setup(hass: HomeAssistant) -> None:
    """If the first AcsWorkStatus poll fails, ConfigEntryNotReady should bubble up."""
    from homeassistant.config_entries import ConfigEntryState

    entry = _build_entry()
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as setup_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-coord", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[])
        client.get_acs_work_status = AsyncMock(side_effect=OSError("net broke"))
        client.stop = AsyncMock()
        setup_cls.return_value = client

        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.SETUP_RETRY
