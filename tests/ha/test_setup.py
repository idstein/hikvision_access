"""Tests for async_setup_entry / async_unload_entry."""

from __future__ import annotations

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
