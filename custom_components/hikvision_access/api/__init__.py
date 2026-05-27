"""Pure-Python ISAPI client. No Home Assistant imports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import aiohttp

from .digest import DigestAuth
from .transport import AlertStreamTransport

if TYPE_CHECKING:
    from .discovery import ReaderInfo

_LOGGER = logging.getLogger(__name__)

_BACKOFF_MAX = 60
_AUTH_FAIL_LIMIT = 2
# Minimum spacing between outbound ISAPI requests (seconds). Kept in the
# pure-Python layer so the client is self-contained; the HA const mirrors it.
MIN_REQUEST_INTERVAL = 0.5


class HikAccessError(Exception):
    """Base class for domain errors from the Hikvision ISAPI client."""


class HikAccessAuthError(HikAccessError):
    """Raised when the controller rejects credentials (HTTP 401).

    Distinct from ``PermissionError`` (OS-level EACCES) so HA's
    ``except OSError`` handlers don't swallow auth failures.
    """


def request_uri(url: str) -> str:
    """Return the request-URI (path + ?query) used in Digest HA2 hashing.

    Exposed for the helper modules in ``api/`` that build their own
    Authorization header off a ``DigestAuth`` handed down by the client.
    """
    parts = urlsplit(url)
    uri = parts.path or "/"
    if parts.query:
        uri = f"{uri}?{parts.query}"
    return uri


class HikAccessClient:
    """Single-controller ISAPI client.

    Owns one ``aiohttp.ClientSession``. ``events()`` is an async generator
    that yields normalized AccessControllerEvents and auto-reconnects with
    exponential backoff on transport errors. Two consecutive 401s raise
    ``HikAccessAuthError`` (so HA can surface ``ConfigEntryAuthFailed``).

    Hikvision ISAPI requires HTTP Digest auth (RFC 7616), not Basic, so the
    client owns a stateless :class:`DigestAuth` helper. Every request does
    its own self-contained ``unauthenticated → 401 → parse challenge →
    authenticated retry`` handshake with a request-local nonce. The device
    issues single-use nonces, so caching a challenge buys nothing and a
    shared cache only invites races between concurrent requests — hence no
    shared challenge state anywhere.
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
        self._digest = DigestAuth(username, password) if username else None
        scheme = "https" if ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"
        # ssl=False disables verification; verify_ssl=True needs an explicit
        # SSLContext so we get real cert checking, not aiohttp's default behaviour.
        if verify_ssl:
            import ssl as _ssl

            ssl_ctx: _ssl.SSLContext | bool = _ssl.create_default_context()
        else:
            ssl_ctx = False
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self._session = aiohttp.ClientSession(connector=connector)
        self._transport: AlertStreamTransport | None = None
        self._stop = asyncio.Event()
        self._initial_backoff = 1.0
        self._auth_fail_count = 0
        # True while events() is iterating; lets stop() know whether to close
        # the session itself or defer to events()'s finally clause.
        self._streaming = False
        # Throttle: serialize requests and keep them MIN_REQUEST_INTERVAL
        # apart so the setup burst doesn't trip the device's IP-filter.
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._min_request_interval = MIN_REQUEST_INTERVAL

    async def _throttle(self) -> None:
        """Block until at least ``_min_request_interval`` has elapsed since the
        previous outbound request, then record the new timestamp."""
        async with self._request_lock:
            now = asyncio.get_running_loop().time()
            wait = self._min_request_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = asyncio.get_running_loop().time()

    async def stop(self) -> None:
        """Signal the event loop to terminate and release the aiohttp session.

        If ``events()`` is currently running, the session is left open — its
        ``finally`` clause will close it after the in-flight request unwinds,
        avoiding aiohttp's ``RuntimeError("Connection closed.")`` from
        mid-stream teardown. If ``events()`` was never called (e.g. the client
        was only used for ``get_device_info`` in config flow / setup entry),
        the session is closed here so it doesn't leak.
        """
        self._stop.set()
        if self._transport:
            await self._transport.stop()
        if not self._streaming and not self._session.closed:
            await self._session.close()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized events; reconnect with exponential backoff."""
        self._streaming = True
        try:
            backoff = self._initial_backoff
            while not self._stop.is_set():
                self._transport = AlertStreamTransport(
                    client=self,
                    url=f"{self._base_url}/ISAPI/Event/notification/alertStream",
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
                except (aiohttp.ClientError, TimeoutError) as err:
                    _LOGGER.debug("transport error, will retry: %s", err)

                if self._stop.is_set():
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
        finally:
            # Now that the generator is closing for good, release resources.
            self._transport = None
            self._streaming = False
            if not self._session.closed:
                await self._session.close()

    def request_ctx(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> _DigestRequestCM:
        """Return an async context manager that yields an aiohttp response.

        Performs a self-contained Digest handshake per request: fire the
        request unauthenticated, and on a 401 carrying a Digest
        ``WWW-Authenticate`` challenge, retry once with an Authorization
        header built from that fresh challenge. No challenge state is shared
        between requests, so concurrent callers never interfere.
        """
        return _DigestRequestCM(self, method, url, kwargs)

    async def get_device_info(self) -> dict[str, str]:
        from .http import fetch_device_info
        return await fetch_device_info(self, self._base_url)

    async def probe_readers(self, max_slots: int = 4) -> list[ReaderInfo]:
        from .http import probe_card_readers
        return await probe_card_readers(self, self._base_url, max_slots)

    async def get_acs_work_status(self) -> dict[str, Any]:
        from .http import fetch_acs_work_status
        return await fetch_acs_work_status(self, self._base_url)

    async def backfill(
        self,
        start_time: str,
        end_time: str,
        already_seen: Iterable[int] = (),
    ) -> AsyncIterator[dict[str, Any]]:
        """Replay AcsEvents in ``[start_time, end_time]`` that the device buffered.

        ``start_time`` / ``end_time`` are ISO8601 strings with a timezone
        (e.g. ``"2026-05-26T10:00:00+02:00"`` or ``"...Z"``). Yields
        normalized event dicts with ``backfilled=True``; serials already in
        ``already_seen`` are skipped, and any serial yielded is added to
        the working set so per-page dedup carries across pages.
        """
        from .backfill import fetch_pages, replay_page
        seen = set(already_seen)
        async for page in fetch_pages(self, self._base_url, start_time, end_time):
            for evt in replay_page(page, seen):
                seen.add(evt["serial_no"])
                yield evt


class _DigestRequestCM:
    """Async context manager that wraps Digest-aware request issuing.

    Mirrors the call shape of ``aiohttp.ClientSession.request(...)`` so
    helpers can write ``async with client.request_ctx("GET", url) as r:``
    in place of ``async with session.get(url, auth=auth) as r:``.
    """

    def __init__(
        self,
        client: HikAccessClient,
        method: str,
        url: str,
        kwargs: dict[str, Any],
    ) -> None:
        self._client = client
        self._method = method
        self._url = url
        self._kwargs = kwargs
        self._resp: aiohttp.ClientResponse | None = None

    async def __aenter__(self) -> aiohttp.ClientResponse:
        # The throttle paces requests AND serializes the handshake so the
        # unauthenticated probe and its authenticated retry stay together.
        await self._client._throttle()
        session = self._client._session
        digest = self._client._digest
        method = self._method
        url = self._url
        kwargs = self._kwargs

        if digest is None:
            self._resp = await session.request(method, url, **kwargs)
            return self._resp

        uri = request_uri(url)
        # Pop headers so we can mutate them without leaking back into the caller.
        base_headers = dict(kwargs.pop("headers", {}) or {})

        # Self-contained handshake: unauthenticated probe first.
        resp = await session.request(method, url, headers=base_headers, **kwargs)
        if resp.status != 401:
            self._resp = resp
            return resp
        www_auth = resp.headers.get("WWW-Authenticate")
        _LOGGER.debug("Digest 401 on %s; WWW-Authenticate=%r", url, www_auth)
        resp.release()
        if not www_auth or "digest" not in www_auth.lower():
            # No usable challenge — let the caller surface the 401.
            self._resp = resp
            return resp
        auth = digest.authorization(www_auth, method, uri)
        headers = dict(base_headers)
        headers["Authorization"] = auth
        resp2 = await session.request(method, url, headers=headers, **kwargs)
        if resp2.status == 401:
            _LOGGER.debug(
                "Digest still 401 after authenticated retry on %s; "
                "WWW-Authenticate=%r",
                url, resp2.headers.get("WWW-Authenticate"),
            )
        self._resp = resp2
        return resp2

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        # No shared challenge state to invalidate — just release the response.
        if self._resp is not None and not self._resp.closed:
            self._resp.release()
