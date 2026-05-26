"""Streaming MIME multipart parser for Hikvision alertStream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


class MultipartBufferOverflow(RuntimeError):
    """Raised when the internal buffer grows past ``max_buffer`` without ever
    seeing a boundary marker — protects against an unresponsive peer that
    streams bytes without ever delimiting them."""


class MultipartParser:
    """Incrementally parse application/x-mixed-replace style multipart streams.

    A single instance is NOT safe for concurrent ``feed()`` calls — the buffer
    is shared mutable state. One parser per connection.
    """

    DEFAULT_MAX_BUFFER = 1 << 20  # 1 MiB

    def __init__(self, boundary: bytes, max_buffer: int | None = None) -> None:
        self._sep = b"--" + boundary
        self._buf = b""
        self._max_buffer = max_buffer if max_buffer is not None else self.DEFAULT_MAX_BUFFER

    async def feed(self, data: bytes) -> AsyncIterator[dict[str, Any]]:
        self._buf += data
        if len(self._buf) > self._max_buffer:
            raise MultipartBufferOverflow(
                f"buffer exceeded {self._max_buffer} bytes without seeing a boundary"
            )
        while True:
            # Find a complete part: --boundary <CRLF> headers <CRLF><CRLF> body <CRLF>--boundary
            first = self._buf.find(self._sep)
            if first < 0:
                return
            second = self._buf.find(self._sep, first + len(self._sep))
            if second < 0:
                return  # incomplete part; wait for more data

            part = self._buf[first + len(self._sep) : second]
            self._buf = self._buf[second:]

            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue  # malformed; skip
            body = part[header_end + 4 :].rstrip(b"\r\n")
            try:
                yield json.loads(body)
            except (ValueError, json.JSONDecodeError):
                continue
