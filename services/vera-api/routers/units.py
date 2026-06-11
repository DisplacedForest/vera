"""Temperature-unit config — TEMPERATURE_UNIT: fahrenheit (default) | celsius.

Shared by the weather and heartbeat veins. Sources are asked to report in the
configured unit and thresholds are interpreted in it, so no conversion math lives
in code — the unit is a request parameter and a display label, end to end.
"""
import os


def unit() -> str:
    """The configured temperature unit name, normalized for the Open-Meteo API."""
    u = os.environ.get("TEMPERATURE_UNIT", "").strip().lower()
    return "celsius" if u.startswith("c") else "fahrenheit"


def label() -> str:
    """The display suffix for temperatures in the configured unit."""
    return "C" if unit() == "celsius" else "F"
