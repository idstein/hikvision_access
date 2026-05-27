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
    ) -> None:
        self._session = session
        self._url = url
        self._digest = digest
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        # Lazy import avoids a circular dependency with the client module.
        from . import HikAccessAuthError

        headers: dict[str, str] = {}
        if self._digest is not None:
            # Explicit handshake: probe unauthenticated to harvest a fresh
            # challenge, then open the real stream with the Authorization
            # header already set so the device never 401s mid-body.
            async with self._session.get(self._url) as probe:
                if probe.status == 401:
                    www_auth = probe.headers.get("WWW-Authenticate")
                    if not www_auth:
                        raise HikAccessAuthError("alertStream returned 401")
                    headers["Authorization"] = self._digest.authorization(
                        www_auth, "GET", _request_uri(self._url)
                    )
                # If the probe returned 200 (anonymous access), no auth needed;
                # we re-open below to stream the body cleanly.

        parser = MultipartParser(boundary=b"MIME_boundary")
        async with self._session.get(self._url, headers=headers) as resp:
            if resp.status == 401:
                raise HikAccessAuthError("alertStream returned 401 after retry")
            resp.raise_for_status()
            async for raw in resp.content.iter_any():
                if self._stop.is_set():
                    return
                async for chunk in parser.feed(raw):
                    norm = normalize_event(chunk)
                    if norm is not None:
                        yield norm
