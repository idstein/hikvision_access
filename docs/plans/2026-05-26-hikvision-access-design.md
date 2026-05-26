# Hikvision Access Control — Home Assistant HACS Integration

**Status:** Design approved, ready for implementation plan.
**Author:** paulidstein@gmail.com
**Date:** 2026-05-26

## Goal

Stream `AccessControllerEvent`s from a Hikvision access controller into Home Assistant so that:

- Chip/card reads appear in real time (who, where, when).
- Door/lock/tamper state is exposed as binary sensors.
- Daily/hourly usage statistics work with HA's native long-term-stats and `utility_meter` helpers.
- Automations can correlate swipes with other HA entities (e.g. Reolink person detection).

Target device confirmed live: `DS-K2702WX-E1(P)`, firmware V1.7.4, at `https://192.0.2.1:443`. 557 registered users; reader 1 ("Eingang") active, readers 2–4 disabled.

## Non-goals (v1)

- WebSocket transport (port 7682 reachable, accepts WS upgrade, but post-handshake framing requires reverse-engineering; alertStream gives identical event content with documented framing). Stubbed for v1.1.
- Door remote-control actions.
- UserInfo enrichment from `/ISAPI/AccessControl/UserInfo` (event payloads already carry `name`).
- Push/arming mode `httpHosts` (listening mode chosen).
- Video / camera / `InputProxy` channels — all `notSupport` on this controller; out of scope.
- Snapshots, two-way audio, lighting — controller has no video subsystem.

## Repo layout

```
/Users/pstrawder/Developer/hikvision/        ← new HACS repo
├── hacs.json
├── README.md
├── custom_components/hikvision_access/
│   ├── __init__.py           # async_setup_entry / async_unload_entry
│   ├── manifest.json         # domain=hikvision_access, iot_class=local_push
│   ├── config_flow.py        # host, port, ssl, verify_ssl, username, password
│   ├── const.py
│   ├── api/
│   │   ├── __init__.py       # HikAccessClient
│   │   ├── transport.py      # Transport base + AlertStreamTransport (v1) + WebSocketTransport (stub)
│   │   ├── multipart.py      # streaming MIME-multipart parser
│   │   ├── events.py         # AccessControllerEvent dataclass + minor/major code decoder
│   │   ├── discovery.py      # probe CardReaderCfg/{i}, deviceInfo, AcsWorkStatus
│   │   └── backfill.py       # AcsEvent search + replay + async_import_statistics injection
│   ├── coordinator.py        # DataUpdateCoordinator for AcsWorkStatus poll
│   ├── sensor.py             # last-event, total_swipes, unique_visitors_today
│   ├── binary_sensor.py      # door open/forced/tamper/online, reader online
│   ├── device_trigger.py     # card_read, card_denied, door_forced, door_held_open
│   ├── diagnostics.py
│   ├── strings.json
│   └── translations/en.json
├── tests/
│   ├── fixtures/             # captured alertStream chunks
│   ├── test_multipart.py
│   ├── test_events.py
│   ├── test_client.py
│   ├── test_config_flow.py
│   ├── test_setup.py
│   ├── test_backfill.py
│   └── test_device_trigger.py
├── docs/plans/
│   └── 2026-05-26-hikvision-access-design.md   ← this file
└── .github/workflows/        # hassfest, hacs, ruff, mypy, pytest
```

## Architecture

```
   Hikvision DS-K2702WX (ISAPI)                Home Assistant
   ────────────────────────────                ──────────────
   :443/ISAPI/Event/notification/alertStream
                       │ HTTPS multipart push
                       ▼
                  AlertStreamTransport ──┐
                                         ▼
   :443/ISAPI/AccessControl/AcsEvent    HikAccessClient
                       ▲                  (asyncio task)
   on startup, POST    │
   (backfill missed)   │                  │
                       │                  ▼
                  backfill.replay()   parsed AccessControllerEvent
                                          │
                                          ├──► dispatcher_send(SIGNAL_EVENT)
                                          │       │
                                          │       ▼
                                          │   ┌─ event bus: hikvision_access_event
                                          │   ├─ per-reader last-event sensor
                                          │   ├─ total_swipes (total_increasing)
                                          │   └─ unique_visitors_today (set, RestoreEntity)
                                          │
   :443/ISAPI/AccessControl/AcsWorkStatus
                       ▲ poll every 10s
                       │
                  AcsWorkStatusCoordinator ──► door + reader-online binary_sensors
```

One config entry = one controller. One `HikAccessClient` and one `DataUpdateCoordinator` per entry. No global state.

## Discovery

Runs once per `async_setup_entry`:

1. `GET /ISAPI/System/deviceInfo` → model, serial, firmware, MAC → HA device registry.
2. **Reader probe**: `GET /AccessControl/CardReaderCfg/{i}?format=json` for `i=1..8`, stop on first `notSupport`. Cache `{slot, enabled, name, description}`. Confirmed shape on target device:
   ```json
   {"CardReaderCfg":{"enable":true,"cardReaderName":"Eingang",
    "cardReaderDescription":"DS-K1105EDKB-QRbuild20251013", ...}}
   ```
3. **Door count**: model lookup table (`DS-K2702WX-E1(P)` → 2 doors). Fallback heuristic `ceil(reader_count / 2)`.
4. **Reader→door map**: default Hik convention (readers `2k-1`, `2k` → door `k`). Overridable in options flow.
5. **First AcsWorkStatus poll** happens via the coordinator's `_async_setup`, populating door binary_sensors before they're added.

## Transport: alertStream (v1)

- `aiohttp.ClientSession` with `ssl=False` if `verify_ssl=False`, `BasicAuth` falling back to `DigestAuth` (Hik requires digest).
- `GET /ISAPI/Event/notification/alertStream` keeps a long-lived response open.
- Custom streaming MIME parser yields `dict` per chunk.
- Filter `eventType == "AccessControllerEvent"`.
- Liveness: any chunk (including `videoloss` heartbeats observed every ~30s) resets the 90s degraded-state timer.

### Reconnect

Exponential backoff `1s, 2s, 4s, 8s, 16s, 32s, 60s, 60s, …`. Reset after 60s of stable connection.

### Auth failures

After 2 consecutive `401`s, raise `ConfigEntryAuthFailed`. Prevents lockout of the Hik `admin` account.

### Connection states

`disconnected → connecting → connected → degraded → disconnected`. Surfaced as the `available` property on all entities.

## Event normalization

Raw chunk:
```json
{"eventType":"AccessControllerEvent","dateTime":"2026-04-02T12:00:57+02:00",
 "AccessControllerEvent":{"deviceName":"Access Controller","majorEventType":3,
  "subEventType":1034,"serialNo":1,"currentEvent":false,
  "cardNo":"...","name":"...","cardReaderNo":1,"employeeNoString":"..."}}
```

Normalized payload (on event bus, on sensor state attributes):
```python
{
  "device_id":    "DS-K2702WX-E1(P)_<serial>",
  "controller":   "Access Controller",
  "reader_no":    1,
  "reader_name":  "Eingang",
  "door_no":      1,
  "card_no":      "1234567890",
  "employee_no":  "42",
  "name":         "Max Mustermann",
  "major":        "event",        # 0=alarm, 1=exception, 2=operation, 3=event, 5=…
  "minor":        1034,
  "minor_label":  "card_swiped_valid",  # via lookup; "unknown_<n>" if unmapped
  "serial_no":    1,
  "timestamp":    "2026-04-02T12:00:57+02:00",
  "backfilled":   false,
}
```

Lookup tables for major/minor codes live in `api/events.py`, seeded from the `AcsEvent/capabilities` enum (`minorAlarm`, `minorException`, `minorOperation`, `minorEvent`) and the ISAPI PDF section 16. Unknown codes pass through with `minor_label: "unknown_<n>"`.

## Entities

| Entity | Per | Class | Notes |
|---|---|---|---|
| `sensor.<reader>_last_event` | enabled reader | `sensor` | State = last person name (or card_no if no name). Attributes = full normalized payload. |
| `sensor.<reader>_total_swipes` | enabled reader | `state_class=total_increasing` | Lifetime cumulative. HA recorder + long-term stats give hourly buckets persisted forever. Uses `RestoreEntity`. |
| `sensor.<reader>_unique_visitors_today` | enabled reader | `state_class=measurement`, `last_reset` = today-00:00 local | Python set of `card_no` seen today, capped 5000 entries. Set serialized into state attributes via `RestoreEntity`. Reset job at local midnight. |
| `binary_sensor.<door>_lock` | door | `device_class=lock` | Inverted from `AcsWorkStatus.doorLockStatus[k-1]`. |
| `binary_sensor.<door>_open` | door | `device_class=door` | From `AcsWorkStatus.magneticStatus[k-1]`. |
| `binary_sensor.<door>_forced` | door | `device_class=problem` | Fired from event stream `subEventType` in forced-door codes. |
| `binary_sensor.<door>_held_open` | door | `device_class=problem` | Same, held-open codes. |
| `binary_sensor.<reader>_online` | reader | `device_class=connectivity` | From `AcsWorkStatus.cardReaderOnlineStatus[i-1]`. |
| `binary_sensor.<controller>_tamper` | controller | `device_class=tamper` | From `AcsWorkStatus.hostAntiDismantleStatus`. |
| `binary_sensor.<controller>_connected` | controller | `device_class=connectivity` | Mirrors the `HikAccessClient` connection state. |

Statistics composition that users build on top (documented in README, not in code):

```yaml
utility_meter:
  swipes_eingang_hourly:
    source: sensor.hikvision_eingang_total_swipes
    cycle: hourly
  swipes_eingang_daily:
    source: sensor.hikvision_eingang_total_swipes
    cycle: daily
```

## Device triggers

Exposed in `device_trigger.py` so HA's UI automation editor surfaces them:

- `card_read` — any AccessControllerEvent carrying `card_no`.
- `card_denied` — `minor` in the denial-codes set.
- `door_forced` — forced-door codes.
- `door_held_open` — held-open codes.

Trigger data mirrors the normalized payload. Example automation in README:

```yaml
trigger:
  - platform: device
    domain: hikvision_access
    type: card_read
condition:
  - condition: state
    entity_id: binary_sensor.reolink_doorbell_person
    state: "on"
    for: { seconds: -5 }
action:
  - service: camera.snapshot
    target: { entity_id: camera.reolink_doorbell }
    data:
      filename: "/config/www/entries/{{ trigger.event.data.name }}_{{ now() }}.jpg"
```

## Persistence & backfill

Risk: HA restart in the middle of a day/hour. Three-layer mitigation:

1. **`RestoreEntity` on all stats sensors.** `total_swipes` value, `unique_visitors_today` set + `last_reset`, and the last-seen event `serial_no` per reader are serialized into state attributes and reloaded.

2. **AcsEvent backfill on startup.** Controller buffers events in `/ISAPI/AccessControl/AcsEvent` (search API; capabilities confirm `isSupportAcsEvent`). On startup:
   1. Read `last_serial_no` and `last_event_time` from restored attributes.
   2. POST `AcsEvent` search for `startTime = last_event_time - 60s` (overlap buffer) … `now`.
   3. Dedup by `serialNo`, replay each through the normal normalization pipeline with `backfilled: true`.
   4. Sensors catch up; events fire on the bus with original timestamps.

3. **Exact hour-bucket import via `homeassistant.components.recorder.statistics.async_import_statistics`.** Inject per-hour swipe counts into past hour buckets so long-term stats charts stay exact across restart windows. Done after the replay, as a one-shot startup task.

Caveats: the device's event log eventually wraps (capacity in the thousands for this model); a multi-day outage could lose old events. Acceptable.

## AcsWorkStatus polling

Coordinator polls `/ISAPI/AccessControl/AcsWorkStatus?format=json` every 10s. Soft-fail: 3 consecutive failures flip door binary_sensors to `unavailable`; alertStream is unaffected.

Confirmed shape on target device:
```json
{"AcsWorkStatus":{
  "doorLockStatus":[0,0,...,0],     // 126 entries; trailing zeros ignored
  "doorStatus":[4,4,...,4],
  "magneticStatus":[0,0,...,0],
  "cardReaderOnlineStatus":[1],     // length matches active reader count
  "powerSupplyStatus":"ACPowerSupply",
  "hostAntiDismantleStatus":"close",
  ...
}}
```

## Error handling summary

| Failure | Behaviour |
|---|---|
| TCP / TLS error on alertStream | Reconnect with exponential backoff. |
| Stalled stream (no chunk 90s) | Force reconnect. |
| 2× consecutive 401 | `ConfigEntryAuthFailed`, reauth flow. |
| `AcsWorkStatus` poll fails | After 3 consecutive failures, door entities go `unavailable`. |
| Malformed multipart frame | Skip, log at debug; keep connection. |
| Unknown `subEventType` | Fire with `minor_label: "unknown_<n>"`. |
| AcsEvent backfill fails | Log warning, continue without backfill. |

## Diagnostics

`diagnostics.py` returns: redacted device info, last 20 raw alertStream chunks (card numbers truncated to last 4 digits), last `AcsWorkStatus` snapshot, connection-state history, reader→door map, lookup-table miss counters.

## Testing

Order (each layer must pass before the next):

1. **Pure-Python unit tests.**
   - `multipart.py` parser with golden fixtures (split chunks, oversized `Content-Length`, JSON+XML bodies).
   - `events.py` decoder: every documented `subEventType` in `AcsEvent/capabilities` returns non-`unknown` label.
   - `HikAccessClient` connect logic with `aiohttp.test_utils.TestServer`: 401→reauth, backoff schedule, 90s degraded detector.
   - `backfill.py` against a fixture AcsEvent JSON response; verify dedup and `async_import_statistics` payload shape.

2. **HA integration tests** (`pytest-homeassistant-custom-component`).
   - Config flow: happy path, wrong password, unreachable host, options-flow reader↔door remap, reauth.
   - `async_setup_entry` with mocked client → assert entity creation, bus events, clean unload.
   - Device trigger registration.
   - Snapshot test for entity attributes.

3. **Live smoke test** against `192.0.2.1`.
   - Confirm one "Eingang" reader sensor + 2 door binary_sensors + counters appear.
   - Hold chip → confirm event on bus, `name`/`card_no` populated, counters increment, unique-visitors set grows.
   - Pull controller's network cable 30s → reconnect; `available` flips off then on.
   - HA restart mid-hour → counters survive; backfill replays missed events; long-term stats hour bucket exact.

4. **CI.**
   - GitHub Actions matrix on Python 3.12/3.13 × HA `current`, `current-1`.
   - `hassfest` + `hacs/action@main`.
   - Ruff + mypy strict on `api/`, lenient on HA entity files.
