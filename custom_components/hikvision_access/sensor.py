"""Sensor entities for Hikvision Access Control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

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
