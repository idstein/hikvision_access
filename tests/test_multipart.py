"""Tests for the streaming MIME multipart parser."""

from __future__ import annotations

import pytest

from custom_components.hikvision_access.api.multipart import (
    MultipartBufferOverflow,
    MultipartParser,
)


@pytest.mark.asyncio
async def test_parses_single_complete_chunk(fixtures_dir) -> None:
    data = (fixtures_dir / "single_chunk.bin").read_bytes()
    parser = MultipartParser(boundary=b"MIME_boundary")

    chunks = []
    async for chunk in parser.feed(data):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0]["eventType"] == "AccessControllerEvent"


@pytest.mark.asyncio
async def test_overflow_raises_when_no_boundary_seen() -> None:
    parser = MultipartParser(boundary=b"MIME_boundary", max_buffer=4096)
    with pytest.raises(MultipartBufferOverflow):
        async for _ in parser.feed(b"x" * 5000):
            pass
