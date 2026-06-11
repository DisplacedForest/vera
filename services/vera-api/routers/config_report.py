"""Startup config report — one legible log block showing what's wired.

A fresh install's first question is "why is X dead?"; this answers it at boot.
Logs only set/unset per variable — never a value, so secrets stay out of logs.
"""

import logging
import os

log = logging.getLogger("vera.config")

# (group, vars). Everything optional by design: an unset integration degrades
# gracefully — this report is how you see what's off and why.
GROUPS: list[tuple[str, list[str]]] = [
    ("core llm",      ["VERA_BASE", "VERA_MODEL", "VERA_CHAT_TEMPLATE_KWARGS"]),
    ("open-webui",    ["OWUI_BASE", "OWUI_KEY"]),
    ("web search",    ["SEARXNG_BASE", "PLAYWRIGHT_WS"]),
    ("dream/coder",   ["DREAM_BASE", "DREAM_MODEL", "DREAM_TOOL_PROTOCOL"]),
    ("image gen",     ["VERA_IMAGE_BASE", "IMAGE_PROTOCOL"]),
    ("identity",      ["VERA_OWNER_NAME", "HOME_LOCATION_NAME", "HOME_TZ"]),
    ("location",      ["WEATHER_LAT", "WEATHER_LON", "HOME_STATE"]),
    ("home assistant", ["HOME_ASSISTANT_BASE", "HOME_ASSISTANT_KEY"]),
    ("kitchen",       ["GROCY_BASE", "GROCY_KEY", "MEALIE_BASE", "MEALIE_KEY"]),
    ("media",         ["OVERSEERR_BASE", "OVERSEERR_KEY"]),
    ("signals",       ["FRED_KEY", "EIA_KEY", "EIA_RESPONDENT"]),
    ("signals knobs", ["SIGNALS_ORIENTATION", "SIGNALS_NEWS_QUERIES", "SIGNALS_SENTINELS",
                       "SIGNALS_IMPACT_GOODS", "SIGNALS_NEAR_KM"]),
    ("weather",       ["TEMPERATURE_UNIT", "WEATHER_FORECAST_URL"]),
    ("dream tuning",  ["DREAM_DEDUP_THRESHOLD", "DREAM_PROMOTE_CONF", "DREAM_CLUSTER_THRESHOLD",
                       "DREAM_MAX_OPINIONS"]),
    ("toggles",       ["HEARTBEAT_ENABLED", "KNOWLEDGE_GROOM_ENABLED", "MEMORY_GROOM_ENABLED"]),
    ("tunables",      ["VERA_MEMORY_CORE_CHARS", "VERA_MEMORY_SCRATCH_TTL_HOURS",
                       "VERA_INTEREST_COOLDOWN_HOURS", "MEDIA_CURATION_CAP", "PULSE_RUN_STALE_SECS",
                       "HOME_EVENTS_RETAIN_DAYS", "HOME_MODEL_WINDOW_DAYS"]),
    ("actuation",     ["UNRAID_BASE", "UNRAID_KEY", "HA_ALLOWED_SERVICES", "HA_ALLOWED_DOMAINS"]),
    ("pulse",         ["PULSE_FOLDER_ID", "VERA_DEFAULT_USER", "PULSE_MIN_CARDS",
                       "PULSE_MAX_CARDS", "PULSE_TRIAGE_ROUNDS", "PULSE_MAX_PER_INTEREST"]),
    ("scheduler",     ["SCHEDULER_ENABLED"]),
]


def report(version: str) -> None:
    from routers import env_compat
    log.info("vera-api v%s — configuration (set/unset per integration; unset = that capability is off):", version)
    for group, names in GROUPS:
        parts = [f"{n}={'set' if env_compat.read(n) or os.environ.get(n, '').strip() else 'unset'}"
                 for n in names]
        log.info("  [%-14s] %s", group, "  ".join(parts))
    for old, new in env_compat.deprecated_in_use():
        log.warning("  deprecated env name in use: %s — rename it to %s (the fallback goes away next release)",
                    old, new)
    # Resolved protocols are config, not secrets — name them so a misbehaving endpoint
    # is diagnosable from the boot log alone.
    proto = "mlx" if os.environ.get("DREAM_TOOL_PROTOCOL", "").strip().lower() == "mlx" else "openai"
    log.info("  dream/coder tool protocol: %s", proto)
    img_proto = "vera" if os.environ.get("IMAGE_PROTOCOL", "").strip().lower() == "vera" else "openai"
    log.info("  image-gen protocol: %s", img_proto)
    if os.environ.get("VERA_CHAT_TEMPLATE_KWARGS", "").strip():
        log.info("  primary chat: server-specific template kwargs configured")
    log.info("integrations (runtime state — env seeds, the store carries edits):")
    try:
        from routers import integrations
        for line in integrations.report_lines():
            log.info("%s", line)
    except Exception as e:  # noqa: BLE001 — the report must never block startup
        log.warning("  integration table unavailable: %s", e)
