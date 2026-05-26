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
    client owns a :class:`DigestAuth` helper. ``request_ctx`` does an
    unauthenticated probe, then retries with the Authorization header once
    the challenge has been cached. Streaming endpoints (alertStream) need
    the header up-front and are served via :meth:`_ensure_digest_challenge`,
    which sends a cheap preflight ``GET /ISAPI/System/deviceInfo`` to fill
    the cache before opening the stream.
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

    async def _ensure_digest_challenge(self) -> None:
        """Populate the Digest challenge cache via a cheap preflight.

        Streaming endpoints (alertStream) can't tolerate the 401-then-retry
        dance — once the body starts flowing we can't roll back. So before
        opening the stream we issue a one-shot GET against
        ``/ISAPI/System/deviceInfo``; the 401 it returns carries the
        ``WWW-Authenticate: Digest`` header we need to build subsequent
        Authorization values.
        """
        if self._digest is None or self._digest.has_challenge:
            return
        url = f"{self._base_url}/ISAPI/System/deviceInfo"
        async with self._session.get(url) as resp:
            if resp.status == 401:
                www_auth = resp.headers.get("WWW-Authenticate")
                if not www_auth:
                    raise HikAccessAuthError(
                        "401 response had no WWW-Authenticate header"
                    )
                self._digest.handle_challenge(www_auth)
            # If the server didn't 401 (e.g. anonymous access allowed) we
            # have nothing to cache and that's fine — request_ctx will skip
            # the auth branch entirely.

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized events; reconnect with exponential backoff."""
        self._streaming = True
        try:
            # Make sure we have the digest challenge BEFORE opening the
            # stream — otherwise the first connect 401s, the retry kicks
            # in mid-stream, and we get a mess. Preflight failures are
            # tolerated: if it's a real auth problem, the stream will 401
            # too and the normal escalation path runs.
            try:
                await self._ensure_digest_challenge()
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug("digest preflight failed, will let stream surface it: %s", err)

            backoff = self._initial_backoff
            while not self._stop.is_set():
                self._transport = AlertStreamTransport(
                    session=self._session,
                    url=f"{self._base_url}/ISAPI/Event/notification/alertStream",
                    digest=self._digest,
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

        Performs a Digest auth handshake on demand:
        1. If the challenge cache is empty, fire the request unauth'd. On
           401, parse the ``WWW-Authenticate`` header and retry with the
           Authorization header.
        2. If the cache is populated, send the Authorization header on the
           first try. If the server returns 401 (e.g. nonce stale), re-parse
           the challenge and retry once.
        """
        return _DigestRequestCM(self, method, url, kwargs)

    async def get_device_info(self) -> dict[str, str]:
        from .http import fetch_device_info
        return await fetch_device_info(self, self._base_url)

    async def probe_readers(self, max_slots: int = 8) -> list[ReaderInfo]:
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

        if digest.has_challenge:
            headers = dict(base_headers)
            headers["Authorization"] = digest.build_header(method, uri)
            resp = await session.request(method, url, headers=headers, **kwargs)
            if resp.status != 401:
                self._resp = resp
                return resp
            # Cached challenge stale (nonce expired or device rotated). Reparse
            # and try once more. NEVER fall back to an unauthenticated retry —
            # that just guarantees another 401.
            www_auth = resp.headers.get("WWW-Authenticate")
            _LOGGER.debug(
                "Digest 401 on %s after cached-challenge attempt; WWW-Authenticate=%r",
                url, www_auth,
            )
            resp.release()
            if not www_auth or "digest" not in www_auth.lower():
                # The device 401'd without a fresh challenge — invalidate our
                # cache so the next request_ctx triggers a fresh preflight,
                # then surface this 401 to the caller.
                digest.invalidate()
                self._resp = resp
                return resp
            digest.handle_challenge(www_auth)
            headers = dict(base_headers)
            headers["Authorization"] = digest.build_header(method, uri)
            resp2 = await session.request(method, url, headers=headers, **kwargs)
            if resp2.status == 401:
                _LOGGER.debug(
                    "Digest still 401 after stale-nonce retry on %s; "
                    "WWW-Authenticate=%r",
                    url, resp2.headers.get("WWW-Authenticate"),
                )
                # Make sure the next attempt does a fresh preflight rather than
                # reusing whatever the device just rejected.
                digest.invalidate()
            self._resp = resp2
            return resp2

        # No cached challenge: try unauth'd first to harvest one.
        resp = await session.request(method, url, headers=base_headers, **kwargs)
        if resp.status != 401:
            self._resp = resp
            return resp
        www_auth = resp.headers.get("WWW-Authenticate")
        _LOGGER.debug(
            "Digest 401 on %s (cache empty); WWW-Authenticate=%r", url, www_auth,
        )
        resp.release()
        if not www_auth or "digest" not in www_auth.lower():
            # No usable challenge — let the caller surface the 401.
            self._resp = resp
            return resp
        digest.handle_challenge(www_auth)
        headers = dict(base_headers)
        headers["Authorization"] = digest.build_header(method, uri)
        resp2 = await session.request(method, url, headers=headers, **kwargs)
        if resp2.status == 401:
            _LOGGER.debug(
                "Digest still 401 after initial challenge harvest on %s; "
                "WWW-Authenticate=%r",
                url, resp2.headers.get("WWW-Authenticate"),
            )
            digest.invalidate()
        self._resp = resp2
        return resp2

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        # Keep the digest challenge cached so subsequent requests can send
        # Authorization on the first try (nc increments to satisfy replay
        # protection). Refreshing per request would double the request rate
        # and trip Hikvision's IP-filter "illegal login" counter — every
        # unauthenticated probe is logged by the device as a failed attempt.
        #
        # If the device sends a fresh nonce via the RFC 7616 §3.5
        # Authentication-Info header on a successful response, fold it into
        # the cache so the very next request uses the new nonce.
        if (
            self._resp is not None
            and self._resp.status not in (401,)
            and self._client._digest is not None
        ):
            auth_info = self._resp.headers.get("Authentication-Info")
            if auth_info:
                self._client._digest.handle_auth_info(auth_info)
        if self._resp is not None and not self._resp.closed:
            self._resp.release()
