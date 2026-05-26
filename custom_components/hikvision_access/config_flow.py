"""Config flow for Hikvision Access Control."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME

from .api import HikAccessAuthError, HikAccessClient
from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DEFAULT_VERIFY_SSL, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_SSL, default=True): bool,
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class HikAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hikvision Access Control."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = HikAccessClient(
                host=user_input[CONF_HOST],
                port=user_input[CONF_PORT],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                ssl=user_input[CONF_SSL],
                verify_ssl=user_input[CONF_VERIFY_SSL],
            )
            try:
                info = await client.get_device_info()
            except HikAccessAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while validating Hikvision config")
                errors["base"] = "cannot_connect"
            else:
                serial = info.get("serial_number", user_input[CONF_HOST])
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info.get("model", "Hikvision"), data=user_input
                )
            finally:
                await client.stop()

        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)
