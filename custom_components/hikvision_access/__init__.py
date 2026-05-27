"""The Hikvision Access Control integration."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMetaData,
    async_add_external_statistics,
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

if TYPE_CHECKING:
    from .coordinator import AcsWorkStatusCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


@dataclass
class HikAccessData:
    """Runtime data for one configured Hikvision controller."""

    client: HikAccessClient
    device_info: dict[str, str]
    readers: list[ReaderInfo] = field(default_factory=list)
    stream_task: asyncio.Task | None = None
    coordinator: AcsWorkStatusCoordinator | None = None
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
    except Exception:
        _LOGGER.exception("Hikvision event stream task crashed; events will resume on next entry reload")


async def _run_backfill(hass: HomeAssistant, entry: HikAccessConfigEntry) -> None:
    """Replay AcsEvents the controller buffered while HA was down.

    Reads ``last_serial_no`` / ``last_event_time`` from restored TotalSwipes
    sensor states. The fetch window is bounded by ``last_event_time`` on a warm
    restart (so we pull only new events instead of re-paginating the whole
    window), clamped to the ``backfill_days`` option (default 30 days; 0
    disables backfill). The serial high-water mark dedups within that window.
    Logged at warning on failure — does NOT block setup.
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

    # Restore the high-water mark from the persisted sensor attributes. The
    # serial is a monotonically increasing per-device counter, so anything at or
    # below ``high_water`` was already ingested (counted and fired) in a prior
    # session. ``last_event_time`` is the timestamp of that newest event.
    seen: set[int] = set()
    last_event_time: datetime | None = None
    for state in hass.states.async_all("sensor"):
        if not state.entity_id.startswith("sensor.hikvision_"):
            continue
        if "total_swipes" not in state.entity_id:
            continue
        s = state.attributes.get("last_serial_no")
        if isinstance(s, int):
            seen.add(s)
        let = state.attributes.get("last_event_time")
        if isinstance(let, str):
            parsed = dt_util.parse_datetime(let)
            if parsed is not None:
                parsed = dt_util.as_utc(parsed)
                if last_event_time is None or parsed > last_event_time:
                    last_event_time = parsed
    high_water = max(seen, default=0)

    end = dt_util.utcnow()
    deep_start = end - timedelta(days=int(backfill_days))
    # Warm restart: start at the last event we ingested so we pull only what's
    # new. Clamp to the deep window so a stale high-water mark can't widen the
    # fetch (and the device's buffer doesn't reach back further anyway). Cold
    # start (no prior events): import the full deep window once.
    start = max(last_event_time, deep_start) if last_event_time is not None else deep_start

    def iso(dt: datetime) -> str:
        # Keep the explicit ``+00:00`` offset — Hikvision firmware on some
        # controllers (e.g. DS-K2702WX V1.7.4) 400 the search request when
        # the time uses the ``Z`` Zulu shorthand.
        return dt.isoformat(timespec="seconds")

    # slot -> list of UTC datetimes for new card events the device replayed
    replayed_per_slot: dict[int, list[datetime]] = defaultdict(list)

    try:
        async for evt in data.client.backfill(start_time=iso(start), end_time=iso(end)):
            # Skip anything already ingested in a prior session. With a
            # timestamp-bounded window this only trims the boundary overlap, but
            # it's what stops a restart from re-firing old events onto the bus —
            # inflating the counter, resurfacing old cardholders, re-triggering
            # automations — and from double-counting them into the statistics.
            if evt["serial_no"] <= high_water:
                continue
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

    # Append the new per-hour counts to EXTERNAL statistics — a namespace
    # separate from the live `sensor.*_total_swipes` entity.
    #
    # We must NOT import into the entity's own statistic_id: that sensor is a
    # recorder-owned `total_increasing` entity, so the recorder already
    # compiles hourly rows there. A second writer collides on
    # (metadata_id, start_ts) → "UNIQUE constraint failed".
    #
    # The cumulative ``sum`` continues from the last stored row (read via
    # get_last_statistics), so we only append the new hours instead of
    # recomputing the whole history on every restart. The user charts the
    # `hikvision_access:..._swipes_history` statistic for the hourly/daily view.
    if not replayed_per_slot:
        return
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import get_last_statistics

    from .sensor import _slug

    for slot, timestamps in replayed_per_slot.items():
        r = reader_index.get(slot)
        if r is None:
            continue
        slug = _slug(r.name, slot)
        statistic_id = f"{DOMAIN}:{slug}_swipes_history"
        base_sum = 0.0
        try:
            last_stats = await get_instance(hass).async_add_executor_job(
                get_last_statistics, hass, 1, statistic_id, True, {"sum"}
            )
            rows = last_stats.get(statistic_id)
            if rows and rows[0].get("sum") is not None:
                base_sum = float(rows[0]["sum"])
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "could not read last statistic for %s; seeding sum at 0",
                statistic_id,
                exc_info=True,
            )
        buckets: dict[datetime, int] = defaultdict(int)
        for ts in timestamps:
            bucket = ts.replace(minute=0, second=0, microsecond=0)
            buckets[bucket] += 1
        running = base_sum
        stats: list[StatisticData] = []
        for bucket in sorted(buckets):
            running += buckets[bucket]
            # `state` = swipes in this hour, `sum` = cumulative. HA's
            # statistics-graph "change" view derives per-hour swipes from the
            # sum deltas, so continuing `sum` from the stored value keeps the
            # chart correct even across the boundary hour.
            stats.append(
                {"start": bucket, "state": float(buckets[bucket]), "sum": running}
            )
        metadata: StatisticMetaData = {
            "source": DOMAIN,
            "statistic_id": statistic_id,
            "name": f"{r.name or f'Reader {slot}'} swipes (history)",
            "unit_of_measurement": "swipes",
            "has_sum": True,
        }
        # HA 2026.x replaces the ``has_mean`` flag with ``mean_type``; older
        # releases only know ``has_mean``. Set whichever the running version
        # understands so we neither crash on old HA nor emit the deprecation
        # warning on new HA. A wrong/absent import just falls back to has_mean.
        try:
            from homeassistant.components.recorder.models import StatisticMeanType

            metadata["mean_type"] = StatisticMeanType.NONE
        except ImportError:
            metadata["has_mean"] = False
        async_add_external_statistics(hass, metadata, stats)


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
    # Single broad try/except so any failure between client construction and
    # the stream task being scheduled triggers client.stop() — otherwise the
    # aiohttp session leaks ("Unclosed client session" log line) on every
    # ConfigEntryNotReady retry.
    try:
        try:
            info = await client.get_device_info()
            readers = await client.probe_readers()
        except HikAccessAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:
            raise ConfigEntryNotReady(
                f"Cannot connect to {d[CONF_HOST]}: {err}"
            ) from err

        entry.runtime_data = HikAccessData(
            client=client, device_info=info, readers=readers
        )

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
        # config entry's unique_id (set by the config flow to serial-or-host),
        # so `unique_id` and `(DOMAIN, identifier)` stay in lock-step across
        # reconfigs.
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

        entry.runtime_data.stream_task = hass.loop.create_task(
            _run_stream(hass, entry)
        )
    except BaseException:
        # Any failure (including ConfigEntryNotReady / ConfigEntryAuthFailed
        # / CancelledError): close the client so its aiohttp session doesn't
        # leak. HA will recreate the entry on the next retry.
        await client.stop()
        raise

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
