"""Pytest configuration for the A2A test suite."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "golden: Monday NSE validation fixture; update before relying on tight tolerance.",
    )
