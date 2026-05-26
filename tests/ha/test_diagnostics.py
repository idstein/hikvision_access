"""Tests for diagnostics endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


@pytest.mark.asyncio
async def test_diagnostics_redacts_secrets(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.0.1", CONF_PORT: 443, CONF_SSL: True,
            CONF_VERIFY_SSL: False, CONF_USERNAME: "admin",
            CONF_PASSWORD: "supersecret",
        },
        unique_id="serial-diag",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={
            "serial_number": "ABC123", "model": "DS-K2702WX-E1(P)",
            "mac_address": "00:11:22:33:44:55", "firmware_version": "V1.7.4",
            "device_name": "Access Controller",
        })
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description="DS-K1105"),
        ])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty():
            if False:
                yield  # pragma: no cover
        client.events = MagicMock(return_value=empty())
        client.backfill = MagicMock(return_value=empty())
        client.stop = AsyncMock()
        cls.return_value = client
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    from custom_components.hikvision_access.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # Entry data: password redacted, host preserved.
    assert diag["entry"]["host"] == "192.168.0.1"
    assert "password" not in diag["entry"] or diag["entry"]["password"] == "**REDACTED**"

    # Device info: mac_address and serial_number redacted; model preserved.
    assert diag["device_info"]["model"] == "DS-K2702WX-E1(P)"
    assert diag["device_info"].get("serial_number", "**REDACTED**") == "**REDACTED**"
    assert diag["device_info"].get("mac_address", "**REDACTED**") == "**REDACTED**"

    # Readers list preserved.
    assert diag["readers"] == [{"slot": 1, "enabled": True, "name": "Eingang"}]
