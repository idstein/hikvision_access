"""Constants for the Hikvision Access Control integration."""

from __future__ import annotations

DOMAIN = "hikvision_access"

DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = False

CONF_VERIFY_SSL = "verify_ssl"
CONF_BACKFILL_DAYS = "backfill_days"
DEFAULT_BACKFILL_DAYS = 30
BACKFILL_DAYS_MIN = 0     # 0 means "no backfill" (just live stream)
BACKFILL_DAYS_MAX = 365   # device-side buffer will usually run out long before this

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
