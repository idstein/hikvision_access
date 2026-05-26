"""The Hikvision Access Control integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .api import HikAccessAuthError, HikAccessClient
from .api.discovery import ReaderInfo
from .const import CONF_VERIFY_SSL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Platforms are added in later tasks; keep empty for now so async_setup_entry
# can be exercised without sensor.py / binary_sensor.py existing yet.
PLATFORMS: list[Platform] = []


@dataclass
class HikAccessData:
    """Runtime data for one configured Hikvision controller."""

    client: HikAccessClient
    device_info: dict[str, str]
    readers: list[ReaderInfo] = field(default_factory=list)
    stream_task: asyncio.Task | None = None
    door_count: int = 1


type HikAccessConfigEntry = ConfigEntry[HikAccessData]


async def async_setup_entry(hass: HomeAssistant, entry: HikAccessConfigEntry) -> bool:
    """Set up Hikvision Access from a config entry."""
    d = entry.data
    client = HikAccessClient(
        host=d[CONF_HOST],
        port=d[CONF_PORT],
        username=d[CONF_USERNAME],
        password=d[CONF_PASSWORD],
        ssl=d[CONF_SSL],
        verify_ssl=d[CONF_VERIFY_SSL],
    )
    try:
        info = await client.get_device_info()
        readers = await client.probe_readers()
    except HikAccessAuthError as err:
        await client.stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except Exception as err:  # noqa: BLE001
        await client.stop()
        raise ConfigEntryNotReady(f"Cannot connect to {d[CONF_HOST]}: {err}") from err

    entry.runtime_data = HikAccessData(client=client, device_info=info, readers=readers)

    # Register the controller device so future entities use_via_device / device_info.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, info.get("serial_number", entry.entry_id))},
        manufacturer="Hikvision",
        model=info.get("model", "Access Controller"),
        name=info.get("device_name", "Hikvision Access Controller"),
        sw_version=info.get("firmware_version"),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikAccessConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data: HikAccessData = entry.runtime_data
    if data.stream_task:
        data.stream_task.cancel()
    await data.client.stop()
    return True
