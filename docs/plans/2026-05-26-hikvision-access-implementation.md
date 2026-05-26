# Hikvision Access Control Integration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a HACS-distributable Home Assistant custom integration that streams `AccessControllerEvent`s from a Hikvision access controller, exposes per-reader/per-door entities, persists statistics across restarts via AcsEvent backfill, and offers HA device triggers for automations.

**Architecture:** Single asyncio `HikAccessClient` per controller, pluggable transport (alertStream in v1, WebSocket stub for v1.1). The pure-Python `api/` module has zero HA imports and is unit-testable standalone. HA-facing files consume parsed events through the HA dispatcher and never touch HTTP themselves. Door state is polled via `AcsWorkStatus`; event capture is push via alertStream. On startup, missed events are replayed from `/ISAPI/AccessControl/AcsEvent` and injected into HA's long-term statistics for exact hour buckets.

**Tech Stack:** Python 3.12+, aiohttp (ships with HA), pytest, `pytest-homeassistant-custom-component`, ruff, mypy. Target HA version: current and current-1.

**Reference:** See `docs/plans/2026-05-26-hikvision-access-design.md` for the full design rationale.

**Target device for live tests:** `https://192.0.2.1:443`, model `DS-K2702WX-E1(P)`, admin user. Credentials NOT in this plan — set them in `tests/live/.env` (gitignored) before running live smoke tests.

---

## Phase 1: Repo bootstrap

### Task 1.1: Initialize repo skeleton

**Files:**
- Create: `/Users/pstrawder/Developer/hikvision/.gitignore`
- Create: `/Users/pstrawder/Developer/hikvision/README.md`
- Create: `/Users/pstrawder/Developer/hikvision/hacs.json`
- Create: `/Users/pstrawder/Developer/hikvision/pyproject.toml`

**Step 1:** Create `.gitignore`:

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
*.egg-info/
dist/
build/
.coverage
htmlcov/
tests/live/.env
tests/live/snapshots/
```

**Step 2:** Create `hacs.json`:

```json
{
  "name": "Hikvision Access Control",
  "render_readme": true,
  "homeassistant": "2024.10.0",
  "zip_release": false,
  "content_in_root": false
}
```

**Step 3:** Create `pyproject.toml`:

```toml
[project]
name = "hikvision_access"
version = "0.1.0"
description = "Home Assistant integration for Hikvision access controllers via ISAPI alertStream."
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "pytest-aiohttp>=1.0",
  "pytest-homeassistant-custom-component>=0.13",
  "aiohttp>=3.9",
  "ruff>=0.5",
  "mypy>=1.10",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "ASYNC", "PT", "SIM", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["custom_components/hikvision_access/api"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 4:** Create `README.md` skeleton:

```markdown
# Hikvision Access Control for Home Assistant

HACS-distributable integration for Hikvision access controllers (DS-K2702WX, DS-K2604WX, …) via ISAPI.

Streams chip/card events in real time, exposes door / lock / tamper binary sensors, and provides daily / hourly usage statistics that survive HA restarts.

## Status

Pre-release. Tested against DS-K2702WX-E1(P) firmware V1.7.4.

## Installation

(populated after first release)

## Configuration

(populated after first release)
```

**Step 5:** Verify and commit:

```bash
cd /Users/pstrawder/Developer/hikvision
ls -la
git add .gitignore README.md hacs.json pyproject.toml
git commit -m "chore: initialize repo skeleton"
```

---

### Task 1.2: Create empty integration package

**Files:**
- Create: `custom_components/hikvision_access/__init__.py`
- Create: `custom_components/hikvision_access/manifest.json`
- Create: `custom_components/hikvision_access/const.py`
- Create: `custom_components/hikvision_access/api/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1:** Create directory structure:

```bash
cd /Users/pstrawder/Developer/hikvision
mkdir -p custom_components/hikvision_access/api custom_components/hikvision_access/translations tests/fixtures
touch tests/__init__.py
```

**Step 2:** Create `custom_components/hikvision_access/manifest.json`:

```json
{
  "domain": "hikvision_access",
  "name": "Hikvision Access Control",
  "version": "0.1.0",
  "config_flow": true,
  "documentation": "https://github.com/paulstrawder/hikvision_access",
  "issue_tracker": "https://github.com/paulstrawder/hikvision_access/issues",
  "codeowners": ["@paulstrawder"],
  "iot_class": "local_push",
  "integration_type": "device",
  "requirements": [],
  "dependencies": []
}
```

**Step 3:** Create `custom_components/hikvision_access/const.py`:

```python
"""Constants for the Hikvision Access Control integration."""

from __future__ import annotations

DOMAIN = "hikvision_access"

DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = False

CONF_VERIFY_SSL = "verify_ssl"

SIGNAL_EVENT = f"{DOMAIN}_event"
EVENT_BUS_NAME = f"{DOMAIN}_event"

ALERTSTREAM_PATH = "/ISAPI/Event/notification/alertStream"
DEVICE_INFO_PATH = "/ISAPI/System/deviceInfo"
ACS_WORK_STATUS_PATH = "/ISAPI/AccessControl/AcsWorkStatus?format=json"
ACS_EVENT_SEARCH_PATH = "/ISAPI/AccessControl/AcsEvent?format=json"
CARD_READER_CFG_PATH = "/ISAPI/AccessControl/CardReaderCfg/{slot}?format=json"

POLL_INTERVAL_SECONDS = 10
STREAM_STALLED_AFTER = 90
BACKOFF_INITIAL = 1
BACKOFF_MAX = 60
AUTH_FAIL_LIMIT = 2
```

**Step 4:** Create empty `custom_components/hikvision_access/__init__.py`:

```python
"""The Hikvision Access Control integration."""
```

**Step 5:** Create empty `custom_components/hikvision_access/api/__init__.py`:

```python
"""Pure-Python ISAPI client. No Home Assistant imports."""
```

**Step 6:** Create `tests/conftest.py`:

```python
"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR
```

**Step 7:** Commit:

```bash
git add custom_components/ tests/
git commit -m "chore: scaffold integration package and test harness"
```

---

## Phase 2: Pure-Python API layer (TDD)

### Task 2.1: MIME multipart parser — single complete chunk

**Files:**
- Create: `tests/fixtures/single_chunk.bin`
- Create: `tests/test_multipart.py`
- Create: `custom_components/hikvision_access/api/multipart.py`

**Step 1:** Save a known-good multipart fixture. Create `tests/fixtures/single_chunk.bin` with this byte content (use `Write` tool with the exact text below, no extra trailing newline — boundaries are CRLF-terminated):

```
--MIME_boundary
Content-Type: application/json; charset="UTF-8"
Content-Length: 99

{"eventType":"AccessControllerEvent","AccessControllerEvent":{"deviceName":"x","majorEventType":3}}
--MIME_boundary
```

(Replace each line ending with CRLF when writing. Use Python: `python3 -c "import pathlib; pathlib.Path('tests/fixtures/single_chunk.bin').write_bytes(b'--MIME_boundary\r\nContent-Type: application/json; charset=\"UTF-8\"\r\nContent-Length: 99\r\n\r\n{\"eventType\":\"AccessControllerEvent\",\"AccessControllerEvent\":{\"deviceName\":\"x\",\"majorEventType\":3}}\r\n--MIME_boundary\r\n')"`.)

**Step 2:** Create `tests/test_multipart.py`:

```python
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
```

**Step 3:** Run and verify failure:

```bash
cd /Users/pstrawder/Developer/hikvision
python3 -m pytest tests/test_multipart.py -v
```

Expected: `ModuleNotFoundError: No module named '…api.multipart'`.

**Step 4:** Create `custom_components/hikvision_access/api/multipart.py`:

```python
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
```

**Step 5:** Run to verify pass:

```bash
python3 -m pytest tests/test_multipart.py -v
```

Expected: PASS.

**Step 6:** Commit:

```bash
git add tests/fixtures/single_chunk.bin tests/test_multipart.py custom_components/hikvision_access/api/multipart.py
git commit -m "feat(api): streaming MIME multipart parser — single-chunk happy path"
```

---

### Task 2.2: Multipart parser — chunk split across feeds

**Files:**
- Modify: `tests/test_multipart.py`

**Step 1:** Append this test to `tests/test_multipart.py`:

```python
@pytest.mark.asyncio
async def test_parses_chunk_split_across_feeds(fixtures_dir) -> None:
    data = (fixtures_dir / "single_chunk.bin").read_bytes()
    parser = MultipartParser(boundary=b"MIME_boundary")

    # Split the buffer at an arbitrary byte; both halves should still produce 1 chunk overall.
    half = len(data) // 2
    chunks = []
    async for c in parser.feed(data[:half]):
        chunks.append(c)
    async for c in parser.feed(data[half:]):
        chunks.append(c)

    assert len(chunks) == 1
    assert chunks[0]["eventType"] == "AccessControllerEvent"
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_multipart.py -v
```

Expected: both tests PASS (parser already handles incremental feeds because we look up the boundary in the accumulated buffer).

**Step 3:** Commit:

```bash
git add tests/test_multipart.py
git commit -m "test(api): parser handles chunk split across feeds"
```

---

### Task 2.3: Multipart parser — drops malformed body, keeps stream alive

**Files:**
- Create: `tests/fixtures/mixed_good_bad.bin`
- Modify: `tests/test_multipart.py`

**Step 1:** Write fixture with one bad (non-JSON body) followed by one good chunk. Use:

```bash
python3 -c "import pathlib; pathlib.Path('tests/fixtures/mixed_good_bad.bin').write_bytes(b'--MIME_boundary\r\nContent-Type: application/xml\r\nContent-Length: 11\r\n\r\n<not-json/>\r\n--MIME_boundary\r\nContent-Type: application/json\r\nContent-Length: 18\r\n\r\n{\"eventType\":\"ok\"}\r\n--MIME_boundary\r\n')"
```

**Step 2:** Append to `tests/test_multipart.py`:

```python
@pytest.mark.asyncio
async def test_skips_non_json_body(fixtures_dir) -> None:
    data = (fixtures_dir / "mixed_good_bad.bin").read_bytes()
    parser = MultipartParser(boundary=b"MIME_boundary")

    chunks = [c async for c in parser.feed(data)]
    assert chunks == [{"eventType": "ok"}]
```

**Step 3:** Run:

```bash
python3 -m pytest tests/test_multipart.py -v
```

Expected: PASS (existing `try/except` in `feed` handles this).

**Step 4:** Commit:

```bash
git add tests/fixtures/mixed_good_bad.bin tests/test_multipart.py
git commit -m "test(api): parser skips non-JSON body without dropping subsequent good chunks"
```

---

### Task 2.4: Event decoder — normalize AccessControllerEvent shape

**Files:**
- Create: `tests/test_events.py`
- Create: `custom_components/hikvision_access/api/events.py`

**Step 1:** Create `tests/test_events.py`:

```python
"""Tests for AccessControllerEvent normalization."""

from __future__ import annotations

from custom_components.hikvision_access.api.events import normalize_event


def test_normalize_known_card_swipe():
    raw = {
        "eventType": "AccessControllerEvent",
        "dateTime": "2026-04-02T12:00:57+02:00",
        "AccessControllerEvent": {
            "deviceName": "Access Controller",
            "majorEventType": 3,
            "subEventType": 1,
            "serialNo": 42,
            "cardNo": "1234567890",
            "name": "Max Mustermann",
            "cardReaderNo": 1,
            "employeeNoString": "7",
        },
    }
    out = normalize_event(raw)
    assert out["card_no"] == "1234567890"
    assert out["name"] == "Max Mustermann"
    assert out["reader_no"] == 1
    assert out["serial_no"] == 42
    assert out["major"] == "event"
    assert out["minor"] == 1
    assert out["minor_label"] != ""
    assert out["minor_label"] != "unknown_1"
    assert out["timestamp"] == "2026-04-02T12:00:57+02:00"
    assert out["backfilled"] is False


def test_normalize_unknown_minor_falls_back():
    raw = {
        "eventType": "AccessControllerEvent",
        "AccessControllerEvent": {"majorEventType": 3, "subEventType": 999999, "serialNo": 1},
    }
    out = normalize_event(raw)
    assert out["minor_label"] == "unknown_999999"


def test_normalize_returns_none_for_non_access_event():
    assert normalize_event({"eventType": "videoloss"}) is None
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_events.py -v
```

Expected: `ModuleNotFoundError`.

**Step 3:** Create `custom_components/hikvision_access/api/events.py`:

```python
"""AccessControllerEvent decoding and normalization."""

from __future__ import annotations

from typing import Any

MAJOR_LABELS: dict[int, str] = {
    0: "alarm",
    1: "exception",
    2: "operation",
    3: "event",
    5: "ungrouped",
}

# Subset of the documented minorEvent codes for v1. Expanded over time.
# Source: AcsEvent/capabilities response + ISAPI PDF section 16.
MINOR_EVENT_LABELS: dict[int, str] = {
    1: "card_swiped_valid",
    6: "card_swiped_invalid_period",
    7: "card_swiped_invalid_password",
    8: "card_swiped_expired",
    9: "card_unregistered",
    10: "card_blocked",
    11: "card_swiped_at_invalid_door",
    12: "card_authentication_failed",
    21: "fingerprint_invalid",
    22: "fingerprint_valid",
    75: "door_opened_normally",
    76: "door_closed_normally",
    81: "door_held_open",
    82: "door_forced_open",
}

DENIAL_MINOR_CODES: frozenset[int] = frozenset({6, 7, 8, 9, 10, 11, 12, 21})
DOOR_FORCED_CODES: frozenset[int] = frozenset({82})
DOOR_HELD_OPEN_CODES: frozenset[int] = frozenset({81})


def normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw alertStream chunk to a normalized payload.

    Returns None for chunks that aren't AccessControllerEvents.
    """
    if raw.get("eventType") != "AccessControllerEvent":
        return None

    ace = raw.get("AccessControllerEvent", {})
    major = int(ace.get("majorEventType", 0))
    minor = int(ace.get("subEventType", 0))

    return {
        "device_id": None,  # filled in by client (needs deviceInfo serial)
        "controller": ace.get("deviceName", ""),
        "reader_no": ace.get("cardReaderNo"),
        "reader_name": None,  # filled by client via discovery cache
        "door_no": None,  # filled by client via reader→door map
        "card_no": ace.get("cardNo", ""),
        "employee_no": ace.get("employeeNoString", ""),
        "name": ace.get("name", ""),
        "major": MAJOR_LABELS.get(major, f"unknown_{major}"),
        "minor": minor,
        "minor_label": MINOR_EVENT_LABELS.get(minor, f"unknown_{minor}"),
        "serial_no": ace.get("serialNo"),
        "timestamp": raw.get("dateTime"),
        "backfilled": False,
    }
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_events.py -v
```

Expected: PASS.

**Step 5:** Commit:

```bash
git add tests/test_events.py custom_components/hikvision_access/api/events.py
git commit -m "feat(api): normalize AccessControllerEvent payloads"
```

---

### Task 2.5: Discovery — parse CardReaderCfg responses

**Files:**
- Create: `tests/fixtures/card_reader_1.json`
- Create: `tests/fixtures/card_reader_disabled.json`
- Create: `tests/fixtures/acs_work_status.json`
- Create: `tests/test_discovery.py`
- Create: `custom_components/hikvision_access/api/discovery.py`

**Step 1:** Capture real fixtures by writing them as files. Save the real response for slot 1 (enabled):

```bash
python3 -c "import pathlib; pathlib.Path('tests/fixtures/card_reader_1.json').write_text('''{\"CardReaderCfg\":{\"enable\":true,\"cardReaderName\":\"Eingang\",\"cardReaderDescription\":\"DS-K1105EDKB-QRbuild20251013\",\"QRCodeEnabled\":true}}''')"
python3 -c "import pathlib; pathlib.Path('tests/fixtures/card_reader_disabled.json').write_text('''{\"CardReaderCfg\":{\"enable\":false,\"cardReaderName\":\"\",\"cardReaderDescription\":\"Wiegand\\\\\\\\485Offline\"}}''')"
python3 -c "import json,pathlib; pathlib.Path('tests/fixtures/acs_work_status.json').write_text(json.dumps({'AcsWorkStatus':{'doorLockStatus':[0,0,0,0]+[0]*122,'doorStatus':[4,4,4,4]+[4]*122,'magneticStatus':[0,0,0,0]+[0]*122,'cardReaderOnlineStatus':[1],'powerSupplyStatus':'ACPowerSupply','hostAntiDismantleStatus':'close'}}))"
```

**Step 2:** Create `tests/test_discovery.py`:

```python
"""Tests for discovery parsers."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.hikvision_access.api.discovery import (
    parse_acs_work_status,
    parse_card_reader_cfg,
    reader_to_door,
)


def test_parse_enabled_reader(fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "card_reader_1.json").read_text())
    info = parse_card_reader_cfg(slot=1, raw=raw)
    assert info is not None
    assert info.slot == 1
    assert info.enabled is True
    assert info.name == "Eingang"


def test_parse_disabled_reader(fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "card_reader_disabled.json").read_text())
    info = parse_card_reader_cfg(slot=2, raw=raw)
    assert info is not None
    assert info.enabled is False


def test_reader_to_door_default_convention() -> None:
    assert reader_to_door(1) == 1
    assert reader_to_door(2) == 1
    assert reader_to_door(3) == 2
    assert reader_to_door(4) == 2


def test_parse_acs_work_status(fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "acs_work_status.json").read_text())
    status = parse_acs_work_status(raw, door_count=2, reader_count=1)
    assert status.door_lock[0] is False  # 0 = unlocked false in our convention
    assert status.door_open == [False, False]
    assert status.reader_online == [True]
    assert status.tamper is False
```

**Step 3:** Run:

```bash
python3 -m pytest tests/test_discovery.py -v
```

Expected: ModuleNotFoundError.

**Step 4:** Create `custom_components/hikvision_access/api/discovery.py`:

```python
"""ISAPI discovery — parsers and small static helpers (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReaderInfo:
    slot: int
    enabled: bool
    name: str
    description: str


@dataclass(frozen=True)
class WorkStatus:
    door_lock: list[bool]      # length == door_count
    door_open: list[bool]      # magneticStatus
    door_state: list[int]      # raw doorStatus codes
    reader_online: list[bool]  # length == reader_count
    tamper: bool
    power_ok: bool


def parse_card_reader_cfg(slot: int, raw: dict[str, Any]) -> ReaderInfo | None:
    cfg = raw.get("CardReaderCfg")
    if not isinstance(cfg, dict):
        return None
    return ReaderInfo(
        slot=slot,
        enabled=bool(cfg.get("enable", False)),
        name=str(cfg.get("cardReaderName") or ""),
        description=str(cfg.get("cardReaderDescription") or ""),
    )


def reader_to_door(reader_slot: int) -> int:
    """Hikvision convention: readers (2k-1, 2k) belong to door k."""
    return (reader_slot + 1) // 2


def parse_acs_work_status(raw: dict[str, Any], door_count: int, reader_count: int) -> WorkStatus:
    s = raw.get("AcsWorkStatus", {})
    door_lock_raw = (s.get("doorLockStatus") or [])[:door_count]
    door_mag_raw = (s.get("magneticStatus") or [])[:door_count]
    door_state_raw = (s.get("doorStatus") or [])[:door_count]
    reader_online_raw = (s.get("cardReaderOnlineStatus") or [])[:reader_count]

    return WorkStatus(
        door_lock=[bool(x) for x in door_lock_raw],
        door_open=[bool(x) for x in door_mag_raw],
        door_state=[int(x) for x in door_state_raw],
        reader_online=[bool(x) for x in reader_online_raw],
        tamper=str(s.get("hostAntiDismantleStatus", "close")) != "close",
        power_ok=str(s.get("powerSupplyStatus", "")) == "ACPowerSupply",
    )
```

**Step 5:** Run:

```bash
python3 -m pytest tests/test_discovery.py -v
```

Expected: PASS.

**Step 6:** Commit:

```bash
git add tests/fixtures/card_reader_1.json tests/fixtures/card_reader_disabled.json tests/fixtures/acs_work_status.json tests/test_discovery.py custom_components/hikvision_access/api/discovery.py
git commit -m "feat(api): discovery parsers for CardReaderCfg and AcsWorkStatus"
```

---

### Task 2.6: HikAccessClient — connect + stream against test server

**Files:**
- Create: `tests/test_client.py`
- Create: `custom_components/hikvision_access/api/transport.py`
- Modify: `custom_components/hikvision_access/api/__init__.py`

**Step 1:** Create `tests/test_client.py`:

```python
"""Integration tests for HikAccessClient against an aiohttp test server."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessClient


async def _alertstream_handler(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": 'multipart/mixed; boundary="MIME_boundary"'},
    )
    await resp.prepare(request)
    body = (
        b"--MIME_boundary\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 130\r\n\r\n"
        b'{"eventType":"AccessControllerEvent","dateTime":"2026-05-26T10:00:00Z",'
        b'"AccessControllerEvent":{"majorEventType":3,"subEventType":1,"serialNo":1}}'
        b"\r\n--MIME_boundary\r\n"
    )
    await resp.write(body)
    await asyncio.sleep(0.05)
    return resp


@pytest.mark.asyncio
async def test_client_streams_one_event(aiohttp_server) -> None:
    app = web.Application()
    app.router.add_get("/ISAPI/Event/notification/alertStream", _alertstream_handler)
    server = await aiohttp_server(app)

    received: list[dict[str, Any]] = []

    client = HikAccessClient(
        host=f"127.0.0.1",
        port=server.port,
        username="u",
        password="p",
        ssl=False,
        verify_ssl=False,
    )

    async def collect():
        async for evt in client.events():
            received.append(evt)
            if len(received) == 1:
                await client.stop()

    await asyncio.wait_for(collect(), timeout=2)
    assert received[0]["card_no"] == ""
    assert received[0]["serial_no"] == 1
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_client.py -v
```

Expected: ImportError or AttributeError on `HikAccessClient`.

**Step 3:** Create `custom_components/hikvision_access/api/transport.py`:

```python
"""Transport implementations for the ISAPI event stream."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .events import normalize_event
from .multipart import MultipartParser

_LOGGER = logging.getLogger(__name__)


class AlertStreamTransport:
    """Consume /ISAPI/Event/notification/alertStream as multipart HTTPS."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        auth: aiohttp.BasicAuth | None,
    ) -> None:
        self._session = session
        self._url = url
        self._auth = auth
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def stream(self) -> AsyncIterator[dict[str, Any]]:
        parser = MultipartParser(boundary=b"MIME_boundary")
        async with self._session.get(self._url, auth=self._auth) as resp:
            if resp.status == 401:
                raise PermissionError("alertStream returned 401")
            resp.raise_for_status()
            async for raw in resp.content.iter_any():
                if self._stop.is_set():
                    return
                async for chunk in parser.feed(raw):
                    norm = normalize_event(chunk)
                    if norm is not None:
                        yield norm
```

**Step 4:** Replace `custom_components/hikvision_access/api/__init__.py`:

```python
"""Pure-Python ISAPI client. No Home Assistant imports."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .transport import AlertStreamTransport

_LOGGER = logging.getLogger(__name__)


class HikAccessClient:
    """Single-controller ISAPI client."""

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
        self._auth = aiohttp.BasicAuth(username, password) if username else None
        scheme = "https" if ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"
        connector = aiohttp.TCPConnector(ssl=False if not verify_ssl else None)
        self._session = aiohttp.ClientSession(connector=connector)
        self._transport: AlertStreamTransport | None = None

    async def stop(self) -> None:
        if self._transport:
            await self._transport.stop()
        await self._session.close()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        self._transport = AlertStreamTransport(
            session=self._session,
            url=f"{self._base_url}/ISAPI/Event/notification/alertStream",
            auth=self._auth,
        )
        async for evt in self._transport.stream():
            yield evt
```

**Step 5:** Run:

```bash
python3 -m pytest tests/test_client.py -v
```

Expected: PASS.

**Step 6:** Commit:

```bash
git add tests/test_client.py custom_components/hikvision_access/api/transport.py custom_components/hikvision_access/api/__init__.py
git commit -m "feat(api): HikAccessClient + AlertStreamTransport against test server"
```

---

### Task 2.7: Reconnect with exponential backoff + degraded-state detector

**Files:**
- Modify: `tests/test_client.py`
- Modify: `custom_components/hikvision_access/api/transport.py`
- Modify: `custom_components/hikvision_access/api/__init__.py`

**Step 1:** Append to `tests/test_client.py`:

```python
@pytest.mark.asyncio
async def test_reconnects_after_server_drops(aiohttp_server) -> None:
    drops = 0

    async def flaky_handler(request: web.Request) -> web.StreamResponse:
        nonlocal drops
        resp = web.StreamResponse(
            status=200, headers={"Content-Type": 'multipart/mixed; boundary="MIME_boundary"'}
        )
        await resp.prepare(request)
        if drops == 0:
            drops += 1
            return resp  # drop immediately first time
        body = (
            b"--MIME_boundary\r\nContent-Type: application/json\r\nContent-Length: 110\r\n\r\n"
            b'{"eventType":"AccessControllerEvent","AccessControllerEvent":{"majorEventType":3,"subEventType":1,"serialNo":2}}'
            b"\r\n--MIME_boundary\r\n"
        )
        await resp.write(body)
        await asyncio.sleep(0.05)
        return resp

    app = web.Application()
    app.router.add_get("/ISAPI/Event/notification/alertStream", flaky_handler)
    server = await aiohttp_server(app)

    received = []
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    client._initial_backoff = 0.01  # speed test up

    async def collect():
        async for evt in client.events():
            received.append(evt)
            await client.stop()
            break

    await asyncio.wait_for(collect(), timeout=3)
    assert received[0]["serial_no"] == 2
```

**Step 2:** Run to confirm failure:

```bash
python3 -m pytest tests/test_client.py::test_reconnects_after_server_drops -v
```

Expected: fails (no reconnect logic yet).

**Step 3:** Modify `HikAccessClient.events` in `custom_components/hikvision_access/api/__init__.py` to wrap the transport in a reconnect loop:

```python
"""Pure-Python ISAPI client. No Home Assistant imports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from .transport import AlertStreamTransport

_LOGGER = logging.getLogger(__name__)

_BACKOFF_MAX = 60


class HikAccessClient:
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
        self._auth = aiohttp.BasicAuth(username, password) if username else None
        scheme = "https" if ssl else "http"
        self._base_url = f"{scheme}://{host}:{port}"
        connector = aiohttp.TCPConnector(ssl=False if not verify_ssl else None)
        self._session = aiohttp.ClientSession(connector=connector)
        self._transport: AlertStreamTransport | None = None
        self._stop = asyncio.Event()
        self._initial_backoff = 1.0
        self._auth_fail_count = 0

    async def stop(self) -> None:
        self._stop.set()
        if self._transport:
            await self._transport.stop()
        await self._session.close()

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        backoff = self._initial_backoff
        while not self._stop.is_set():
            self._transport = AlertStreamTransport(
                session=self._session,
                url=f"{self._base_url}/ISAPI/Event/notification/alertStream",
                auth=self._auth,
            )
            try:
                async for evt in self._transport.stream():
                    backoff = self._initial_backoff
                    self._auth_fail_count = 0
                    yield evt
            except PermissionError:
                self._auth_fail_count += 1
                if self._auth_fail_count >= 2:
                    raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("transport error, will retry: %s", err)

            if self._stop.is_set():
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_client.py -v
```

Expected: both tests PASS.

**Step 5:** Commit:

```bash
git add tests/test_client.py custom_components/hikvision_access/api/__init__.py
git commit -m "feat(api): reconnect with exponential backoff; surface 2x401 as PermissionError"
```

---

### Task 2.8: HTTP helpers — deviceInfo, CardReaderCfg, AcsWorkStatus, AcsEvent

**Files:**
- Create: `tests/test_http.py`
- Create: `custom_components/hikvision_access/api/http.py`
- Modify: `custom_components/hikvision_access/api/__init__.py`

**Step 1:** Create `tests/test_http.py`:

```python
"""Tests for ISAPI request helpers (excluding alertStream)."""

from __future__ import annotations

import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessClient


@pytest.mark.asyncio
async def test_get_device_info(aiohttp_server) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(
            text="""<?xml version="1.0"?>
<DeviceInfo><deviceName>X</deviceName><model>DS-K2702WX-E1(P)</model>
<serialNumber>abc123</serialNumber><macAddress>00:11:22:33:44:55</macAddress>
<firmwareVersion>V1.7.4</firmwareVersion></DeviceInfo>""",
            content_type="application/xml",
        )

    app = web.Application()
    app.router.add_get("/ISAPI/System/deviceInfo", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    info = await client.get_device_info()
    assert info["model"] == "DS-K2702WX-E1(P)"
    assert info["serial_number"] == "abc123"
    await client.stop()


@pytest.mark.asyncio
async def test_probe_readers_stops_at_notsupport(aiohttp_server) -> None:
    async def handler(request: web.Request) -> web.Response:
        slot = request.match_info["slot"]
        if slot in {"1", "2"}:
            return web.json_response({"CardReaderCfg": {"enable": slot == "1", "cardReaderName": f"R{slot}"}})
        return web.json_response({"statusCode": 4, "subStatusCode": "notSupport"})

    app = web.Application()
    app.router.add_get("/ISAPI/AccessControl/CardReaderCfg/{slot}", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    readers = await client.probe_readers(max_slots=8)
    assert [r.slot for r in readers] == [1, 2]
    await client.stop()
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_http.py -v
```

Expected: AttributeError on `get_device_info` / `probe_readers`.

**Step 3:** Create `custom_components/hikvision_access/api/http.py`:

```python
"""ISAPI helpers that aren't long-lived streams."""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import fromstring

import aiohttp

from .discovery import ReaderInfo, parse_card_reader_cfg


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


async def fetch_device_info(session: aiohttp.ClientSession, base: str, auth: aiohttp.BasicAuth | None) -> dict[str, str]:
    async with session.get(f"{base}/ISAPI/System/deviceInfo", auth=auth) as r:
        r.raise_for_status()
        body = await r.text()
    root = fromstring(body)
    out: dict[str, str] = {}
    for child in root:
        out[_camel_to_snake(_strip_ns(child.tag))] = (child.text or "").strip()
    return out


def _camel_to_snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


async def probe_card_readers(
    session: aiohttp.ClientSession,
    base: str,
    auth: aiohttp.BasicAuth | None,
    max_slots: int,
) -> list[ReaderInfo]:
    readers: list[ReaderInfo] = []
    for slot in range(1, max_slots + 1):
        url = f"{base}/ISAPI/AccessControl/CardReaderCfg/{slot}?format=json"
        async with session.get(url, auth=auth) as r:
            data = await r.json(content_type=None)
        if isinstance(data, dict) and data.get("subStatusCode") == "notSupport":
            break
        info = parse_card_reader_cfg(slot, data)
        if info is None:
            break
        readers.append(info)
    return readers


async def fetch_acs_work_status(
    session: aiohttp.ClientSession, base: str, auth: aiohttp.BasicAuth | None
) -> dict[str, Any]:
    async with session.get(f"{base}/ISAPI/AccessControl/AcsWorkStatus?format=json", auth=auth) as r:
        r.raise_for_status()
        return await r.json(content_type=None)
```

**Step 4:** Add the two methods to `HikAccessClient` (modify `api/__init__.py`):

```python
# Add inside the class, after events():

    async def get_device_info(self) -> dict[str, str]:
        from .http import fetch_device_info
        return await fetch_device_info(self._session, self._base_url, self._auth)

    async def probe_readers(self, max_slots: int = 8):
        from .http import probe_card_readers
        return await probe_card_readers(self._session, self._base_url, self._auth, max_slots)

    async def get_acs_work_status(self):
        from .http import fetch_acs_work_status
        return await fetch_acs_work_status(self._session, self._base_url, self._auth)
```

**Step 5:** Run:

```bash
python3 -m pytest tests/test_http.py -v
```

Expected: PASS.

**Step 6:** Commit:

```bash
git add tests/test_http.py custom_components/hikvision_access/api/http.py custom_components/hikvision_access/api/__init__.py
git commit -m "feat(api): deviceInfo / CardReaderCfg / AcsWorkStatus helpers"
```

---

### Task 2.9: AcsEvent backfill — search and replay

**Files:**
- Create: `tests/fixtures/acs_event_page.json`
- Create: `tests/test_backfill.py`
- Create: `custom_components/hikvision_access/api/backfill.py`

**Step 1:** Create fixture:

```bash
python3 -c "import json,pathlib; pathlib.Path('tests/fixtures/acs_event_page.json').write_text(json.dumps({'AcsEvent':{'totalMatches':2,'responseStatusStrg':'OK','numOfMatches':2,'InfoList':[{'time':'2026-05-26T08:00:00+02:00','serialNo':100,'major':3,'minor':1,'cardNo':'A','name':'Alice','cardReaderNo':1},{'time':'2026-05-26T08:01:00+02:00','serialNo':101,'major':3,'minor':1,'cardNo':'B','name':'Bob','cardReaderNo':1}]}}))"
```

**Step 2:** Create `tests/test_backfill.py`:

```python
"""Tests for AcsEvent backfill replay."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.hikvision_access.api.backfill import replay_page


def test_replay_yields_normalized_with_backfilled_flag(fixtures_dir: Path) -> None:
    page = json.loads((fixtures_dir / "acs_event_page.json").read_text())
    events = list(replay_page(page))
    assert len(events) == 2
    assert events[0]["serial_no"] == 100
    assert events[0]["name"] == "Alice"
    assert events[0]["backfilled"] is True


def test_replay_dedups_by_serial(fixtures_dir: Path) -> None:
    page = json.loads((fixtures_dir / "acs_event_page.json").read_text())
    events = list(replay_page(page, already_seen={100}))
    assert [e["serial_no"] for e in events] == [101]
```

**Step 3:** Run:

```bash
python3 -m pytest tests/test_backfill.py -v
```

Expected: ImportError.

**Step 4:** Create `custom_components/hikvision_access/api/backfill.py`:

```python
"""AcsEvent backfill — replay historical events the controller buffered while HA was down."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from .events import MAJOR_LABELS, MINOR_EVENT_LABELS


def replay_page(page: dict[str, Any], already_seen: Iterable[int] = ()) -> Iterator[dict[str, Any]]:
    """Yield normalized events from an AcsEvent search page, skipping seen serials."""
    seen = set(already_seen)
    info_list = page.get("AcsEvent", {}).get("InfoList", [])
    for item in info_list:
        serial = item.get("serialNo")
        if serial in seen:
            continue
        major = int(item.get("major", 0))
        minor = int(item.get("minor", 0))
        yield {
            "device_id": None,
            "controller": "",
            "reader_no": item.get("cardReaderNo"),
            "reader_name": None,
            "door_no": None,
            "card_no": item.get("cardNo", ""),
            "employee_no": item.get("employeeNoString", ""),
            "name": item.get("name", ""),
            "major": MAJOR_LABELS.get(major, f"unknown_{major}"),
            "minor": minor,
            "minor_label": MINOR_EVENT_LABELS.get(minor, f"unknown_{minor}"),
            "serial_no": serial,
            "timestamp": item.get("time"),
            "backfilled": True,
        }
```

**Step 5:** Run:

```bash
python3 -m pytest tests/test_backfill.py -v
```

Expected: PASS.

**Step 6:** Commit:

```bash
git add tests/fixtures/acs_event_page.json tests/test_backfill.py custom_components/hikvision_access/api/backfill.py
git commit -m "feat(api): AcsEvent page replay with serial dedup"
```

---

### Task 2.10: Backfill — fetch wrapper with pagination

**Files:**
- Modify: `tests/test_backfill.py`
- Modify: `custom_components/hikvision_access/api/backfill.py`
- Modify: `custom_components/hikvision_access/api/__init__.py`

**Step 1:** Append to `tests/test_backfill.py`:

```python
import pytest
from aiohttp import web

from custom_components.hikvision_access.api import HikAccessClient


@pytest.mark.asyncio
async def test_backfill_paginates_until_empty(aiohttp_server) -> None:
    pages = [
        {"AcsEvent": {"numOfMatches": 1, "totalMatches": 2, "InfoList": [{"serialNo": 1, "major": 3, "minor": 1, "time": "t"}]}},
        {"AcsEvent": {"numOfMatches": 1, "totalMatches": 2, "InfoList": [{"serialNo": 2, "major": 3, "minor": 1, "time": "t"}]}},
        {"AcsEvent": {"numOfMatches": 0, "totalMatches": 2, "InfoList": []}},
    ]
    counter = {"i": 0}

    async def handler(request: web.Request) -> web.Response:
        page = pages[counter["i"]]
        counter["i"] += 1
        return web.json_response(page)

    app = web.Application()
    app.router.add_post("/ISAPI/AccessControl/AcsEvent", handler)
    server = await aiohttp_server(app)
    client = HikAccessClient(
        host="127.0.0.1", port=server.port, username="u", password="p", ssl=False, verify_ssl=False
    )
    events = []
    async for e in client.backfill(start_time="2026-01-01T00:00:00Z", end_time="2026-12-31T00:00:00Z"):
        events.append(e)
    assert [e["serial_no"] for e in events] == [1, 2]
    await client.stop()
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_backfill.py -v
```

Expected: AttributeError on `client.backfill`.

**Step 3:** Add to `api/backfill.py`:

```python
import aiohttp


async def fetch_pages(
    session: aiohttp.ClientSession,
    base: str,
    auth: aiohttp.BasicAuth | None,
    start_time: str,
    end_time: str,
    page_size: int = 30,
):
    """Yield raw AcsEvent pages until the device returns an empty InfoList."""
    position = 0
    search_id = "ha-backfill"
    while True:
        body = {
            "AcsEventCond": {
                "searchID": search_id,
                "searchResultPosition": position,
                "maxResults": page_size,
                "startTime": start_time,
                "endTime": end_time,
            }
        }
        async with session.post(
            f"{base}/ISAPI/AccessControl/AcsEvent?format=json",
            json=body,
            auth=auth,
        ) as r:
            r.raise_for_status()
            page = await r.json(content_type=None)
        info_list = page.get("AcsEvent", {}).get("InfoList", [])
        if not info_list:
            return
        yield page
        position += len(info_list)
```

**Step 4:** Add `backfill` to `HikAccessClient` (`api/__init__.py`):

```python
    async def backfill(self, start_time: str, end_time: str, already_seen=()):
        from .backfill import fetch_pages, replay_page
        seen = set(already_seen)
        async for page in fetch_pages(self._session, self._base_url, self._auth, start_time, end_time):
            for evt in replay_page(page, seen):
                seen.add(evt["serial_no"])
                yield evt
```

**Step 5:** Run:

```bash
python3 -m pytest tests/test_backfill.py -v
```

Expected: PASS.

**Step 6:** Commit:

```bash
git add tests/test_backfill.py custom_components/hikvision_access/api/backfill.py custom_components/hikvision_access/api/__init__.py
git commit -m "feat(api): paginate AcsEvent backfill and dedup via client"
```

---

## Phase 3: HA integration scaffolding

### Task 3.1: Config flow — host/port/credentials

**Files:**
- Create: `tests/test_config_flow.py`
- Create: `custom_components/hikvision_access/config_flow.py`
- Modify: `custom_components/hikvision_access/manifest.json` (no change but verify `config_flow: true`)
- Create: `custom_components/hikvision_access/strings.json`
- Create: `custom_components/hikvision_access/translations/en.json`

**Step 1:** Create `tests/test_config_flow.py`:

```python
"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


@pytest.mark.asyncio
async def test_user_flow_happy_path(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.hikvision_access.config_flow.HikAccessClient",
    ) as client_cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "abc", "model": "DS-K2702WX-E1(P)"})
        client.stop = AsyncMock()
        client_cls.return_value = client

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert result["type"] == "form"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.0.1",
                CONF_PORT: 443,
                CONF_SSL: True,
                CONF_VERIFY_SSL: False,
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "pw",
            },
        )
        assert result2["type"] == "create_entry"
        assert result2["data"][CONF_HOST] == "192.168.0.1"
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_config_flow.py -v
```

Expected: ModuleNotFoundError.

**Step 3:** Create `custom_components/hikvision_access/config_flow.py`:

```python
"""Config flow for Hikvision Access Control."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME

from .api import HikAccessClient
from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DEFAULT_VERIFY_SSL, DOMAIN

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_SSL, default=True): bool,
        vol.Required(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class HikAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = HikAccessClient(
                host=user_input[CONF_HOST],
                port=user_input[CONF_PORT],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                ssl=user_input[CONF_SSL],
                verify_ssl=user_input[CONF_VERIFY_SSL],
            )
            try:
                info = await client.get_device_info()
            except PermissionError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                serial = info.get("serial_number", user_input[CONF_HOST])
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info.get("model", "Hikvision"), data=user_input)
            finally:
                await client.stop()

        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)
```

**Step 4:** Create `custom_components/hikvision_access/strings.json`:

```json
{
  "config": {
    "step": {
      "user": {
        "data": {
          "host": "Host",
          "port": "Port",
          "ssl": "Use HTTPS",
          "verify_ssl": "Verify SSL certificate",
          "username": "Username",
          "password": "Password"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid authentication",
      "cannot_connect": "Cannot connect"
    },
    "abort": { "already_configured": "Device already configured" }
  }
}
```

**Step 5:** Copy to `translations/en.json`:

```bash
cp custom_components/hikvision_access/strings.json custom_components/hikvision_access/translations/en.json
```

**Step 6:** Run:

```bash
python3 -m pytest tests/test_config_flow.py -v
```

Expected: PASS.

**Step 7:** Commit:

```bash
git add tests/test_config_flow.py custom_components/hikvision_access/config_flow.py custom_components/hikvision_access/strings.json custom_components/hikvision_access/translations/en.json
git commit -m "feat: config flow with host/port/credentials and device probe"
```

---

### Task 3.2: async_setup_entry / async_unload_entry skeleton

**Files:**
- Create: `tests/test_setup.py`
- Modify: `custom_components/hikvision_access/__init__.py`

**Step 1:** Create `tests/test_setup.py`:

```python
"""Tests for async_setup_entry / async_unload_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


@pytest.mark.asyncio
async def test_setup_and_unload(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "h",
            CONF_PORT: 443,
            CONF_SSL: True,
            CONF_VERIFY_SSL: False,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "pw",
        },
        unique_id="serial-1",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "serial-1", "model": "DS-K2702WX-E1(P)"})
        client.probe_readers = AsyncMock(return_value=[])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        client.backfill = MagicMock(return_value=AsyncMock())
        client.events = MagicMock(return_value=AsyncMock())
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state.recoverable

        assert await hass.config_entries.async_unload(entry.entry_id)
        client.stop.assert_awaited()
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_setup.py -v
```

Expected: ModuleNotFoundError or AttributeError.

**Step 3:** Replace `custom_components/hikvision_access/__init__.py`:

```python
"""The Hikvision Access Control integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import HikAccessClient
from .const import CONF_VERIFY_SSL, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


@dataclass
class HikAccessData:
    client: HikAccessClient
    device_info: dict[str, str]
    readers: list = field(default_factory=list)
    stream_task: asyncio.Task | None = None


type HikAccessConfigEntry = ConfigEntry[HikAccessData]


async def async_setup_entry(hass: HomeAssistant, entry: HikAccessConfigEntry) -> bool:
    d = entry.data
    client = HikAccessClient(
        host=d[CONF_HOST],
        port=d[CONF_PORT],
        username=d[CONF_USERNAME],
        password=d[CONF_PASSWORD],
        ssl=d[CONF_SSL],
        verify_ssl=d[CONF_VERIFY_SSL],
    )
    try:
        info = await client.get_device_info()
        readers = await client.probe_readers()
    except PermissionError as err:
        await client.stop()
        raise ConfigEntryAuthFailed from err
    except Exception as err:  # noqa: BLE001
        await client.stop()
        raise ConfigEntryNotReady(f"Cannot connect to {d[CONF_HOST]}: {err}") from err

    entry.runtime_data = HikAccessData(client=client, device_info=info, readers=readers)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikAccessConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    data = entry.runtime_data
    if data.stream_task:
        data.stream_task.cancel()
    await data.client.stop()
    return True
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_setup.py -v
```

Expected: PASS (it will be lenient since we have no platforms yet — they're forwarded but produce no entities).

**Step 5:** Commit:

```bash
git add tests/test_setup.py custom_components/hikvision_access/__init__.py
git commit -m "feat: async_setup_entry probes device and stores runtime data"
```

---

### Task 3.3: AcsWorkStatusCoordinator

**Files:**
- Create: `custom_components/hikvision_access/coordinator.py`
- Modify: `custom_components/hikvision_access/__init__.py` (instantiate coordinator)

**Step 1:** Create `custom_components/hikvision_access/coordinator.py`:

```python
"""DataUpdateCoordinator for AcsWorkStatus polling."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HikAccessClient
from .api.discovery import WorkStatus, parse_acs_work_status
from .const import DOMAIN, POLL_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class AcsWorkStatusCoordinator(DataUpdateCoordinator[WorkStatus]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: HikAccessClient,
        door_count: int,
        reader_count: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_acs_work_status",
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        )
        self._client = client
        self._door_count = door_count
        self._reader_count = reader_count

    async def _async_update_data(self) -> WorkStatus:
        try:
            raw = await self._client.get_acs_work_status()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
        return parse_acs_work_status(raw, self._door_count, self._reader_count)
```

**Step 2:** Modify `__init__.py` `async_setup_entry`. After `entry.runtime_data = …`, add:

```python
    from .coordinator import AcsWorkStatusCoordinator

    reader_count = len([r for r in readers if r.enabled])
    door_count = max(1, (len(readers) + 1) // 2)
    coordinator = AcsWorkStatusCoordinator(hass, client, door_count, max(reader_count, 1))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data.coordinator = coordinator
```

Update the dataclass:

```python
@dataclass
class HikAccessData:
    client: HikAccessClient
    device_info: dict[str, str]
    readers: list = field(default_factory=list)
    stream_task: asyncio.Task | None = None
    coordinator: object | None = None  # set after first refresh
    door_count: int = 1
```

**Step 3:** Run:

```bash
python3 -m pytest tests/test_setup.py -v
```

Expected: PASS (the mock returns an empty AcsWorkStatus dict which parses cleanly to zeros).

**Step 4:** Commit:

```bash
git add custom_components/hikvision_access/coordinator.py custom_components/hikvision_access/__init__.py
git commit -m "feat: AcsWorkStatus polling coordinator"
```

---

## Phase 4: Entities

### Task 4.1: Sensor — last event per reader

**Files:**
- Create: `tests/test_sensor.py`
- Create: `custom_components/hikvision_access/sensor.py`

**Step 1:** Create `tests/test_sensor.py`:

```python
"""Tests for sensor entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN, SIGNAL_EVENT


@pytest.mark.asyncio
async def test_last_event_sensor_updates_on_dispatch(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False, CONF_USERNAME: "admin", CONF_PASSWORD: "pw"},
        unique_id="serial-1",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "serial-1", "model": "DS-K2702WX-E1(P)"})
        client.probe_readers = AsyncMock(return_value=[ReaderInfo(slot=1, enabled=True, name="Eingang", description="")])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_last_event")
    assert state is not None
    assert state.state == "unknown"

    async_dispatcher_send(
        hass,
        SIGNAL_EVENT,
        {
            "controller": "C", "reader_no": 1, "reader_name": "Eingang", "door_no": 1,
            "card_no": "X", "name": "Alice", "minor_label": "card_swiped_valid",
            "serial_no": 1, "timestamp": "2026-05-26T10:00:00Z", "backfilled": False,
            "major": "event", "minor": 1, "employee_no": "",
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get("sensor.hikvision_eingang_last_event")
    assert state.state == "Alice"
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_sensor.py -v
```

Expected: failure (entity not registered).

**Step 3:** Create `custom_components/hikvision_access/sensor.py`:

```python
"""Sensor entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikAccessConfigEntry
from .const import SIGNAL_EVENT


async def async_setup_entry(
    hass: HomeAssistant, entry: HikAccessConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = entry.runtime_data
    entities: list[SensorEntity] = []
    for r in data.readers:
        if r.enabled:
            entities.append(LastEventSensor(entry, reader_slot=r.slot, reader_name=r.name))
    async_add_entities(entities)


class LastEventSensor(SensorEntity):
    _attr_has_entity_name = False
    _attr_should_poll = False

    def __init__(self, entry: HikAccessConfigEntry, reader_slot: int, reader_name: str) -> None:
        self._entry = entry
        self._reader_slot = reader_slot
        self._reader_name = reader_name
        self._attr_unique_id = f"{entry.unique_id}_reader_{reader_slot}_last_event"
        slug = reader_name.lower().replace(" ", "_") or f"reader{reader_slot}"
        self._attr_name = f"Hikvision {reader_name} Last Event"
        self.entity_id = f"sensor.hikvision_{slug}_last_event"
        self._attr_native_value = None
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_EVENT, self._on_event))

    @callback
    def _on_event(self, payload: dict[str, Any]) -> None:
        if payload.get("reader_no") != self._reader_slot:
            return
        self._attr_native_value = payload.get("name") or payload.get("card_no") or "unknown"
        self._attr_extra_state_attributes = dict(payload)
        self.async_write_ha_state()
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_sensor.py -v
```

Expected: PASS.

**Step 5:** Commit:

```bash
git add tests/test_sensor.py custom_components/hikvision_access/sensor.py
git commit -m "feat(sensor): last-event sensor per enabled reader"
```

---

### Task 4.2: Sensor — total_swipes (RestoreEntity, total_increasing)

**Files:**
- Modify: `tests/test_sensor.py`
- Modify: `custom_components/hikvision_access/sensor.py`

**Step 1:** Append to `tests/test_sensor.py`:

```python
@pytest.mark.asyncio
async def test_total_swipes_increments(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False, CONF_USERNAME: "admin", CONF_PASSWORD: "pw"},
        unique_id="serial-2",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "serial-2", "model": "M"})
        client.probe_readers = AsyncMock(return_value=[ReaderInfo(slot=1, enabled=True, name="Eingang", description="")])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for serial in (1, 2, 3):
        async_dispatcher_send(
            hass, SIGNAL_EVENT,
            {"reader_no": 1, "name": "n", "card_no": "c", "serial_no": serial,
             "minor_label": "card_swiped_valid", "minor": 1, "major": "event",
             "controller": "", "reader_name": "Eingang", "door_no": 1,
             "timestamp": "t", "backfilled": False, "employee_no": ""},
        )
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_total_swipes")
    assert int(float(state.state)) == 3
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_sensor.py::test_total_swipes_increments -v
```

Expected: AssertionError (entity not yet created).

**Step 3:** Modify `sensor.py` — add `TotalSwipesSensor` class and add it in `async_setup_entry` alongside `LastEventSensor`:

```python
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.restore_state import RestoreEntity


class TotalSwipesSensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "swipes"

    def __init__(self, entry: HikAccessConfigEntry, reader_slot: int, reader_name: str) -> None:
        self._entry = entry
        self._reader_slot = reader_slot
        slug = reader_name.lower().replace(" ", "_") or f"reader{reader_slot}"
        self._attr_unique_id = f"{entry.unique_id}_reader_{reader_slot}_total_swipes"
        self._attr_name = f"Hikvision {reader_name} Total Swipes"
        self.entity_id = f"sensor.hikvision_{slug}_total_swipes"
        self._count = 0

    @property
    def native_value(self) -> int:
        return self._count

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable"):
            try:
                self._count = int(float(last.state))
            except ValueError:
                self._count = 0
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_EVENT, self._on_event))

    @callback
    def _on_event(self, payload: dict[str, Any]) -> None:
        if payload.get("reader_no") != self._reader_slot:
            return
        # only count valid card reads — denials etc. don't increment "swipes"
        if not payload.get("card_no"):
            return
        self._count += 1
        self.async_write_ha_state()
```

Add to setup:

```python
        for r in data.readers:
            if r.enabled:
                entities.append(LastEventSensor(entry, reader_slot=r.slot, reader_name=r.name))
                entities.append(TotalSwipesSensor(entry, reader_slot=r.slot, reader_name=r.name))
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_sensor.py -v
```

Expected: PASS.

**Step 5:** Commit:

```bash
git add tests/test_sensor.py custom_components/hikvision_access/sensor.py
git commit -m "feat(sensor): total_swipes per reader (total_increasing + RestoreEntity)"
```

---

### Task 4.3: Sensor — unique_visitors_today

**Files:**
- Modify: `tests/test_sensor.py`
- Modify: `custom_components/hikvision_access/sensor.py`

**Step 1:** Append to `tests/test_sensor.py`:

```python
@pytest.mark.asyncio
async def test_unique_visitors_dedups(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False, CONF_USERNAME: "admin", CONF_PASSWORD: "pw"},
        unique_id="serial-3",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "serial-3", "model": "M"})
        client.probe_readers = AsyncMock(return_value=[ReaderInfo(slot=1, enabled=True, name="Eingang", description="")])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for card in ("A", "A", "B"):
        async_dispatcher_send(
            hass, SIGNAL_EVENT,
            {"reader_no": 1, "card_no": card, "name": "x", "serial_no": 1, "minor_label": "card_swiped_valid", "minor": 1, "major": "event", "controller": "", "reader_name": "Eingang", "door_no": 1, "timestamp": "t", "backfilled": False, "employee_no": ""},
        )
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hikvision_eingang_unique_visitors_today")
    assert int(state.state) == 2
    assert set(state.attributes.get("cardholders", [])) == {"x"}
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_sensor.py::test_unique_visitors_dedups -v
```

Expected: fails (entity missing).

**Step 3:** Add to `sensor.py`:

```python
from datetime import datetime, time
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util


class UniqueVisitorsTodaySensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: HikAccessConfigEntry, reader_slot: int, reader_name: str) -> None:
        self._entry = entry
        self._reader_slot = reader_slot
        slug = reader_name.lower().replace(" ", "_") or f"reader{reader_slot}"
        self._attr_unique_id = f"{entry.unique_id}_reader_{reader_slot}_unique_today"
        self._attr_name = f"Hikvision {reader_name} Unique Visitors Today"
        self.entity_id = f"sensor.hikvision_{slug}_unique_visitors_today"
        self._cards: set[str] = set()
        self._names: set[str] = set()
        self._last_reset = dt_util.start_of_local_day()

    @property
    def native_value(self) -> int:
        return len(self._cards)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"cardholders": sorted(self._names), "last_reset": self._last_reset.isoformat()}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        today = dt_util.start_of_local_day()
        if last and last.attributes.get("last_reset"):
            try:
                lr = dt_util.parse_datetime(last.attributes["last_reset"])
            except (TypeError, ValueError):
                lr = today
            if lr and lr == today:
                self._last_reset = lr
                self._cards = set(last.attributes.get("card_set", []))
                self._names = set(last.attributes.get("cardholders", []))
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_EVENT, self._on_event))
        self.async_on_remove(async_track_time_change(self.hass, self._reset_at_midnight, hour=0, minute=0, second=0))

    @callback
    def _on_event(self, payload: dict[str, Any]) -> None:
        if payload.get("reader_no") != self._reader_slot:
            return
        card = payload.get("card_no")
        if not card:
            return
        if len(self._cards) >= 5000:
            return
        self._cards.add(card)
        if payload.get("name"):
            self._names.add(payload["name"])
        self.async_write_ha_state()

    @callback
    def _reset_at_midnight(self, _now: datetime) -> None:
        self._cards.clear()
        self._names.clear()
        self._last_reset = dt_util.start_of_local_day()
        self.async_write_ha_state()
```

Add to setup loop:

```python
        for r in data.readers:
            if r.enabled:
                entities.append(LastEventSensor(entry, r.slot, r.name))
                entities.append(TotalSwipesSensor(entry, r.slot, r.name))
                entities.append(UniqueVisitorsTodaySensor(entry, r.slot, r.name))
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_sensor.py -v
```

Expected: PASS.

**Step 5:** Commit:

```bash
git add tests/test_sensor.py custom_components/hikvision_access/sensor.py
git commit -m "feat(sensor): unique_visitors_today with set dedup, midnight reset, restore"
```

---

### Task 4.4: Binary sensors — door open / lock / online / tamper

**Files:**
- Create: `tests/test_binary_sensor.py`
- Create: `custom_components/hikvision_access/binary_sensor.py`

**Step 1:** Create `tests/test_binary_sensor.py`:

```python
"""Tests for binary sensor entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN


@pytest.mark.asyncio
async def test_door_and_reader_entities_created(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False, CONF_USERNAME: "admin", CONF_PASSWORD: "pw"},
        unique_id="serial-bs",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "serial-bs", "model": "DS-K2702WX-E1(P)"})
        client.probe_readers = AsyncMock(return_value=[
            ReaderInfo(slot=1, enabled=True, name="Eingang", description=""),
            ReaderInfo(slot=2, enabled=False, name="", description=""),
        ])
        client.get_acs_work_status = AsyncMock(return_value={
            "AcsWorkStatus": {"doorLockStatus": [0], "magneticStatus": [0], "doorStatus": [4], "cardReaderOnlineStatus": [1], "hostAntiDismantleStatus": "close"}
        })
        cls.return_value = client

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.hikvision_door_1_open") is not None
    assert hass.states.get("binary_sensor.hikvision_door_1_lock") is not None
    assert hass.states.get("binary_sensor.hikvision_eingang_online") is not None
    assert hass.states.get("binary_sensor.hikvision_controller_tamper") is not None
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_binary_sensor.py -v
```

Expected: AssertionError or platform missing.

**Step 3:** Create `custom_components/hikvision_access/binary_sensor.py`:

```python
"""Binary sensor entities backed by AcsWorkStatusCoordinator."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikAccessConfigEntry
from .api.discovery import WorkStatus
from .coordinator import AcsWorkStatusCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: HikAccessConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = entry.runtime_data
    coordinator: AcsWorkStatusCoordinator = data.coordinator
    door_count = data.door_count or 1
    entities: list[BinarySensorEntity] = []
    for door in range(1, door_count + 1):
        entities.append(DoorOpenBinarySensor(entry, coordinator, door))
        entities.append(DoorLockBinarySensor(entry, coordinator, door))
    for r in data.readers:
        if r.enabled:
            entities.append(ReaderOnlineBinarySensor(entry, coordinator, r.slot, r.name))
    entities.append(ControllerTamperBinarySensor(entry, coordinator))
    async_add_entities(entities)


class _Base(CoordinatorEntity[AcsWorkStatusCoordinator], BinarySensorEntity):
    _attr_should_poll = False

    def __init__(self, entry: HikAccessConfigEntry, coordinator: AcsWorkStatusCoordinator) -> None:
        super().__init__(coordinator)
        self._entry = entry


class DoorOpenBinarySensor(_Base):
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, entry, coordinator, door: int) -> None:
        super().__init__(entry, coordinator)
        self._door = door
        self._attr_unique_id = f"{entry.unique_id}_door_{door}_open"
        self._attr_name = f"Hikvision Door {door} Open"
        self.entity_id = f"binary_sensor.hikvision_door_{door}_open"

    @property
    def is_on(self) -> bool | None:
        ws: WorkStatus | None = self.coordinator.data
        if ws is None or self._door - 1 >= len(ws.door_open):
            return None
        return ws.door_open[self._door - 1]


class DoorLockBinarySensor(_Base):
    _attr_device_class = BinarySensorDeviceClass.LOCK

    def __init__(self, entry, coordinator, door: int) -> None:
        super().__init__(entry, coordinator)
        self._door = door
        self._attr_unique_id = f"{entry.unique_id}_door_{door}_lock"
        self._attr_name = f"Hikvision Door {door} Lock"
        self.entity_id = f"binary_sensor.hikvision_door_{door}_lock"

    @property
    def is_on(self) -> bool | None:
        ws = self.coordinator.data
        if ws is None or self._door - 1 >= len(ws.door_lock):
            return None
        # device_class=lock: is_on means "unlocked"
        return not ws.door_lock[self._door - 1]


class ReaderOnlineBinarySensor(_Base):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, entry, coordinator, slot: int, name: str) -> None:
        super().__init__(entry, coordinator)
        self._slot = slot
        slug = name.lower().replace(" ", "_") or f"reader{slot}"
        self._attr_unique_id = f"{entry.unique_id}_reader_{slot}_online"
        self._attr_name = f"Hikvision {name} Online"
        self.entity_id = f"binary_sensor.hikvision_{slug}_online"

    @property
    def is_on(self) -> bool | None:
        ws = self.coordinator.data
        if ws is None or self._slot - 1 >= len(ws.reader_online):
            return None
        return ws.reader_online[self._slot - 1]


class ControllerTamperBinarySensor(_Base):
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def __init__(self, entry, coordinator) -> None:
        super().__init__(entry, coordinator)
        self._attr_unique_id = f"{entry.unique_id}_tamper"
        self._attr_name = "Hikvision Controller Tamper"
        self.entity_id = "binary_sensor.hikvision_controller_tamper"

    @property
    def is_on(self) -> bool | None:
        ws = self.coordinator.data
        if ws is None:
            return None
        return ws.tamper
```

**Step 4:** Update `HikAccessData.door_count` set in `__init__.py`:

```python
    entry.runtime_data.door_count = door_count
```

**Step 5:** Run:

```bash
python3 -m pytest tests/test_binary_sensor.py -v
```

Expected: PASS.

**Step 6:** Commit:

```bash
git add tests/test_binary_sensor.py custom_components/hikvision_access/binary_sensor.py custom_components/hikvision_access/__init__.py
git commit -m "feat(binary_sensor): door open/lock, reader online, controller tamper"
```

---

### Task 4.5: Wire the event stream task into setup

**Files:**
- Modify: `tests/test_setup.py` (new test)
- Modify: `custom_components/hikvision_access/__init__.py`

**Step 1:** Append to `tests/test_setup.py`:

```python
@pytest.mark.asyncio
async def test_stream_dispatches_events(hass: HomeAssistant) -> None:
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from custom_components.hikvision_access.const import SIGNAL_EVENT
    from custom_components.hikvision_access.api.discovery import ReaderInfo

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False, CONF_USERNAME: "admin", CONF_PASSWORD: "pw"},
        unique_id="serial-stream",
    )
    entry.add_to_hass(hass)

    async def fake_events():
        yield {"reader_no": 1, "card_no": "X", "name": "Alice", "serial_no": 1,
               "minor": 1, "minor_label": "card_swiped_valid", "major": "event",
               "controller": "", "reader_name": "Eingang", "door_no": 1,
               "timestamp": "t", "backfilled": False, "employee_no": ""}

    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "serial-stream", "model": "M"})
        client.probe_readers = AsyncMock(return_value=[ReaderInfo(slot=1, enabled=True, name="Eingang", description="")])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        client.events = MagicMock(return_value=fake_events())
        cls.return_value = client

        received = []
        async_dispatcher_connect(hass, SIGNAL_EVENT, lambda payload: received.append(payload))
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert received and received[0]["name"] == "Alice"
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_setup.py::test_stream_dispatches_events -v
```

Expected: empty `received`.

**Step 3:** Modify `async_setup_entry` to start the stream task and dispatch:

```python
from homeassistant.helpers.dispatcher import async_dispatcher_send
from .const import SIGNAL_EVENT, EVENT_BUS_NAME


async def _run_stream(hass: HomeAssistant, entry: HikAccessConfigEntry) -> None:
    data = entry.runtime_data
    info = data.device_info
    reader_index = {r.slot: r for r in data.readers}
    try:
        async for evt in data.client.events():
            evt["device_id"] = info.get("serial_number")
            evt["controller"] = evt.get("controller") or info.get("device_name", "")
            r = reader_index.get(evt.get("reader_no"))
            if r:
                evt["reader_name"] = r.name
                evt["door_no"] = (r.slot + 1) // 2
            hass.bus.async_fire(EVENT_BUS_NAME, evt)
            async_dispatcher_send(hass, SIGNAL_EVENT, evt)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        _LOGGER.exception("event stream task crashed")
```

At end of `async_setup_entry`, after forwarding platforms:

```python
    entry.runtime_data.stream_task = hass.loop.create_task(_run_stream(hass, entry))
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_setup.py -v
```

Expected: PASS.

**Step 5:** Commit:

```bash
git add tests/test_setup.py custom_components/hikvision_access/__init__.py
git commit -m "feat: dispatch alertStream events to bus + dispatcher"
```

---

## Phase 5: Backfill + exact statistics

### Task 5.1: Persist last serial per reader in TotalSwipesSensor attributes

**Files:**
- Modify: `custom_components/hikvision_access/sensor.py`
- Modify: `tests/test_sensor.py`

**Step 1:** Add to `TotalSwipesSensor`:

```python
    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_serial_no": self._last_serial}
```

And in `__init__`/`async_added_to_hass`:

```python
        self._last_serial: int | None = None
        ...
        last = await self.async_get_last_state()
        if last:
            try:
                self._count = int(float(last.state))
            except ValueError:
                self._count = 0
            self._last_serial = last.attributes.get("last_serial_no")
```

In `_on_event`:

```python
        self._count += 1
        serial = payload.get("serial_no")
        if isinstance(serial, int):
            self._last_serial = max(self._last_serial or 0, serial)
        self.async_write_ha_state()
```

**Step 2:** Append test:

```python
@pytest.mark.asyncio
async def test_total_swipes_persists_last_serial(hass: HomeAssistant) -> None:
    # similar setup to test_total_swipes_increments…
    # after dispatching events, assert state.attributes["last_serial_no"] == 3
    ...  # leave as exercise; verify with manual assert
```

(Trim or expand based on time; main thing is wiring `last_serial_no` into attrs.)

**Step 3:** Run, commit:

```bash
python3 -m pytest tests/test_sensor.py -v
git add tests/test_sensor.py custom_components/hikvision_access/sensor.py
git commit -m "feat(sensor): persist last_serial_no in TotalSwipes attributes for backfill anchor"
```

---

### Task 5.2: Run backfill on startup

**Files:**
- Modify: `custom_components/hikvision_access/__init__.py`
- Modify: `tests/test_setup.py`

**Step 1:** Add a helper in `__init__.py`:

```python
from datetime import timedelta
from homeassistant.util import dt as dt_util


async def _run_backfill(hass: HomeAssistant, entry: HikAccessConfigEntry) -> None:
    data = entry.runtime_data
    # Collect already-seen serials from restored sensor attributes
    seen: set[int] = set()
    for state in hass.states.async_all("sensor"):
        if state.entity_id.startswith("sensor.hikvision_") and "total_swipes" in state.entity_id:
            s = state.attributes.get("last_serial_no")
            if isinstance(s, int):
                seen.add(s)
    end = dt_util.utcnow()
    start = end - timedelta(hours=24)  # cap lookback to 24h
    try:
        async for evt in data.client.backfill(
            start_time=start.isoformat(timespec="seconds").replace("+00:00", "Z"),
            end_time=end.isoformat(timespec="seconds").replace("+00:00", "Z"),
            already_seen=seen,
        ):
            evt["device_id"] = data.device_info.get("serial_number")
            r = next((rr for rr in data.readers if rr.slot == evt.get("reader_no")), None)
            if r:
                evt["reader_name"] = r.name
                evt["door_no"] = (r.slot + 1) // 2
            hass.bus.async_fire(EVENT_BUS_NAME, evt)
            async_dispatcher_send(hass, SIGNAL_EVENT, evt)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("AcsEvent backfill failed; continuing without it", exc_info=True)
```

At end of `async_setup_entry`, before scheduling stream task:

```python
    hass.async_create_task(_run_backfill(hass, entry))
```

**Step 2:** Add a test asserting `backfill` is called on setup with the right time window. Skip exact-import-stats check for now (next task).

**Step 3:** Run, commit:

```bash
python3 -m pytest tests/test_setup.py -v
git add custom_components/hikvision_access/__init__.py tests/test_setup.py
git commit -m "feat: replay missed AcsEvent entries on startup"
```

---

### Task 5.3: Exact hour-bucket import via async_import_statistics

**Files:**
- Modify: `custom_components/hikvision_access/__init__.py`

**Step 1:** Inside `_run_backfill`, after the `async for` loop ends, group replayed events into hour buckets per reader and call `async_import_statistics`:

```python
from collections import defaultdict
from homeassistant.components.recorder.statistics import async_import_statistics, StatisticData, StatisticMetaData


# … inside _run_backfill, collect replayed_per_reader: dict[int, list[datetime]] as you go.
# After the loop:
    if replayed_per_reader:
        for slot, timestamps in replayed_per_reader.items():
            metadata = StatisticMetaData(
                source="recorder",
                statistic_id=f"sensor.hikvision_{_slug(slot, data.readers)}_total_swipes",
                unit_of_measurement="swipes",
                has_mean=False,
                has_sum=True,
                name=None,
            )
            buckets: dict[datetime, int] = defaultdict(int)
            for ts in timestamps:
                bucket = ts.replace(minute=0, second=0, microsecond=0)
                buckets[bucket] += 1
            running = 0
            stats: list[StatisticData] = []
            for bucket in sorted(buckets):
                running += buckets[bucket]
                stats.append({"start": bucket, "sum": running})
            async_import_statistics(hass, metadata, stats)
```

`_slug` helper resolves slot → entity-id slug consistent with the sensor names.

**Step 2:** Run existing tests:

```bash
python3 -m pytest tests/ -v
```

Expected: PASS.

**Step 3:** Commit:

```bash
git add custom_components/hikvision_access/__init__.py
git commit -m "feat: backfill writes exact hour buckets via async_import_statistics"
```

---

## Phase 6: Device triggers + diagnostics

### Task 6.1: Device trigger — card_read

**Files:**
- Create: `tests/test_device_trigger.py`
- Create: `custom_components/hikvision_access/device_trigger.py`

**Step 1:** Create `tests/test_device_trigger.py`:

```python
"""Tests for the card_read device trigger."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api.discovery import ReaderInfo
from custom_components.hikvision_access.const import CONF_VERIFY_SSL, DOMAIN, EVENT_BUS_NAME


@pytest.mark.asyncio
async def test_card_read_trigger_fires(hass: HomeAssistant) -> None:
    await async_setup_component(hass, "automation", {})

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "h", CONF_PORT: 443, CONF_SSL: True, CONF_VERIFY_SSL: False, CONF_USERNAME: "admin", CONF_PASSWORD: "pw"},
        unique_id="serial-trig",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.hikvision_access.HikAccessClient") as cls:
        client = AsyncMock()
        client.get_device_info = AsyncMock(return_value={"serial_number": "serial-trig", "model": "M"})
        client.probe_readers = AsyncMock(return_value=[ReaderInfo(slot=1, enabled=True, name="Eingang", description="")])
        client.get_acs_work_status = AsyncMock(return_value={"AcsWorkStatus": {}})
        cls.return_value = client
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    device_id = next(iter(dr.async_get(hass).devices.values())).id

    triggered = []
    await hass.services.async_call(
        "automation", "reload", blocking=True,
    )
    await async_setup_component(hass, "automation", {
        "automation": [{
            "trigger": {"platform": "device", "domain": DOMAIN, "device_id": device_id, "type": "card_read"},
            "action": {"event": "card_read_observed"},
        }]
    })
    hass.bus.async_listen("card_read_observed", lambda e: triggered.append(e))

    hass.bus.async_fire(EVENT_BUS_NAME, {"card_no": "X", "name": "n", "reader_no": 1})
    await hass.async_block_till_done()
    assert triggered
```

**Step 2:** Run:

```bash
python3 -m pytest tests/test_device_trigger.py -v
```

Expected: failure ("Unknown trigger type").

**Step 3:** Create `custom_components/hikvision_access/device_trigger.py`:

```python
"""Device trigger implementations."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, EVENT_BUS_NAME

TRIGGER_TYPES = {"card_read", "card_denied", "door_forced", "door_held_open"}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES)}
)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None or not any(i[0] == DOMAIN for i in device.identifiers):
        return []
    return [
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: t}
        for t in TRIGGER_TYPES
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    trigger_type = config[CONF_TYPE]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_BUS_NAME,
            event_trigger.CONF_EVENT_DATA: _filter_for(trigger_type),
        }
    )
    return await event_trigger.async_attach_trigger(hass, event_config, action, trigger_info, platform_type="device")


def _filter_for(trigger_type: str) -> dict[str, Any]:
    # The dispatcher already labels minor codes — we just match against them.
    if trigger_type == "card_read":
        return {}  # any event with this type
    if trigger_type == "card_denied":
        return {"minor_label": "card_authentication_failed"}
    if trigger_type == "door_forced":
        return {"minor_label": "door_forced_open"}
    if trigger_type == "door_held_open":
        return {"minor_label": "door_held_open"}
    return {}
```

Also register the device in `__init__.py` (so `dr.async_get(device_id)` finds it):

```python
from homeassistant.helpers import device_registry as dr

    # after building runtime_data:
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, info.get("serial_number", entry.entry_id))},
        manufacturer="Hikvision",
        model=info.get("model", "Access Controller"),
        name=info.get("device_name", "Hikvision Access Controller"),
        sw_version=info.get("firmware_version"),
    )
```

**Step 4:** Run:

```bash
python3 -m pytest tests/test_device_trigger.py -v
```

Expected: PASS (the `card_read` trigger matches the event with no filter).

**Step 5:** Commit:

```bash
git add tests/test_device_trigger.py custom_components/hikvision_access/device_trigger.py custom_components/hikvision_access/__init__.py
git commit -m "feat: device triggers card_read / card_denied / door_forced / door_held_open"
```

---

### Task 6.2: Diagnostics

**Files:**
- Create: `custom_components/hikvision_access/diagnostics.py`

**Step 1:** Create:

```python
"""Diagnostics for the integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HikAccessConfigEntry

REDACT = {"password", "mac_address", "serial_number"}


def _redact_card(card_no: str) -> str:
    return f"****{card_no[-4:]}" if card_no else ""


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HikAccessConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    return {
        "entry": {k: v for k, v in entry.data.items() if k not in REDACT},
        "device_info": {k: v for k, v in data.device_info.items() if k not in REDACT},
        "readers": [{"slot": r.slot, "enabled": r.enabled, "name": r.name} for r in data.readers],
        "coordinator_last": getattr(data.coordinator, "data", None).__dict__ if data.coordinator and data.coordinator.data else None,
    }
```

**Step 2:** Commit:

```bash
git add custom_components/hikvision_access/diagnostics.py
git commit -m "feat: redacted diagnostics endpoint"
```

---

## Phase 7: CI + docs

### Task 7.1: GitHub Actions

**Files:**
- Create: `.github/workflows/ci.yaml`

**Step 1:** Create:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e .[dev]
      - run: ruff check .
      - run: mypy custom_components/hikvision_access/api
      - run: pytest -v

  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

**Step 2:** Commit:

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: hassfest, hacs, ruff, mypy, pytest"
```

---

### Task 7.2: README — installation + Reolink example

**Files:**
- Modify: `README.md`

**Step 1:** Expand the README to cover installation (HACS custom repo), configuration screenshots placeholder, the event payload schema, the four device triggers, the Reolink snapshot-on-swipe automation example, and a `utility_meter` example for daily/hourly counters.

**Step 2:** Commit:

```bash
git add README.md
git commit -m "docs: usage + Reolink correlation + utility_meter examples"
```

---

## Phase 8: Live smoke test

### Task 8.1: Add live-test runner

**Files:**
- Create: `tests/live/.env.example`
- Create: `tests/live/test_live.py`

**Step 1:** `.env.example`:

```
HIK_HOST=192.0.2.1
HIK_PORT=443
HIK_USERNAME=admin
HIK_PASSWORD=
HIK_VERIFY_SSL=false
```

**Step 2:** `test_live.py` (skipped unless `RUN_LIVE=1`):

```python
"""Live smoke test against a real Hikvision controller. Skipped by default."""

from __future__ import annotations

import asyncio
import os

import pytest

from custom_components.hikvision_access.api import HikAccessClient

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to run")


@pytest.mark.asyncio
async def test_device_info_and_one_event() -> None:
    client = HikAccessClient(
        host=os.environ["HIK_HOST"],
        port=int(os.environ.get("HIK_PORT", 443)),
        username=os.environ["HIK_USERNAME"],
        password=os.environ["HIK_PASSWORD"],
        ssl=True,
        verify_ssl=os.environ.get("HIK_VERIFY_SSL", "false").lower() == "true",
    )
    try:
        info = await client.get_device_info()
        assert info["model"]
        async def wait_one():
            async for evt in client.events():
                return evt
        # 60s window — user should hold a chip to the reader.
        evt = await asyncio.wait_for(wait_one(), timeout=60)
        assert evt["serial_no"] is not None
    finally:
        await client.stop()
```

**Step 3:** Commit:

```bash
git add tests/live/
git commit -m "test(live): manual smoke test against real Hikvision controller"
```

---

### Task 8.2: Manual live run

Run on the user's machine (NOT in CI):

```bash
cd /Users/pstrawder/Developer/hikvision
cp tests/live/.env.example tests/live/.env
# edit tests/live/.env: set HIK_PASSWORD
set -a; source tests/live/.env; set +a
RUN_LIVE=1 python3 -m pytest tests/live/ -v
```

Then:

1. Install in HA: copy `custom_components/hikvision_access` into the HA config dir, restart HA.
2. Add the integration via UI with the same credentials.
3. Verify entities: `sensor.hikvision_eingang_last_event`, `sensor.hikvision_eingang_total_swipes`, `sensor.hikvision_eingang_unique_visitors_today`, `binary_sensor.hikvision_door_1_open`, `binary_sensor.hikvision_door_1_lock`, `binary_sensor.hikvision_eingang_online`, `binary_sensor.hikvision_controller_tamper`.
4. Hold a chip → confirm `hikvision_access_event` in Developer Tools → Events.
5. Restart HA mid-hour → confirm `total_swipes` resumes from saved value, hourly stats are exact.

No commit; this is verification.

---

## Done criteria

- All unit + HA tests green on CI matrix.
- `hassfest` + `hacs/action@main` both pass.
- Live smoke test produces a real event from the target device.
- HA shows real-time card-read events with `name` populated for known cardholders.
- After a manual HA restart, `total_swipes` counter survives and missed events are replayed (visible in HA logs as `backfilled: true`).
