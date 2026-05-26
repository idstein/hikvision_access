"""Pytest fixtures for HA integration tests.

These tests opt back into the pytest-homeassistant-custom-component plugin
(disabled globally in pyproject.toml). Run with:

    PYTHONPATH=. .venv/bin/python -m pytest tests/ha/ -p homeassistant -v
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: F401


# Re-export everything HA needs; the plugin provides hass, enable_custom_integrations, etc.
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make pytest auto-import the custom_components directory for these tests."""
    return None
