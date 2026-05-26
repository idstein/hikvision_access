"""Tests for the streaming MIME multipart parser."""

from __future__ import annotations

import pytest

from custom_components.hikvision_access.api.multipart import MultipartParser


@pytest.mark.asyncio
async def test_parses_single_complete_chunk(fixtures_dir) -> None:
    data = (fixtures_dir / "single_chunk.bin").read_bytes()
    parser = MultipartParser(boundary=b"MIME_boundary")

    chunks = []
    async for chunk in parser.feed(data):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0]["eventType"] == "AccessControllerEvent"
