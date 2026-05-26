"""Live smoke test against a real Hikvision controller.

Skipped by default. To run:

    cp tests/live/.env.example tests/live/.env
    # set HIK_PASSWORD in tests/live/.env
    set -a; source tests/live/.env; set +a
    RUN_LIVE=1 PYTHONPATH=. pytest tests/live/ -v
"""

from __future__ import annotations

import asyncio
import os

import pytest

from custom_components.hikvision_access.api import HikAccessClient

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to run live device tests"
)


@pytest.mark.asyncio
async def test_device_info_returns_model_and_serial() -> None:
    client = HikAccessClient(
        host=os.environ["HIK_HOST"],
        port=int(os.environ.get("HIK_PORT", "443")),
        username=os.environ["HIK_USERNAME"],
        password=os.environ["HIK_PASSWORD"],
        ssl=True,
        verify_ssl=os.environ.get("HIK_VERIFY_SSL", "false").lower() == "true",
    )
    try:
        info = await client.get_device_info()
        assert info["model"], "device_info missing model"
        assert info.get("serial_number"), "device_info missing serial_number"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_probe_readers_returns_at_least_one_enabled() -> None:
    client = HikAccessClient(
        host=os.environ["HIK_HOST"],
        port=int(os.environ.get("HIK_PORT", "443")),
        username=os.environ["HIK_USERNAME"],
        password=os.environ["HIK_PASSWORD"],
        ssl=True,
        verify_ssl=os.environ.get("HIK_VERIFY_SSL", "false").lower() == "true",
    )
    try:
        readers = await client.probe_readers()
        assert readers, "probe_readers returned an empty list"
        assert any(r.enabled for r in readers), "no enabled readers found"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_wait_for_one_real_event() -> None:
    """Open alertStream and wait up to 60s for a real AccessControllerEvent.

    To exercise the path: hold a card to the reader within 60 seconds of
    starting this test. If the device only emits heartbeats, this test
    times out — that's a hint that the configured reader is unused.
    """
    client = HikAccessClient(
        host=os.environ["HIK_HOST"],
        port=int(os.environ.get("HIK_PORT", "443")),
        username=os.environ["HIK_USERNAME"],
        password=os.environ["HIK_PASSWORD"],
        ssl=True,
        verify_ssl=os.environ.get("HIK_VERIFY_SSL", "false").lower() == "true",
    )

    async def wait_one() -> dict:
        async for evt in client.events():
            return evt
        raise AssertionError("event stream ended without yielding")

    try:
        evt = await asyncio.wait_for(wait_one(), timeout=60)
        assert evt["serial_no"] is not None
    finally:
        await client.stop()
