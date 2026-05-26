"""Sensor entities for Hikvision Access Control."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import HikAccessConfigEntry
from .const import DOMAIN, SIGNAL_EVENT


def _slug(name: str, slot: int) -> str:
    """Convert a reader name into an entity-id slug.

    Falls back to ``readerN`` when the device exposes a blank name (e.g.
    disabled reader slots), but disabled readers don't get entities anyway —
    this is a defensive default for the rare enabled-but-unnamed case.
    """
    slug = name.lower().replace(" ", "_")
    return slug or f"reader{slot}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikAccessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one LastEventSensor and one TotalSwipesSensor per enabled reader."""
    data = entry.runtime_data
    entities: list[SensorEntity] = []
    for r in data.readers:
        if r.enabled:
            entities.append(LastEventSensor(entry, reader_slot=r.slot, reader_name=r.name))
            entities.append(TotalSwipesSensor(entry, reader_slot=r.slot, reader_name=r.name))
            entities.append(UniqueVisitorsTodaySensor(entry, reader_slot=r.slot, reader_name=r.name))
    async_add_entities(entities)


class LastEventSensor(SensorEntity):
    """State = last person's name (or card_no) seen on this reader."""

    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self, entry: HikAccessConfigEntry, reader_slot: int, reader_name: str
    ) -> None:
        self._entry = entry
        self._reader_slot = reader_slot
        self._reader_name = reader_name
        slug = _slug(reader_name, reader_slot)
        self._attr_unique_id = f"{entry.unique_id}_reader_{reader_slot}_last_event"
        self._attr_name = f"Hikvision {reader_name or f'Reader {reader_slot}'} Last Event"
        self.entity_id = f"sensor.hikvision_{slug}_last_event"
        self._attr_native_value: str | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}
        # Tie the sensor to the parent controller device. NOTE: if the config
        # flow's unique_id was the host fallback (no serial), promoting to a
        # real serial via reconfigure WILL recreate these entities — accepted
        # trade-off, documented for future debugging.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the SIGNAL_EVENT dispatcher when added."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EVENT, self._on_event)
        )

    @callback
    def _on_event(self, payload: dict[str, Any]) -> None:
        """Update state when an event for our reader_slot arrives."""
        if payload.get("reader_no") != self._reader_slot:
            return
        self._attr_native_value = (
            payload.get("name") or payload.get("card_no") or "unknown"
        )
        self._attr_extra_state_attributes = dict(payload)
        self.async_write_ha_state()


class TotalSwipesSensor(RestoreEntity, SensorEntity):
    """Lifetime cumulative count of card swipes on this reader.

    State_class=total_increasing so HA's long-term statistics keep hourly
    buckets persisted forever. RestoreEntity so the counter survives HA
    restarts (the value is read back from HA's state-store on startup).
    """

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "swipes"

    def __init__(
        self, entry: HikAccessConfigEntry, reader_slot: int, reader_name: str
    ) -> None:
        self._entry = entry
        self._reader_slot = reader_slot
        slug = _slug(reader_name, reader_slot)
        self._attr_unique_id = f"{entry.unique_id}_reader_{reader_slot}_total_swipes"
        self._attr_name = f"Hikvision {reader_name or f'Reader {reader_slot}'} Total Swipes"
        self.entity_id = f"sensor.hikvision_{slug}_total_swipes"
        self._count = 0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        )

    @property
    def native_value(self) -> int:
        return self._count

    async def async_added_to_hass(self) -> None:
        """Restore counter from last state and subscribe to events."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable", None):
            try:
                self._count = int(float(last.state))
            except (TypeError, ValueError):
                self._count = 0
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EVENT, self._on_event)
        )

    @callback
    def _on_event(self, payload: dict[str, Any]) -> None:
        """Increment counter only for card-read events on our reader."""
        if payload.get("reader_no") != self._reader_slot:
            return
        # Denials / system events have no card_no — don't count them.
        if not payload.get("card_no"):
            return
        self._count += 1
        self.async_write_ha_state()


class UniqueVisitorsTodaySensor(RestoreEntity, SensorEntity):
    """Count of distinct cardholders seen today (resets at local midnight).

    Card numbers are PII — they're treated as credentials by the door
    controller. To avoid persisting them through HA's recorder DB, the
    state-store, and the REST/WS APIs, the dedup set holds truncated
    SHA-256 digests of the raw card numbers instead of the card numbers
    themselves. Names (often a friendly display name, not a credential)
    are still exposed via ``cardholders``. RestoreEntity round-trips the
    digest set so a same-day HA restart resumes the count.
    """

    MAX_CARDS = 5000
    _HASH_PREFIX_BYTES = 8  # 64 bits = 1.8e19 distinct values — collision-safe.

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, entry: HikAccessConfigEntry, reader_slot: int, reader_name: str
    ) -> None:
        self._entry = entry
        self._reader_slot = reader_slot
        slug = _slug(reader_name, reader_slot)
        self._attr_unique_id = (
            f"{entry.unique_id}_reader_{reader_slot}_unique_today"
        )
        self._attr_name = (
            f"Hikvision {reader_name or f'Reader {reader_slot}'} Unique Visitors Today"
        )
        self.entity_id = f"sensor.hikvision_{slug}_unique_visitors_today"
        self._card_hashes: set[str] = set()
        self._names: set[str] = set()
        self._last_reset = dt_util.start_of_local_day()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        )

    @staticmethod
    def _hash_card(card_no: str) -> str:
        import hashlib

        return hashlib.sha256(card_no.encode("utf-8")).hexdigest()[
            : UniqueVisitorsTodaySensor._HASH_PREFIX_BYTES * 2
        ]

    @property
    def native_value(self) -> int:
        return len(self._card_hashes)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "cardholders": sorted(self._names),
            # Opaque digests, not card numbers — see class docstring.
            "card_hashes": sorted(self._card_hashes),
            "last_reset": self._last_reset.isoformat(),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        today = dt_util.start_of_local_day()
        last = await self.async_get_last_state()
        if last is not None and last.attributes.get("last_reset"):
            try:
                lr = dt_util.parse_datetime(last.attributes["last_reset"])
            except (TypeError, ValueError):
                lr = today
            if lr is not None and lr == today:
                self._last_reset = lr
                self._card_hashes = set(last.attributes.get("card_hashes") or [])
                self._names = set(last.attributes.get("cardholders") or [])
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EVENT, self._on_event)
        )
        self.async_on_remove(
            async_track_time_change(
                self.hass, self._reset_at_midnight, hour=0, minute=0, second=0
            )
        )

    @callback
    def _on_event(self, payload: dict[str, Any]) -> None:
        if payload.get("reader_no") != self._reader_slot:
            return
        card = payload.get("card_no")
        if not card:
            return
        if len(self._card_hashes) >= self.MAX_CARDS:
            return
        self._card_hashes.add(self._hash_card(card))
        if payload.get("name"):
            self._names.add(payload["name"])
        self.async_write_ha_state()

    @callback
    def _reset_at_midnight(self, _now: datetime) -> None:
        self._card_hashes.clear()
        self._names.clear()
        self._last_reset = dt_util.start_of_local_day()
        self.async_write_ha_state()
