"""Streaming MIME multipart parser for Hikvision alertStream."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator


class MultipartParser:
    """Incrementally parse application/x-mixed-replace style multipart streams."""

    def __init__(self, boundary: bytes) -> None:
        self._sep = b"--" + boundary
        self._buf = b""

    async def feed(self, data: bytes) -> AsyncIterator[dict]:
        self._buf += data
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
