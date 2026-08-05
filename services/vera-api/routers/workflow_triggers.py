import re


SCHEDULE_TYPE = "trigger.schedule"

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

INTERVAL_MINUTES = [5, 10, 15, 30, 60, 120, 240, 360, 720]

SPECS = {
    SCHEDULE_TYPE: {
        "label": "Schedule",
        "icon": "clock",
        "tint": "accent",
        "category": "trigger",
        "description": "Starts this workflow automatically on the schedule you set.",
        "config_schema": {
            "mode": {"type": "choice", "default": "daily", "options": ["daily", "weekly", "interval"]},
            "time": {"type": "text", "default": "05:00"},
            "weekday": {"type": "choice", "default": "monday", "options": WEEKDAYS},
            "every_minutes": {"type": "choice", "default": 60, "options": INTERVAL_MINUTES},
        },
        "insertable": False,
    },
}

JOB_FOR_WORKFLOW = {"pulse": "pulse"}

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

_CRON_DOW = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
             "friday": 5, "saturday": 6, "sunday": 0}

_DOW_NAME = {str(number): name for name, number in _CRON_DOW.items()} | {"7": "sunday"}


def is_trigger(node_type) -> bool:
    return node_type in SPECS


def _parse_time(value) -> tuple[int, int]:
    match = _TIME.match(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Schedule time must be a time of day like 05:00")
    return int(match.group(1)), int(match.group(2))


def validate_config(config: dict):
    if config.get("mode", "daily") in ("daily", "weekly"):
        _parse_time(config.get("time", "05:00"))


def cron_for(config: dict) -> str:
    mode = config.get("mode", "daily")
    if mode == "interval":
        minutes = config.get("every_minutes", 60)
        if minutes not in INTERVAL_MINUTES:
            raise ValueError("Schedule interval is not one of the offered choices")
        if minutes < 60:
            return f"*/{minutes} * * * *"
        return f"0 */{minutes // 60} * * *"
    hour, minute = _parse_time(config.get("time", "05:00"))
    if mode == "weekly":
        weekday = config.get("weekday", "monday")
        if weekday not in _CRON_DOW:
            raise ValueError("Schedule weekday is not one of the offered choices")
        return f"{minute} {hour} * * {_CRON_DOW[weekday]}"
    return f"{minute} {hour} * * *"


DEFAULT_CONFIG = {"mode": "daily", "time": "05:00"}


def config_for(cron) -> dict | None:
    fields = cron.split() if isinstance(cron, str) else []
    if len(fields) != 5:
        return None
    minute, hour, dom, month, dow = fields
    if dom != "*" or month != "*":
        return None
    if dow == "*" and hour == "*" and minute.startswith("*/"):
        every = _int(minute[2:])
        return {"mode": "interval", "every_minutes": every} if every in INTERVAL_MINUTES else None
    if dow == "*" and minute == "0" and hour.startswith("*/"):
        every = _int(hour[2:])
        if every is not None and every * 60 in INTERVAL_MINUTES:
            return {"mode": "interval", "every_minutes": every * 60}
        return None
    clock = _clock(minute, hour)
    if clock and dow == "*":
        return {"mode": "daily", "time": clock}
    if clock and dow in _DOW_NAME:
        return {"mode": "weekly", "weekday": _DOW_NAME[dow], "time": clock}
    return None


def _int(text) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _clock(minute, hour) -> str | None:
    m, h = _int(minute), _int(hour)
    if m is None or h is None or not (0 <= m <= 59 and 0 <= h <= 23):
        return None
    return f"{h:02d}:{m:02d}"
