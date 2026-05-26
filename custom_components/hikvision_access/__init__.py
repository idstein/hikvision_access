"""The Hikvision Access Control integration."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_import_statistics,
)
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .api import HikAccessAuthError, HikAccessClient
from .api.discovery import ReaderInfo
from .const import (
    CONF_BACKFILL_DAYS,
    CONF_VERIFY_SSL,
    DEFAULT_BACKFILL_DAYS,
    DOMAIN,
    EVENT_BUS_NAME,
    SIGNAL_EVENT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


@dataclass
class HikAccessData:
    """Runtime data for one configured Hikvision controller."""

    client: HikAccessClient
    device_info: dict[str, str]
    readers: list[ReaderInfo] = field(default_factory=list)
    stream_task: asyncio.Task | None = None
    coordinator: "AcsWorkStatusCoordinator | None" = None
    # Sentinel — overwritten by AcsWorkStatusCoordinator setup (Task 3.3) once
    # we know how many doors this controller exposes.
    door_count: int = 1


type HikAccessConfigEntry = ConfigEntry[HikAccessData]


async def _run_stream(hass: HomeAssistant, entry: HikAccessConfigEntry) -> None:
    """Consume HikAccessClient.events(), enrich, and fan out to bus + dispatcher."""
    data = entry.runtime_data
    info = data.device_info
    reader_index = {r.slot: r for r in data.readers}
    try:
        async for evt in data.client.events():
            evt["device_id"] = info.get("serial_number") or entry.unique_id or entry.entry_id
            evt["controller"] = evt.get("controller") or info.get("device_name", "")
            r = reader_index.get(evt.get("reader_no"))
            if r is not None:
                evt["reader_name"] = r.name
                evt["door_no"] = (r.slot + 1) // 2
            hass.bus.async_fire(EVENT_BUS_NAME, evt)
            async_dispatcher_send(hass, SIGNAL_EVENT, evt)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Hikvision event stream task crashed; events will resume on next entry reload")


async def _run_backfill(hass: HomeAssistant, entry: HikAccessConfigEntry) -> None:
    """Replay AcsEvents the controller buffered while HA was down.

    Reads ``last_serial_no`` from restored TotalSwipes sensor states to skip
    already-ingested events. Window size is controlled by the
    ``backfill_days`` option (default 30 days). Setting it to 0 disables
    backfill entirely. Logged at warning on failure — does NOT block setup.
    """
    data = entry.runtime_data
    info = data.device_info
    reader_index = {r.slot: r for r in data.readers}

    backfill_days = (
        entry.options.get(CONF_BACKFILL_DAYS)
        or entry.data.get(CONF_BACKFILL_DAYS)
        or DEFAULT_BACKFILL_DAYS
    )
    if backfill_days <= 0:
        _LOGGER.debug("backfill disabled by configuration; skipping")
        return

    # Collect already-seen serials from restored sensor attributes.
    seen: set[int] = set()
    for state in hass.states.async_all("sensor"):
        if not state.entity_id.startswith("sensor.hikvision_"):
            continue
        if "total_swipes" not in state.entity_id:
            continue
        s = state.attributes.get("last_serial_no")
        if isinstance(s, int):
            seen.add(s)

    end = dt_util.utcnow()
    start = end - timedelta(days=int(backfill_days))
    iso = lambda dt: dt.isoformat(timespec="seconds").replace("+00:00", "Z")

    # slot -> list of UTC datetimes for events the device replayed
    replayed_per_slot: dict[int, list[datetime]] = defaultdict(list)

    try:
        async for evt in data.client.backfill(
            start_time=iso(start), end_time=iso(end), already_seen=seen
        ):
            evt["device_id"] = info.get("serial_number") or entry.unique_id or entry.entry_id
            evt["controller"] = evt.get("controller") or info.get("device_name", "")
            r = reader_index.get(evt.get("reader_no"))
            if r is not None:
                evt["reader_name"] = r.name
                evt["door_no"] = (r.slot + 1) // 2
                if evt.get("card_no"):
                    ts = dt_util.parse_datetime(evt.get("timestamp") or "")
                    if ts is not None:
                        replayed_per_slot[r.slot].append(dt_util.as_utc(ts))
            hass.bus.async_fire(EVENT_BUS_NAME, evt)
            async_dispatcher_send(hass, SIGNAL_EVENT, evt)
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "AcsEvent backfill failed; HA will resume on the next live event",
            exc_info=True,
        )

    # Inject exact hour buckets into long-term statistics so the chart stays
    # accurate even when ingest time differs from the original event time.
    if not replayed_per_slot:
        return
    for slot, timestamps in replayed_per_slot.items():
        r = reader_index.get(slot)
        if r is None:
            continue
        from .sensor import _slug

        slug = _slug(r.name, slot)
        statistic_id = f"sensor.hikvision_{slug}_total_swipes"
        buckets: dict[datetime, int] = defaultdict(int)
        for ts in timestamps:
            bucket = ts.replace(minute=0, second=0, microsecond=0)
            buckets[bucket] += 1
        running = 0
        stats: list[StatisticData] = []
        for bucket in sorted(buckets):
            running += buckets[bucket]
            stats.append({"start": bucket, "sum": running})
        metadata = StatisticMetaData(
            source="recorder",
            statistic_id=statistic_id,
            unit_of_measurement="swipes",
            has_mean=False,
            has_sum=True,
            name=None,
        )
        async_import_statistics(hass, metadata, stats)


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

    # Map readers to doors via the Hik convention (readers 2k-1, 2k → door k).
    # We trust the enabled-reader count rather than the raw slot count: a
    # controller might expose more slots than wired doors. Fall back to 1.
    enabled_reader_count = sum(1 for r in readers if r.enabled) or 1
    door_count = max(1, (enabled_reader_count + 1) // 2)
    entry.runtime_data.door_count = door_count

    from .coordinator import AcsWorkStatusCoordinator
    coordinator = AcsWorkStatusCoordinator(
        hass,
        entry,
        client,
        door_count=door_count,
        reader_count=enabled_reader_count,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data.coordinator = coordinator

    # Register the device BEFORE forwarding to platforms so child entities'
    # via_device references resolve immediately. The identifier matches the
    # config entry's unique_id (set by the config flow to serial-or-host), so
    # `unique_id` and `(DOMAIN, identifier)` stay in lock-step across reconfigs.
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        manufacturer="Hikvision",
        model=info.get("model", "Access Controller"),
        name=info.get("device_name", "Hikvision Access Controller"),
        sw_version=info.get("firmware_version"),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.runtime_data.stream_task = hass.loop.create_task(_run_stream(hass, entry))

    hass.async_create_task(_run_backfill(hass, entry))

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: HikAccessConfigEntry
) -> None:
    """Reload the entry when options change so the new backfill window applies."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HikAccessConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data: HikAccessData = entry.runtime_data
    if data.stream_task:
        data.stream_task.cancel()
    await data.client.stop()
    return True
