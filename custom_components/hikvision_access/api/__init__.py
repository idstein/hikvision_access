"""Pure-Python ISAPI client. No Home Assistant imports."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .transport import AlertStreamTransport

_LOGGER = logging.getLogger(__name__)


class HikAccessError(Exception):
    """Base class for domain errors from the Hikvision ISAPI client."""


class HikAccessAuthError(HikAccessError):
    """Raised when the controller rejects credentials (HTTP 401).

    Distinct from ``PermissionError`` (which carries OS-level EACCES
    semantics) so HA's ``except OSError`` handlers don't accidentally
    swallow auth failures.
    """


class HikAccessClient:
    """Single-controller ISAPI client."""

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

    async def stop(self) -> None:
        if self._transport:
            await self._transport.stop()
        # Note: session close intentionally deferred so an in-flight
        # iter_any() can terminate on natural EOF instead of raising
        # RuntimeError("Connection closed.") when the connector is torn
        # down mid-stream. A dedicated close() (or context manager exit)
        # in Task 2.7 should release the session.

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        self._transport = AlertStreamTransport(
            session=self._session,
            url=f"{self._base_url}/ISAPI/Event/notification/alertStream",
            auth=self._auth,
        )
        async for evt in self._transport.stream():
            yield evt
