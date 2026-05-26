"""Transport implementations for the ISAPI event stream."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .events import normalize_event
from .multipart import MultipartParser

_LOGGER = logging.getLogger(__name__)


class AlertStreamTransport:
    """Consume /ISAPI/Event/notification/alertStream as multipart HTTPS."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        auth: aiohttp.BasicAuth | None,
    ) -> None:
        self._session = session
        self._url = url
        self._auth = auth
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        parser = MultipartParser(boundary=b"MIME_boundary")
        async with self._session.get(self._url, auth=self._auth) as resp:
            if resp.status == 401:
                raise PermissionError("alertStream returned 401")
            resp.raise_for_status()
            async for raw in resp.content.iter_any():
                if self._stop.is_set():
                    return
                async for chunk in parser.feed(raw):
                    norm = normalize_event(chunk)
                    if norm is not None:
                        yield norm
