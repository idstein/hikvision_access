"""Pytest fixtures for HA integration tests.

These tests opt back into the pytest-homeassistant-custom-component plugin
(disabled globally in pyproject.toml). Run with:

    PYTHONPATH=. .venv/bin/python -m pytest tests/ha/ -p homeassistant -v
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make pytest auto-import custom_components/ for HA tests in this folder.

    The plugin provides `hass`, `enable_custom_integrations`, `MockConfigEntry`,
    etc. — test modules import `MockConfigEntry` directly from
    `pytest_homeassistant_custom_component.common`.
    """
    return None
