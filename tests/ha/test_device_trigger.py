"""Tests for device triggers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN, EVENT_BUS_NAME


async def _setup(hass: HomeAssistant) -> str:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
        },
        unique_id="serial-trig",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-trig", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
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
    # Find the device id we registered.
    registry = dr.async_get(hass)
    for device in registry.devices.values():
        if any(i[0] == DOMAIN for i in device.identifiers):
            return device.id
    raise AssertionError("device was not registered")


@pytest.mark.asyncio
async def test_card_read_trigger_fires(hass: HomeAssistant) -> None:
    device_id = await _setup(hass)

    triggered: list = []

    await async_setup_component(hass, "automation", {
        "automation": [{
            "alias": "fire on card_read",
            "trigger": {
                "platform": "device",
                "domain": DOMAIN,
                "device_id": device_id,
                "type": "card_read",
            },
            "action": {"event": "card_read_observed"},
        }]
    })
    hass.bus.async_listen("card_read_observed", lambda e: triggered.append(e))

    hass.bus.async_fire(
        EVENT_BUS_NAME,
        {"card_no": "X", "name": "Alice", "reader_no": 1, "minor_label": "card_swiped_valid"},
    )
    await hass.async_block_till_done()
    assert triggered, "card_read trigger did not fire"


@pytest.mark.asyncio
async def test_card_denied_trigger_only_fires_on_denial(hass: HomeAssistant) -> None:
    device_id = await _setup(hass)

    denied: list = []

    await async_setup_component(hass, "automation", {
        "automation": [{
            "alias": "fire on card_denied",
            "trigger": {
                "platform": "device",
                "domain": DOMAIN,
                "device_id": device_id,
                "type": "card_denied",
            },
            "action": {"event": "card_denied_observed"},
        }]
    })
    hass.bus.async_listen("card_denied_observed", lambda e: denied.append(e))

    # A regular swipe must NOT fire the denied trigger.
    hass.bus.async_fire(
        EVENT_BUS_NAME,
        {"card_no": "X", "name": "A", "reader_no": 1, "minor_label": "card_swiped_valid"},
    )
    await hass.async_block_till_done()
    assert not denied

    # A denial event MUST fire it.
    hass.bus.async_fire(EVENT_BUS_NAME, {"card_no": "X", "name": "A", "reader_no": 1, "minor_label": "card_blocked"})
    await hass.async_block_till_done()
    assert denied


@pytest.mark.asyncio
async def test_async_get_triggers_returns_all_four_types(hass: HomeAssistant) -> None:
    from custom_components.hikvision_access.device_trigger import (
        TRIGGER_TYPES,
        async_get_triggers,
    )

    device_id = await _setup(hass)
    triggers = await async_get_triggers(hass, device_id)
    assert {t["type"] for t in triggers} == TRIGGER_TYPES
