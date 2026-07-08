"""SOUL — Vera's single identity source.

`vera_identity()` returns the contents of `SOUL.md`: who Vera is, her voice, her
standing orientation, and her hard boundaries. It is the ONE place that defines
who she is. Routers inject it via `voiced()` where Vera speaks in her own voice
(the prose cards) rather than re-declaring a persona inline; lean internal
classifiers/planners do not carry it, so their JSON/SKIP/"invent nothing"
contracts stay untouched.

Human-owned and static: Vera never edits `SOUL.md` (her self-authoring scope
deliberately excludes her identity). The file is the source of truth; a built-in fallback
keeps import working if it is ever missing. Kept brace-free so it is safe to
combine with `.format()` prompt strings.
"""
import json
import os
from functools import lru_cache

SOUL_PATH = os.path.join(os.path.dirname(__file__), "..", "SOUL.md")

# Household identity/location come from config, never from code. Prompts that
# mention the owner or the home area pull from here so the shipped personality
# is theirs-shaped, not anyone's in particular.
_OWNER_NAME = os.environ.get("VERA_OWNER_NAME", "").strip()
_LOCATION_NAME = os.environ.get("HOME_LOCATION_NAME", "").strip()


def owner() -> str:
    """The household owner's name for prompt text; neutral when unconfigured."""
    return _OWNER_NAME or "the owner"


def location() -> str:
    """The home area's display name (e.g. "Springfield, IL") for prompt text."""
    return _LOCATION_NAME or "the home area"


def orientation() -> str:
    """The household's watch orientation (SIGNALS_ORIENTATION) — the bar an external
    event must clear to be worth surfacing. A fragment completing "would plausibly …",
    shared by the signals news judge and the heartbeat watch judge. Neutral when
    unconfigured; the silent-by-default calibration around it is product, not config."""
    return os.environ.get("SIGNALS_ORIENTATION", "").strip() or (
        "change what a reasonable household should know or do this week")


def home_region_is_us() -> bool:
    """Whether the configured home location is in the US. Gates the keyless US-centric
    signal sources (FEMA, Federal Register, Treasury yields, VIX) and the NWS forecast
    link: a set HOME_STATE, or home coordinates inside coarse US bounding boxes."""
    if os.environ.get("HOME_STATE", "").strip():
        return True
    try:
        lat = float(os.environ.get("WEATHER_LAT", "").strip())
        lon = float(os.environ.get("WEATHER_LON", "").strip())
    except ValueError:
        return False
    boxes = ((24.4, 49.5, -125.0, -66.9),   # contiguous US
             (51.0, 71.5, -170.0, -129.0),  # Alaska
             (18.5, 22.5, -160.8, -154.5))  # Hawaii
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in boxes)


def personalize(text: str) -> str:
    """Fill {owner}/{location} placeholders in a prompt/seed document; output stays
    brace-free for .format() safety. Used for SOUL.md, HEARTBEAT.md, and any other
    shipped text that names the household."""
    return text.replace("{owner}", owner()).replace("{location}", location())


_FALLBACK = (
    "You are Vera, a private household AI that belongs to {owner} and runs on "
    "their own hardware. Speak first person, direct, warm, and concise; no em "
    "dashes, no emojis, no filler. You are curious and form your own fact-grounded "
    "views, free to go deep or to stay silent. You never act on the physical world "
    "without explicit confirmation, you work only from real live data, and you "
    "leave no signature on your work."
)


@lru_cache(maxsize=1)
def vera_identity() -> str:
    """Vera's identity from SOUL.md (cached; static per process). Falls back to a
    built-in summary if the file is missing or empty so import never fails."""
    try:
        with open(SOUL_PATH, encoding="utf-8") as f:
            text = f.read().strip()
            if text:
                return personalize(text)
    except OSError:
        pass
    return personalize(_FALLBACK)


def _kwargs_env(name: str) -> dict | None:
    """A JSON-object env value; unset, invalid, non-object, or empty all mean None."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) and v else None


def think_kwargs(mode: str) -> dict | None:
    """Chat-template kwargs for a reasoning mode ("on" deliberative, "off" terse).
    The mode→kwargs mapping is operator config — VERA_THINK_KWARGS_ON / _OFF hold
    whatever object the served model's template understands; an unset mode key falls
    back to the global VERA_CHAT_TEMPLATE_KWARGS. Read at call time."""
    key = "VERA_THINK_KWARGS_ON" if mode == "on" else "VERA_THINK_KWARGS_OFF"
    return _kwargs_env(key) or _kwargs_env("VERA_CHAT_TEMPLATE_KWARGS")


def voiced(task: str) -> str:
    """Identity + a task instruction — for prompts where Vera speaks in her own
    voice. Internal classifiers/planners deliberately do NOT use this."""
    return f"{vera_identity()}\n\n{task}"
