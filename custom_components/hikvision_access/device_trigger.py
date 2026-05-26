"""Device triggers for Hikvision Access Control.

Exposes four trigger types to the HA UI automation editor. Each is a thin
filter over the integration's bus event ``EVENT_BUS_NAME`` matched on the
normalized ``minor_label`` field.

* ``card_read``       — any AccessControllerEvent carrying a ``card_no``.
* ``card_denied``     — any denial code (invalid_period, invalid_password,
                        expired, unregistered, blocked, invalid_door,
                        authentication_failed, fingerprint_invalid).
* ``door_forced``     — minor_label == "door_forced_open".
* ``door_held_open``  — minor_label == "door_held_open".
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .api.events import DENIAL_MINOR_CODES, MINOR_EVENT_LABELS
from .const import DOMAIN, EVENT_BUS_NAME

TRIGGER_TYPES: frozenset[str] = frozenset({
    "card_read", "card_denied", "door_forced", "door_held_open",
})

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)}
)


def _denial_labels() -> set[str]:
    """All minor_label values that denote a denial."""
    return {MINOR_EVENT_LABELS[c] for c in DENIAL_MINOR_CODES if c in MINOR_EVENT_LABELS}


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List trigger types available on a given device — empty for foreign devices."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None or not any(i[0] == DOMAIN for i in device.identifiers):
        return []
    return [
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: t}
        for t in TRIGGER_TYPES
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Compose an event-trigger that filters our bus event by minor_label."""
    trigger_type = config[CONF_TYPE]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_BUS_NAME,
            event_trigger.CONF_EVENT_DATA: {},  # Filtered below via a wrapper.
        }
    )
    matcher = _matcher_for(trigger_type)

    async def filtered_action(run_variables: dict[str, Any], context=None) -> None:
        event = run_variables.get("trigger", {}).get("event")
        if event is not None and matcher(event.data):
            await action(run_variables, context)

    return await event_trigger.async_attach_trigger(
        hass, event_config, filtered_action, trigger_info, platform_type="device"
    )


def _matcher_for(trigger_type: str):
    if trigger_type == "card_read":
        return lambda data: bool(data.get("card_no"))
    if trigger_type == "card_denied":
        denials = _denial_labels()
        return lambda data: data.get("minor_label") in denials
    if trigger_type == "door_forced":
        return lambda data: data.get("minor_label") == "door_forced_open"
    if trigger_type == "door_held_open":
        return lambda data: data.get("minor_label") == "door_held_open"
    return lambda _data: False
