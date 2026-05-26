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
    assert out["minor_label"] == "card_swiped_valid"
    assert out["timestamp"] == "2026-04-02T12:00:57+02:00"
    assert out["backfilled"] is False


def test_normalize_denial_code_label():
    raw = {
        "eventType": "AccessControllerEvent",
        "AccessControllerEvent": {"majorEventType": 3, "subEventType": 10, "serialNo": 2},
    }
    out = normalize_event(raw)
    assert out["minor_label"] == "card_blocked"


def test_normalize_returns_none_on_malformed_major_or_minor():
    raw = {
        "eventType": "AccessControllerEvent",
        "AccessControllerEvent": {
            "majorEventType": "not-an-int",
            "subEventType": 1,
            "serialNo": 3,
        },
    }
    assert normalize_event(raw) is None

    raw2 = {
        "eventType": "AccessControllerEvent",
        "AccessControllerEvent": {"majorEventType": 3, "subEventType": None, "serialNo": 4},
    }
    assert normalize_event(raw2) is None


def test_normalize_unknown_minor_falls_back():
    raw = {
        "eventType": "AccessControllerEvent",
        "AccessControllerEvent": {"majorEventType": 3, "subEventType": 999999, "serialNo": 1},
    }
    out = normalize_event(raw)
    assert out["minor_label"] == "unknown_999999"


def test_normalize_returns_none_for_non_access_event():
    assert normalize_event({"eventType": "videoloss"}) is None
