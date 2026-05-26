# Hikvision Access Control for Home Assistant

HACS-distributable integration for Hikvision access controllers (DS-K2702WX, DS-K2604WX, …) via ISAPI.

Streams chip/card events in real time, exposes door / lock / tamper binary sensors, and provides daily / hourly usage statistics that survive HA restarts (including events that landed on the controller while HA was down).

## Status

Pre-release. Tested against `DS-K2702WX-E1(P)` firmware V1.7.4.

## Features

- **Real-time chip/card events** via ISAPI's `/Event/notification/alertStream` (no polling).
- **Auto-discovery of enabled readers** by probing `CardReaderCfg/{slot}`.
- **Door state** (open / locked / tamper) polled from `AcsWorkStatus` every 10 s.
- **Lifetime swipe counter** per reader (`total_increasing` — HA's long-term statistics keep hourly buckets forever).
- **Unique visitors today** per reader. Card numbers are SHA-256-hashed before persisting to avoid leaking PII through HA's state-store / recorder DB / REST API. Display names remain visible.
- **Startup backfill** of events the controller buffered while HA was down. Replayed events are also injected into HA's long-term statistics at their correct hour buckets — no smearing across the downtime window.
- **Device triggers** in the UI automation editor: `card_read`, `card_denied`, `door_forced`, `door_held_open`.
- **Redacted diagnostics** via the standard HA "Download diagnostics" button.

## Installation (HACS custom repository)

1. HACS → Integrations → ⋮ → Custom repositories.
2. Add `paulstrawder/hikvision_access`, category `Integration`.
3. Install, restart HA.
4. Settings → Devices & Services → Add Integration → "Hikvision Access Control".
5. Enter host, port (default 443), HTTPS toggle, "Verify SSL" toggle (Hikvision uses self-signed certs by default — leave off unless you've installed a real cert), username (default `admin`), password.

## Entities

For a controller with one enabled reader called "Eingang" and one wired door:

| Entity ID | Type | Description |
|---|---|---|
| `sensor.hikvision_eingang_last_event` | sensor | Name of the last person to swipe. Attributes carry the full event payload. |
| `sensor.hikvision_eingang_total_swipes` | sensor (`total_increasing`) | Lifetime swipe count. `last_serial_no` attribute persists the latest event serial for backfill. |
| `sensor.hikvision_eingang_unique_visitors_today` | sensor | Distinct cardholders seen since local midnight. Attributes: `cardholders` (names), `card_hashes` (opaque digests), `last_reset`. |
| `binary_sensor.hikvision_door_1_open` | binary_sensor (`door`) | Magnetic-contact state. |
| `binary_sensor.hikvision_door_1_lock` | binary_sensor (`lock`) | `on` = unlocked (HA convention). |
| `binary_sensor.hikvision_eingang_online` | binary_sensor (`connectivity`) | Reader online state from `cardReaderOnlineStatus`. |
| `binary_sensor.hikvision_controller_tamper` | binary_sensor (`tamper`) | From `hostAntiDismantleStatus`. |

## Event bus

Every chip read fires `hikvision_access_event` on HA's event bus with the normalized payload:

```yaml
device_id: DS-K2702WX_<serial>
controller: Access Controller
reader_no: 1
reader_name: Eingang
door_no: 1
card_no: "1234567890"
employee_no: "42"
name: Max Mustermann
major: event                # 0=alarm, 1=exception, 2=operation, 3=event, 5=ungrouped
minor: 1
minor_label: card_swiped_valid    # decoded; unknown_<n> if unmapped
serial_no: 1
timestamp: "2026-04-02T12:00:57+02:00"
backfilled: false
```

Replayed events have `backfilled: true`.

## Hourly / daily / monthly counters via `utility_meter`

The integration ships a single `total_increasing` swipe counter per reader. To get rotating period counters, attach HA's built-in `utility_meter` helper:

```yaml
# configuration.yaml
utility_meter:
  swipes_eingang_hourly:
    source: sensor.hikvision_eingang_total_swipes
    cycle: hourly
  swipes_eingang_daily:
    source: sensor.hikvision_eingang_total_swipes
    cycle: daily
  swipes_eingang_monthly:
    source: sensor.hikvision_eingang_total_swipes
    cycle: monthly
```

The native hourly-bucket histogram is already available in the "Statistics" graph card without any extra config.

## Reolink doorbell snapshot on swipe

The integration ships device triggers, so the automation editor lists this directly. Equivalent YAML:

```yaml
automation:
  - alias: "Snapshot Reolink doorbell on Eingang swipe"
    trigger:
      - platform: device
        domain: hikvision_access
        device_id: !secret hikvision_device_id
        type: card_read
    condition:
      - condition: state
        entity_id: binary_sensor.reolink_doorbell_person
        state: "on"
        for: { seconds: -5 }   # was on within the last 5s
    action:
      - service: camera.snapshot
        target:
          entity_id: camera.reolink_doorbell
        data:
          filename: >-
            /config/www/entries/
            {{ trigger.event.data.name | default('unknown') }}_
            {{ now().strftime('%Y%m%d_%H%M%S') }}.jpg
```

`trigger.event.data` is the full event payload above.

## Other automation examples

**Notify when a denial event fires:**

```yaml
trigger:
  - platform: device
    domain: hikvision_access
    device_id: !secret hikvision_device_id
    type: card_denied
action:
  - service: notify.mobile_app_phone
    data:
      title: "Access denied at Eingang"
      message: >-
        Reason: {{ trigger.event.data.minor_label }} ·
        Card: ****{{ trigger.event.data.card_no[-4:] }}
```

**Door forced/held-open alerts** use `type: door_forced` / `type: door_held_open`.

## Troubleshooting

### "Cannot connect"

- Confirm the controller is reachable at `https://<host>/ISAPI/System/deviceInfo` from the HA host (curl with `--digest -u admin:<pw> --insecure`).
- If you're on HTTPS and HA refuses to connect, leave "Verify SSL certificate" off until you've installed a non-default cert.

### "Invalid auth"

- Hikvision admin accounts have a brute-force counter. After 2 consecutive 401s the integration stops retrying and surfaces `ConfigEntryAuthFailed`. Use the "Reconfigure" button to enter the new password.

### Counters frozen after restart

The integration replays the last 24 h of AcsEvent history on startup. If your downtime exceeded that or the device's buffer wrapped, those events are lost. The `last_serial_no` attribute on `sensor.hikvision_<reader>_total_swipes` shows the most recent event the integration has ingested.

### Diagnostics

HA → Settings → Devices & Services → Hikvision Access Control → ⋮ → Download diagnostics. The dump redacts `password`, `mac_address`, and `serial_number`.

## Limitations

- WebSocket transport (port 7682) is not implemented in v1 — the alertStream HTTPS multipart channel is documented and covers the same events. Track [#issue](https://github.com/paulstrawder/hikvision_access/issues) if you want WS.
- Door remote-control (`/AccessControl/RemoteControl/door/<n>`) is not exposed — out of scope for v1.
- The controller's user database (557 entries on the reference device) is not synced into HA; the integration relays whatever the device puts in the event payload.
- Multi-controller setups: each controller is one config entry. Unique-visitor counts and statistics are per-reader, not aggregated.

## Development

```bash
git clone https://github.com/paulstrawder/hikvision_access
cd hikvision_access
uv venv
uv pip install -e '.[dev]'

# Pure-Python (parser, events, discovery, client, http, backfill).
PYTHONPATH=. pytest tests/ --ignore=tests/ha/ -v

# HA integration (config flow, setup, entities, triggers, diagnostics).
PYTHONPATH=. pytest tests/ha/ -p homeassistant -v
```

Live smoke test against a real device (skipped unless `RUN_LIVE=1`):

```bash
cp tests/live/.env.example tests/live/.env
# edit tests/live/.env to set HIK_HOST and HIK_PASSWORD
set -a; source tests/live/.env; set +a
RUN_LIVE=1 PYTHONPATH=. pytest tests/live/ -v
```

## License

MIT.
