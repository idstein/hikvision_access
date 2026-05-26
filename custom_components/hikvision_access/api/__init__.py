"""Pure-Python ISAPI client. No Home Assistant imports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .transport import AlertStreamTransport

_LOGGER = logging.getLogger(__name__)

_BACKOFF_MAX = 60
_AUTH_FAIL_LIMIT = 2


class HikAccessError(Exception):
    """Base class for domain errors from the Hikvision ISAPI client."""


class HikAccessAuthError(HikAccessError):
    """Raised when the controller rejects credentials (HTTP 401).

    Distinct from ``PermissionError`` (OS-level EACCES) so HA's
    ``except OSError`` handlers don't swallow auth failures.
    """


class HikAccessClient:
    """Single-controller ISAPI client.

    Owns one ``aiohttp.ClientSession``. ``events()`` is an async generator
    that yields normalized AccessControllerEvents and auto-reconnects with
    exponential backoff on transport errors. Two consecutive 401s raise
    ``HikAccessAuthError`` (so HA can surface ``ConfigEntryAuthFailed``).
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        ssl: bool,
        verify_ssl: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._auth = aiohttp.BasicAuth(username, password) if username else None
        scheme = "https" if ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"
        connector = aiohttp.TCPConnector(ssl=verify_ssl)
        self._session = aiohttp.ClientSession(connector=connector)
        self._transport: AlertStreamTransport | None = None
        self._stop = asyncio.Event()
        self._initial_backoff = 1.0
        self._auth_fail_count = 0

    async def stop(self) -> None:
        """Signal the event loop to terminate; do NOT close the session here.

        Session close is performed lazily in ``events()`` when the loop
        actually exits (either via stop() OR via raised exception), avoiding
        the aiohttp ``RuntimeError("Connection closed.")`` that happens when
        the connector is torn down mid-stream.
        """
        self._stop.set()
        if self._transport:
            await self._transport.stop()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized events; reconnect with exponential backoff."""
        try:
            backoff = self._initial_backoff
            while not self._stop.is_set():
                self._transport = AlertStreamTransport(
                    session=self._session,
                    url=f"{self._base_url}/ISAPI/Event/notification/alertStream",
                    auth=self._auth,
                )
                try:
                    async for evt in self._transport.stream():
                        backoff = self._initial_backoff
                        self._auth_fail_count = 0
                        yield evt
                except HikAccessAuthError:
                    self._auth_fail_count += 1
                    if self._auth_fail_count >= _AUTH_FAIL_LIMIT:
                        raise
                except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                    _LOGGER.debug("transport error, will retry: %s", err)

                if self._stop.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
        finally:
            # Now that the generator is closing for good, release resources.
            self._transport = None
            await self._session.close()

    async def get_device_info(self) -> dict[str, str]:
        from .http import fetch_device_info
        return await fetch_device_info(self._session, self._base_url, self._auth)

    async def probe_readers(self, max_slots: int = 8):
        from .http import probe_card_readers
        return await probe_card_readers(self._session, self._base_url, self._auth, max_slots)

    async def get_acs_work_status(self):
        from .http import fetch_acs_work_status
        return await fetch_acs_work_status(self._session, self._base_url, self._auth)
