"""Config flow for Hikvision Access Control."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import callback

from .api import HikAccessAuthError, HikAccessClient
from .const import (
    BACKFILL_DAYS_MAX,
    BACKFILL_DAYS_MIN,
    CONF_BACKFILL_DAYS,
    CONF_VERIFY_SSL,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_SSL, default=True): bool,
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_BACKFILL_DAYS, default=DEFAULT_BACKFILL_DAYS): vol.All(
            int, vol.Range(min=BACKFILL_DAYS_MIN, max=BACKFILL_DAYS_MAX)
        ),
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
            client: HikAccessClient | None = None
            try:
                client = HikAccessClient(
                    host=user_input[CONF_HOST],
                    port=user_input[CONF_PORT],
                    username=user_input[CONF_USERNAME],
                    password=user_input[CONF_PASSWORD],
                    ssl=user_input[CONF_SSL],
                    verify_ssl=user_input[CONF_VERIFY_SSL],
                )
                info = await client.get_device_info()
            except HikAccessAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error while validating Hikvision config")
                errors["base"] = "cannot_connect"
            else:
                serial = info.get("serial_number")
                if not serial:
                    _LOGGER.warning(
                        "Hikvision device returned no serial_number; "
                        "falling back to host (%s) as the unique_id",
                        user_input[CONF_HOST],
                    )
                    serial = user_input[CONF_HOST]
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info.get("model", "Hikvision"), data=user_input
                )
            finally:
                if client is not None:
                    await client.stop()

        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlow):
    """Handle the options flow for the Hikvision Access integration.

    Newer HA versions expose ``self.config_entry`` as a read-only property
    that HA fills in itself before ``async_step_init`` runs, so we don't
    accept or assign it in ``__init__``.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options for the integration."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_BACKFILL_DAYS,
            self.config_entry.data.get(CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS),
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_BACKFILL_DAYS, default=current): vol.All(
                    int, vol.Range(min=BACKFILL_DAYS_MIN, max=BACKFILL_DAYS_MAX)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
