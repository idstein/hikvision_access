"""Tests for the Hikvision Access config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


@pytest.mark.asyncio
async def test_user_flow_happy_path(hass: HomeAssistant) -> None:
    # Patch BOTH import sites: the config flow imports HikAccessClient from
    # .api into its own namespace, and the integration's async_setup_entry
    # — invoked automatically on create_entry — does the same. Without the
    # second patch a real aiohttp connector + cleanup thread would land in
    # the HA plugin's verify_cleanup assertion.
    with patch(
        "custom_components.hikvision_access.config_flow.HikAccessClient"
    ) as cf_cls, patch(
        "custom_components.hikvision_access.HikAccessClient"
    ) as setup_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "abc", "model": "DS-K2702WX-E1(P)"}
        )
        client.probe_readers = AsyncMock(return_value=[])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        client.stop = AsyncMock()
        cf_cls.return_value = client
        setup_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == "form"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.0.1",
                CONF_PORT: 443,
                CONF_SSL: True,
                CONF_VERIFY_SSL: False,
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "pw",
            },
        )
        assert result2["type"] == "create_entry"
        assert result2["data"][CONF_HOST] == "192.168.0.1"
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    from custom_components.hikvision_access.api import HikAccessAuthError

    with patch("custom_components.hikvision_access.config_flow.HikAccessClient") as client_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(side_effect=HikAccessAuthError("nope"))
        client.stop = AsyncMock()
        client_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
                CONF_USERNAME: "admin", CONF_PASSWORD: "wrong",
            },
        )
        assert result2["type"] == "form"
        assert result2["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    with patch("custom_components.hikvision_access.config_flow.HikAccessClient") as client_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(side_effect=OSError("connection refused"))
        client.stop = AsyncMock()
        client_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False,
                CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
            },
        )
        assert result2["type"] == "form"
        assert result2["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_options_flow_updates_backfill_days(hass: HomeAssistant) -> None:
    """The options flow should let the user change backfill_days post-setup."""
    from unittest.mock import MagicMock

    from custom_components.hikvision_access.const import (
        CONF_BACKFILL_DAYS,
        DEFAULT_BACKFILL_DAYS,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="serial-opts",
        data={
            CONF_HOST: "h",
            CONF_PORT: 443,
            CONF_SSL: True,
            CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "pw",
            CONF_BACKFILL_DAYS: DEFAULT_BACKFILL_DAYS,
        },
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as setup_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "serial-opts", "model": "M"}
        )
        client.probe_readers = AsyncMock(return_value=[])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})

        async def empty_events():
            if False:
                yield  # pragma: no cover

        async def empty_backfill(start_time, end_time, already_seen=()):
            if False:
                yield  # pragma: no cover

        client.events = MagicMock(return_value=empty_events())
        client.backfill = MagicMock(side_effect=empty_backfill)
        client.stop = AsyncMock()
        setup_cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Now drive the options flow.
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_BACKFILL_DAYS: 7}
        )
        assert result2["type"] == "create_entry"
        await hass.async_block_till_done()

        assert entry.options[CONF_BACKFILL_DAYS] == 7


@pytest.mark.asyncio
async def test_user_flow_aborts_when_already_configured(hass: HomeAssistant) -> None:
    """Adding a device with a serial already known must abort the flow."""
    existing = MockConfigEntry(
        domain=DOMAIN, unique_id="abc", data={CONF_HOST: "old"},
    )
    existing.add_to_hass(hass)

    with patch("custom_components.hikvision_access.config_flow.HikAccessClient") as client_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(
            return_value={"serial_number": "abc", "model": "DS-K2702WX-E1(P)"}
        )
        client.stop = AsyncMock()
        client_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.0.1", CONF_PORT: 443, CONF_SSL: True,
                CONF_VERIFY_SSL: False, CONF_USERNAME: "admin", CONF_PASSWORD: "pw",
            },
        )
    assert result2["type"] == "abort"
    assert result2["reason"] == "already_configured"
