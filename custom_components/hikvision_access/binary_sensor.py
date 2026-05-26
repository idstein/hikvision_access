"""Binary sensor entities backed by AcsWorkStatusCoordinator."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikAccessConfigEntry
from .api.discovery import WorkStatus
from .const import DOMAIN
from .coordinator import AcsWorkStatusCoordinator
from .sensor import _slug


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikAccessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    coordinator: AcsWorkStatusCoordinator = data.coordinator  # type: ignore[assignment]
    door_count = data.door_count or 1
    entities: list[BinarySensorEntity] = []
    for door in range(1, door_count + 1):
        entities.append(DoorOpenBinarySensor(entry, coordinator, door))
        entities.append(DoorLockBinarySensor(entry, coordinator, door))
    for r in data.readers:
        if r.enabled:
            entities.append(ReaderOnlineBinarySensor(entry, coordinator, r.slot, r.name))
    entities.append(ControllerTamperBinarySensor(entry, coordinator))
    async_add_entities(entities)


class _Base(CoordinatorEntity[AcsWorkStatusCoordinator], BinarySensorEntity):
    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(
        self, entry: HikAccessConfigEntry, coordinator: AcsWorkStatusCoordinator
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        )


class DoorOpenBinarySensor(_Base):
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(
        self, entry: HikAccessConfigEntry, coordinator: AcsWorkStatusCoordinator, door: int
    ) -> None:
        super().__init__(entry, coordinator)
        self._door = door
        self._attr_unique_id = f"{entry.unique_id}_door_{door}_open"
        self._attr_name = f"Hikvision Door {door} Open"
        self.entity_id = f"binary_sensor.hikvision_door_{door}_open"

    @property
    def is_on(self) -> bool | None:
        ws: WorkStatus | None = self.coordinator.data
        if ws is None or self._door - 1 >= len(ws.door_open):
            return None
        return ws.door_open[self._door - 1]


class DoorLockBinarySensor(_Base):
    """device_class=lock — is_on means UNLOCKED per HA convention."""

    _attr_device_class = BinarySensorDeviceClass.LOCK

    def __init__(
        self, entry: HikAccessConfigEntry, coordinator: AcsWorkStatusCoordinator, door: int
    ) -> None:
        super().__init__(entry, coordinator)
        self._door = door
        self._attr_unique_id = f"{entry.unique_id}_door_{door}_lock"
        self._attr_name = f"Hikvision Door {door} Lock"
        self.entity_id = f"binary_sensor.hikvision_door_{door}_lock"

    @property
    def is_on(self) -> bool | None:
        ws: WorkStatus | None = self.coordinator.data
        if ws is None or self._door - 1 >= len(ws.door_lock):
            return None
        # door_lock True = locked, but device_class=lock wants is_on = unlocked.
        return not ws.door_lock[self._door - 1]


class ReaderOnlineBinarySensor(_Base):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        entry: HikAccessConfigEntry,
        coordinator: AcsWorkStatusCoordinator,
        slot: int,
        name: str,
    ) -> None:
        super().__init__(entry, coordinator)
        self._slot = slot
        slug = _slug(name, slot)
        self._attr_unique_id = f"{entry.unique_id}_reader_{slot}_online"
        self._attr_name = f"Hikvision {name or f'Reader {slot}'} Online"
        self.entity_id = f"binary_sensor.hikvision_{slug}_online"

    @property
    def is_on(self) -> bool | None:
        ws: WorkStatus | None = self.coordinator.data
        if ws is None or self._slot - 1 >= len(ws.reader_online):
            return None
        return ws.reader_online[self._slot - 1]


class ControllerTamperBinarySensor(_Base):
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def __init__(
        self, entry: HikAccessConfigEntry, coordinator: AcsWorkStatusCoordinator
    ) -> None:
        super().__init__(entry, coordinator)
        self._attr_unique_id = f"{entry.unique_id}_tamper"
        self._attr_name = "Hikvision Controller Tamper"
        self.entity_id = "binary_sensor.hikvision_controller_tamper"

    @property
    def is_on(self) -> bool | None:
        ws: WorkStatus | None = self.coordinator.data
        if ws is None:
            return None
        return ws.tamper
