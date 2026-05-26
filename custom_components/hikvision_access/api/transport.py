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

    The Digest challenge must be in :class:`DigestAuth`'s cache before this
    transport is constructed — the client's ``_ensure_digest_challenge``
    handles that preflight. Once cached we send ``Authorization: Digest ...``
    on the very first request, so the device doesn't 401-and-retry us
    mid-stream.
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
        if self._digest is not None and self._digest.has_challenge:
            headers["Authorization"] = self._digest.build_header("GET", _request_uri(self._url))

        parser = MultipartParser(boundary=b"MIME_boundary")
        async with self._session.get(self._url, headers=headers) as resp:
            if resp.status == 401:
                # Try once more after refreshing the challenge cache, in
                # case our cached nonce went stale between preflight and
                # stream open.
                www_auth = resp.headers.get("WWW-Authenticate")
                if self._digest is not None and www_auth:
                    self._digest.handle_challenge(www_auth)
                    headers["Authorization"] = self._digest.build_header(
                        "GET", _request_uri(self._url)
                    )
                    async with self._session.get(self._url, headers=headers) as resp2:
                        if resp2.status == 401:
                            raise HikAccessAuthError("alertStream returned 401 after retry")
                        resp2.raise_for_status()
                        async for raw in resp2.content.iter_any():
                            if self._stop.is_set():
                                return
                            async for chunk in parser.feed(raw):
                                norm = normalize_event(chunk)
                                if norm is not None:
                                    yield norm
                        return
                raise HikAccessAuthError("alertStream returned 401")
            resp.raise_for_status()
            async for raw in resp.content.iter_any():
                if self._stop.is_set():
                    return
                async for chunk in parser.feed(raw):
                    norm = normalize_event(chunk)
                    if norm is not None:
                        yield norm
