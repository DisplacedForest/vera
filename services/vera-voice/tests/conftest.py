"""pytest configuration for vera-voice tests."""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: marks tests that load real ML models and may take 30-60s",
    )
