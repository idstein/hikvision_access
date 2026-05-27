"""Transport implementations for the ISAPI event stream."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .digest import DigestAuth
from .events import normalize_event
from .multipart import MultipartParser

_LOGGER = logging.getLogger(__name__)


def _request_uri(url: str) -> str:
    parts = urlsplit(url)
    uri = parts.path or "/"
    if parts.query:
        uri = f"{uri}?{parts.query}"
    return uri


class AlertStreamTransport:
    """Consume /ISAPI/Event/notification/alertStream as multipart HTTPS.

    A streaming response can't tolerate a 401-then-retry once the body is
    flowing, so the transport does its own explicit Digest handshake before
    streaming: an unauthenticated probe to harvest a fresh challenge, then
    the real stream opened with ``Authorization: Digest ...`` on the first
    request. The :class:`DigestAuth` helper is stateless, so there's no
    pre-populated cache to rely on.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        digest: DigestAuth | None,
        challenge_url: str | None = None,
    ) -> None:
        self._session = session
        self._url = url
        self._digest = digest
        # Endpoint used to harvest the Digest challenge. We deliberately DON'T
        # probe the alertStream URL itself: opening that long-lived multipart
        # endpoint twice in quick succession makes some firmware reply 400.
        # A cheap, non-streaming GET (deviceInfo) yields the same realm/nonce.
        self._challenge_url = challenge_url
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        # Lazy import avoids a circular dependency with the client module.
        from . import HikAccessAuthError

        headers: dict[str, str] = {}
        if self._digest is not None and self._challenge_url is not None:
            # Harvest a fresh challenge from the cheap endpoint, then build the
            # Authorization header for the alertStream URI (digest nonces are
            # realm-scoped, so a challenge from deviceInfo authenticates the
            # alertStream request in the same realm). Open the stream exactly
            # once, authenticated, so the device never 401s/400s mid-body.
            async with self._session.get(self._challenge_url) as probe:
                if probe.status == 401:
                    www_auth = probe.headers.get("WWW-Authenticate")
                    if not www_auth:
                        raise HikAccessAuthError("challenge probe returned 401 with no header")
                    headers["Authorization"] = self._digest.authorization(
                        www_auth, "GET", _request_uri(self._url)
                    )
                # 200 → anonymous access; open the stream without auth.

        parser = MultipartParser(boundary=b"MIME_boundary")
        async with self._session.get(self._url, headers=headers) as resp:
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
