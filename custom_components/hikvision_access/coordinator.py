"""DataUpdateCoordinator for /ISAPI/AccessControl/AcsWorkStatus polling."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HikAccessClient
from .api.discovery import WorkStatus, parse_acs_work_status
from .const import DOMAIN, POLL_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class AcsWorkStatusCoordinator(DataUpdateCoordinator[WorkStatus]):
    """Poll AcsWorkStatus on a 10s cadence; surface UpdateFailed on errors."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: HikAccessClient,
        door_count: int,
        reader_count: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_acs_work_status",
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        )
        self._client = client
        self._door_count = door_count
        self._reader_count = reader_count

    async def _async_update_data(self) -> WorkStatus:
        try:
            raw = await self._client.get_acs_work_status()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
        return parse_acs_work_status(raw, self._door_count, self._reader_count)
