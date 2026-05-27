"""Transport implementations for the ISAPI event stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from .events import normalize_event
from .multipart import MultipartParser

if TYPE_CHECKING:
    from . import HikAccessClient


class AlertStreamTransport:
    """Consume /ISAPI/Event/notification/alertStream as multipart HTTPS.

    Uses the client's shared Digest handshake (``request_ctx``): an
    unauthenticated probe of the alertStream URL, then a single authenticated
    retry of the *same* URL whose 200 response carries the multipart body.
    This is the exact flow ``curl --digest`` uses and the same one that drives
    the working ``AcsWorkStatus`` poll, so the device treats it identically.
    Earlier versions harvested the challenge from a different endpoint
    (deviceInfo); that cross-endpoint nonce made the firmware reply 400.
    """

    def __init__(self, client: HikAccessClient, url: str) -> None:
        self._client = client
        self._url = url
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        # Lazy import avoids a circular dependency with the client module.
        from . import HikAccessAuthError

        parser = MultipartParser(boundary=b"MIME_boundary")
        async with self._client.request_ctx("GET", self._url) as resp:
            if resp.status == 401:
                raise HikAccessAuthError("alertStream returned 401")
            resp.raise_for_status()
            async for raw in resp.content.iter_any():
                if self._stop.is_set():
                    return
                async for chunk in parser.feed(raw):
                    norm = normalize_event(chunk)
                    if norm is not None:
                        yield norm
