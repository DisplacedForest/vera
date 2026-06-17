"""Signals vein — a structured external-watch monitor.

Hybrid design: **quantitative live feeds drive the trip; an LLM only explains what tripped.**
Thresholds are pre-declared module constants (auditable, never LLM-chosen). Live data only — a
collector that can't reach its source is skipped and surfaced in `errors[]`, never replaced by a
fabricated or default reading. Silent by default: no trips -> no card -> the vein stays "quiet".

WHAT the vein watches for is config, not code: SIGNALS_NEWS_QUERIES picks the news beats,
SIGNALS_ORIENTATION sets the household's bar for "worth surfacing", and SIGNALS_SENTINELS selects
which collectors run (the keyless US-centric sources auto-gate on the home region).

Four units:
  1. Collectors  — one async fn per live source, returns normalized readings (own try/except).
  2. Evaluator   — pure functions mapping readings -> trips, each tagged tier + reason + sources.
  3. Sentinels   — economic (numeric, multi-factor) and democratic-stability (anchored LLM judge).
  4. Composer    — one LLM call writes the human card body from the trips it is handed; it never
                   decides whether to fire. Max trip tier -> vein severity; posts via _inject.

Tiers map to pulse vein severities: notice < alert < critical. Critical is the loud banner and may
only come from the two sentinels.
"""

import csv
import io
import json
import math
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import env_compat
from .pulse import DEFAULT_FOLDER, _inject, _vera, store
from .persona import home_region_is_us, orientation, owner, voiced
from .websearch import SearchRequest, search as web_search

router = APIRouter()

# ---- home anchor (mirrors weather.py; all from env — near-home logic gates on these) ----
def _coord(name: str) -> float | None:
    v = os.environ.get(name, "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


HOME_LAT = _coord("WEATHER_LAT")
HOME_LON = _coord("WEATHER_LON")
HOME_STATE = os.environ.get("HOME_STATE", "").strip()  # 2-letter US state for FEMA declarations
NEAR_KM = float(os.environ.get("SIGNALS_NEAR_KM", "600"))

# ---- optional API keys (collectors gate on these and skip cleanly when absent) ----
FRED_API_KEY = env_compat.read("FRED_KEY")
EIA_API_KEY = env_compat.read("EIA_KEY")

# ---- pre-declared thresholds (the audit surface — tuned via env, never by an LLM) ----
def _envf(name: str, default: str) -> float:
    try:
        return float(os.environ.get(name, default) or default)
    except ValueError:
        return float(default)


QUAKE_GLOBAL_MIN = _envf("SIGNALS_QUAKE_GLOBAL_MIN", "7.0")   # M>= this anywhere -> notice (M6 is ~2-day-cadence noise; M7 is ~15/yr)
QUAKE_NEAR_MIN = _envf("SIGNALS_QUAKE_NEAR_MIN", "5.0")       # M>= this within NEAR_KM of home -> alert (nearby beats far away)
YIELD_INVERSION_PCT = _envf("SIGNALS_YIELD_INVERSION_PCT", "-0.40")  # (10y - 2y) <= this (pct points) counts as a deep-inversion factor
VIX_REGIME = _envf("SIGNALS_VIX_REGIME", "30.0")              # VIX >= this counts as a stress-regime factor
HY_SPREAD_PCT = _envf("SIGNALS_HY_SPREAD_PCT", "8.0")         # high-yield OAS (pct) >= this counts as a credit-blowout factor (FRED-gated)
ECON_CRITICAL_FACTORS = int(_envf("SIGNALS_ECON_CRITICAL_FACTORS", "2"))  # >= this many economic factors true -> critical
EIA_RESPONDENT = os.environ.get("EIA_RESPONDENT", "").strip()  # the home grid's balancing authority (e.g. MISO)
GRID_DEV_NOTICE = _envf("SIGNALS_GRID_DEV_NOTICE", "5.0")  # actual demand this % over day-ahead forecast -> notice (finalized hours run mean ~-2%, stdev ~2.5%, weekly max ~+2.4% — the forecast biases high)
GRID_DEV_ALERT = _envf("SIGNALS_GRID_DEV_ALERT", "8.0")    # ...this % over -> alert (grid carrying materially more load than planned)
NEWS_MIN_SEVERITY = int(_envf("SIGNALS_NEWS_MIN_SEVERITY", "4"))  # judged candidates below this never trip (4 -> notice, 5 -> alert; a normal week's candidates sit at <= 3)

TIER_RANK = {"notice": 1, "alert": 2, "critical": 3}
SIGNALS_LOG = os.environ.get("SIGNALS_LOG_PATH", "/data/signals_log.jsonl")  # threshold-calibration dataset

UA = {"User-Agent": "Mozilla/5.0 (compatible; vera-signals/1.0)"}  # bare UAs get bot-walled (Yahoo/Treasury)


def _env_list(name: str, default: list[str], sep: str = ";") -> list[str]:
    """A list-valued env var: `sep`-separated entries, falling back to the default when unset."""
    raw = os.environ.get(name, "").strip()
    return [p.strip() for p in raw.split(sep) if p.strip()] if raw else default


# The news beats the qualitative sentinel sweeps — ';'-separated in SIGNALS_NEWS_QUERIES.
# The defaults are deliberately region-neutral; point them at whatever your household watches.
NEWS_QUERIES = _env_list("SIGNALS_NEWS_QUERIES", [
    "major supply chain disruption, port closure, fuel or food shortage latest news",
    "major natural or industrial disaster latest news",
    "major geopolitical or military escalation latest news",
])

def effective_orientation() -> str:
    """The household's bar for "worth surfacing": the signals vein's stored option, else the
    SIGNALS_ORIENTATION env / neutral default. Shared with the journal's recheck judge — one bar."""
    from . import pulse_veins
    return (str(pulse_veins.option_values("signals").get("orientation") or "").strip()
            or orientation())


# News judge: turns the qualitative corpus into structured candidates. It JUDGES significance but
# cannot manufacture a critical trigger — the democratic-stability critical tier requires a citable
# primary-source act (anchored=true with an anchor_url). Everything else caps at alert/notice.
# The household's bar comes from the vein orientation option; the silent-by-default calibration
# is fixed.
def news_judge_sys(orient: str) -> str:
    return (
        f"You're screening world events for {owner()}'s household. You are "
        f"calibrated to be SILENT by default. A candidate qualifies ONLY if it would plausibly {orient}. "
        "Routine politics, market noise, single-source "
        "rumor, speculation, or an ongoing situation with no materially NEW escalation DO NOT qualify; require "
        "corroboration across multiple sources.\n"
        "Classify each into category: 'democratic' (rule-of-law / constitutional / emergency-powers), "
        "'supply' (supply-chain / shortage), 'severe' (natural/industrial disaster), or 'geopolitical'.\n"
        "severity 1-5: 1-3 = ignore, 4 = significant + actionable, 5 = major + immediate relevance.\n"
        "For 'democratic' ONLY: set anchored=true and give anchor_url ONLY when the event is a concrete, "
        "verifiable official act (an actual executive order, court ruling, or official government action) — "
        "NOT op-eds, sentiment, or 'experts warn'. If it is only commentary/sentiment, anchored=false.\n"
        'Return ONLY JSON: {"candidates":[{"category":"...","headline":"...","severity":N,'
        '"corroborated":true|false,"anchored":true|false,"anchor_url":"...|null","why":"...","prep":"...",'
        '"sources":[{"title":"...","url":"..."}]}]}. Be ruthless: on a normal week every item is severity <= 3.'
    )

# Clustering: partition today's trips into DISTINCT situations — one card each — merging only trips
# about the SAME underlying event (e.g. a quake and its own disaster alert).
CLUSTER_SYS = (
    "Group today's vetted signals into DISTINCT situations — one per genuinely separate event. "
    "Merge ONLY trips about the SAME underlying event (e.g. an earthquake and the disaster alert for that "
    "same quake, or a strait closure and its own supply-shock trip). Keep separate events separate even in "
    "one region — a strait closure, a downed aircraft, and a broader war are THREE situations, not one.\n"
    'Return ONLY JSON: {"situations":[{"headline":"3-6 word noun phrase, no trailing punctuation",'
    '"members":[<trip index>,...],"query":"a focused web search to deepen THIS situation"}]}.'
)


class SignalsRequest(BaseModel):
    inject: bool = True  # false = assess only, never post a card (tuning mode)
    pulse_folder_id: str | None = None
    skip_news: bool = False  # quantitative-only run (skips the LLM news judge)


# --------------------------------------------------------------------------- helpers


async def _get_json(session, url, **kw):
    async with session.get(url, headers=UA, timeout=aiohttp.ClientTimeout(total=25), **kw) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


async def _get_text(session, url, **kw):
    async with session.get(url, headers=UA, timeout=aiohttp.ClientTimeout(total=25), **kw) as r:
        r.raise_for_status()
        return await r.text()


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _src(title, url):
    return {"title": str(title)[:140], "url": url}


def _log_run(out, req):
    """Append one structured line per run for threshold calibration. Best-effort: a logging
    failure must never break the endpoint. Records readings + trip tiers, not the news corpus/body."""
    try:
        rec = {
            "ts": int(time.time()),
            "iso": datetime.now(timezone.utc).isoformat(),
            "inject": req.inject, "skip_news": req.skip_news,
            "severity": out.get("severity"),
            "errors": out.get("errors"),
            "readings": out.get("readings"),
            "trips": [{"sentinel": t["sentinel"], "tier": t["tier"], "title": t["title"]}
                      for t in out.get("trips", [])],
            "considered": [{"category": c.get("category"), "severity": c.get("severity"),
                            "corroborated": c.get("corroborated"), "anchored": c.get("anchored")}
                           for c in out.get("considered", [])],
        }
        with open(SIGNALS_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        out.setdefault("errors", []).append(f"log: {e}")


# --------------------------------------------------------------------------- collectors
# Each returns a normalized dict; on failure raises (caller records the error, no fabrication).


async def _collect_usgs(session):
    """USGS FDSN — significant earthquakes in the last 48h (M>=4.5 to keep the payload small)."""
    start = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    url = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson"
           f"&minmagnitude=4.5&orderby=magnitude&starttime={start}")
    data = await _get_json(session, url)
    quakes = []
    for f in data.get("features", []):
        p, g = f.get("properties", {}), f.get("geometry", {})
        coords = (g.get("coordinates") or [None, None])[:2]
        lon, lat = (coords + [None, None])[:2]
        mag = p.get("mag")
        if mag is None or lat is None:
            continue
        # Distance from home is only known when the home anchor is configured; without it the
        # near-home alert tier is skipped and only the global-magnitude tier applies.
        dist = _haversine_km(HOME_LAT, HOME_LON, lat, lon) if HOME_LAT is not None and HOME_LON is not None else None
        quakes.append({"mag": float(mag), "place": p.get("place") or "unknown",
                       "url": p.get("url") or "https://earthquake.usgs.gov",
                       "dist_km": round(dist) if dist is not None else None})
    return {"quakes": quakes}


async def _collect_gdacs(session):
    """GDACS — recent global disaster alerts with alertlevel (Green/Orange/Red)."""
    url = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"
    data = await _get_json(session, url)
    events = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        level = (p.get("alertlevel") or "").strip().lower()
        if level not in ("orange", "red"):
            continue
        name = p.get("eventname") or p.get("name") or p.get("htmldescription") or p.get("eventtype") or "event"
        link = p.get("url")
        if isinstance(link, dict):
            ref = link.get("report") or link.get("details")
        elif isinstance(link, str):
            ref = link
        else:
            ref = None
        events.append({"level": level, "type": p.get("eventtype") or "?",
                       "name": str(name)[:160], "url": ref or "https://www.gdacs.org"})
    return {"events": events}


async def _collect_fema(session):
    """OpenFEMA — major disaster declarations in the home state within the last 30 days.
    Gated on HOME_STATE; skipped (empty reading, not an error) if absent."""
    if not HOME_STATE:
        return {"declarations": []}
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    qs = urllib.parse.urlencode({
        "$filter": f"state eq '{HOME_STATE}' and declarationDate gt '{since}'",
        "$orderby": "declarationDate desc", "$top": "10"})
    url = f"https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?{qs}"
    data = await _get_json(session, url)
    decls = []
    seen = set()
    for d in data.get("DisasterDeclarationsSummaries", []):
        key = d.get("femaDeclarationString") or d.get("disasterNumber")
        if key in seen:
            continue
        seen.add(key)
        num = d.get("disasterNumber")
        decls.append({"title": d.get("declarationTitle") or "declaration",
                      "incident": d.get("incidentType") or "?", "date": d.get("declarationDate") or "",
                      "url": f"https://www.fema.gov/disaster/{num}" if num else "https://www.fema.gov"})
    return {"declarations": decls}


async def _collect_federal_register(session):
    """Federal Register — presidential documents in the last 30 days touching national emergency."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    url = ("https://www.federalregister.gov/api/v1/documents.json?per_page=30&order=newest"
           "&conditions[type][]=PRESDOCU"
           f"&conditions[publication_date][gte]={since}")
    data = await _get_json(session, url)
    docs = []
    for d in data.get("results", []):
        title = d.get("title") or ""
        text = f"{title} {d.get('abstract','') or ''}".lower()
        # NEW national-emergency declarations only — skip the routine annual "Continuation of the
        # National Emergency..." renewals, which would otherwise post a notice every cycle.
        if "national emergency" not in text or "continuation of the national emergency" in title.lower():
            continue
        docs.append({"title": d.get("title") or "presidential document",
                     "date": d.get("publication_date") or "",
                     "url": d.get("html_url") or "https://www.federalregister.gov"})
    return {"docs": docs}


async def _collect_treasury_yield(session):
    """US Treasury daily par yield curve (keyless CSV) — latest 2y and 10y."""
    year = datetime.now(timezone.utc).year
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
           f"{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
    text = await _get_text(session, url)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty treasury csv")
    rows.sort(key=lambda r: r.get("Date", ""), reverse=True)
    latest = rows[0]

    def _num(key):
        v = (latest.get(key) or "").strip()
        return float(v) if v not in ("", "N/A") else None

    two, ten = _num("2 Yr"), _num("10 Yr")
    if two is None or ten is None:
        raise ValueError("missing 2y/10y in treasury csv")
    return {"date": latest.get("Date"), "two_yr": two, "ten_yr": ten,
            "spread_2s10s": round(ten - two, 3)}


async def _collect_vix(session):
    """CBOE VIX latest daily close via Yahoo's keyless chart API (stooq is bot-walled)."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d"
    data = await _get_json(session, url)
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise ValueError("no yahoo chart result")
    closes = [c for c in result["indicators"]["quote"][0].get("close", []) if c is not None]
    if not closes:
        raise ValueError("no vix close")
    return {"vix": float(closes[-1])}


async def _collect_fred_hy(session):
    """High-yield OAS (credit spread) from FRED — gated on FRED_API_KEY; skipped if absent."""
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY not set")
    url = ("https://api.stlouisfed.org/fred/series/observations?series_id=BAMLH0A0HYM2"
           f"&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit=1")
    data = await _get_json(session, url)
    obs = data.get("observations", [])
    if not obs or obs[0].get("value") in (None, ".", ""):
        raise ValueError("no FRED observation")
    return {"hy_oas_pct": float(obs[0]["value"]), "date": obs[0].get("date")}


def _grid_hour(by_period, window=6):
    """Pick the hour the grid reading reports. The newest published hour is often provisional —
    actual demand mirrors the day-ahead forecast exactly until the real value lands — so leading
    exact mirrors are skipped (capped at 3: a longer flat run is data, not publication lag).
    Among the next `window` finalized hours, return the max-positive-deviation hour, so a real
    load excursion is never masked by a provisional 0%. None when no complete hour exists."""
    hours = []
    for period in sorted(by_period, reverse=True):
        v = by_period[period]
        if v.get("D") and v.get("DF"):
            hours.append((period, v["D"], v["DF"]))
    skip = 0
    while skip < min(3, len(hours) - 1) and hours[skip][1] == hours[skip][2]:
        skip += 1
    hours = hours[skip:skip + window]
    if not hours:
        return None
    period, d, df = max(hours, key=lambda h: h[1] / h[2])
    return {"period": period, "demand_mw": d, "forecast_mw": df,
            "dev_pct": round((d / df - 1) * 100, 1)}


async def _collect_eia_grid(session):
    """EIA hourly grid data for our balancing authority — actual demand vs day-ahead forecast.
    Gated on EIA_API_KEY; skipped if absent. EIA has no 'emergency declared' flag, so this is a
    load-stress proxy: actual demand running materially above what was forecast = thinner reserves
    than planned. The reported hour is _grid_hour's pick over the recent finalized window."""
    if not EIA_API_KEY:
        raise ValueError("EIA_API_KEY not set")
    if not EIA_RESPONDENT:
        raise ValueError("EIA_RESPONDENT not set")
    url = ("https://api.eia.gov/v2/electricity/rto/region-data/data/"
           f"?api_key={EIA_API_KEY}&frequency=hourly&data[0]=value"
           f"&facets[respondent][]={EIA_RESPONDENT}&facets[type][]=D&facets[type][]=DF"
           "&sort[0][column]=period&sort[0][direction]=desc&length=48")
    data = await _get_json(session, url)
    rows = (data.get("response", {}) or {}).get("data", [])
    by_period = {}
    for r in rows:
        if r.get("value") is None:
            continue
        by_period.setdefault(r["period"], {})[r["type"]] = float(r["value"])
    hour = _grid_hour(by_period)
    if hour is None:
        raise ValueError("no hour with both demand and forecast")
    return {"respondent": EIA_RESPONDENT, **hour}


async def _collect_news(session):
    """Qualitative corpus for the soft signals (democratic / supply / severe / geopolitical)."""
    parts = []
    for q in NEWS_QUERIES:
        res = await web_search(SearchRequest(query=q, fetch_pages=2, max_results=4))
        parts.append(f"## Query: {q}\n" + "\n".join(
            f"- {r.title} ({r.url})\n  {r.content[:600]}" for r in res.results))
    return {"corpus": "\n\n".join(parts)}


# Collector registry: name -> (fn, us_centric). The us_centric sources read US-specific data
# (FEMA declarations, the Federal Register, Treasury yields, VIX) and run by default only when
# the home region is US; everything is overridable via the SIGNALS_SENTINELS allowlist.
COLLECTORS = {
    "usgs":             (_collect_usgs, False),
    "gdacs":            (_collect_gdacs, False),
    "fema":             (_collect_fema, True),
    "federal_register": (_collect_federal_register, True),
    "treasury":         (_collect_treasury_yield, True),
    "vix":              (_collect_vix, True),
    "fred_hy":          (_collect_fred_hy, False),
    "eia_grid":         (_collect_eia_grid, False),
}


def enabled_sentinels(allow: str | None = None, us: bool | None = None,
                      fred_key: str | None = None, eia_ok: bool | None = None) -> set[str]:
    """Which collectors run this check. SIGNALS_SENTINELS (comma-separated collector names) is
    an explicit allowlist; unset means every globally-valid source, plus the US-centric set when
    the home region is US, minus the key-gated sources whose keys are absent (skipped quietly —
    an unconfigured key is a choice, not an error)."""
    allow = os.environ.get("SIGNALS_SENTINELS", "") if allow is None else allow
    names = {p.strip() for p in allow.split(",") if p.strip()}
    if names:
        return names & set(COLLECTORS)
    us = home_region_is_us() if us is None else us
    out = {n for n, (_, us_centric) in COLLECTORS.items() if not us_centric or us}
    if not (FRED_API_KEY if fred_key is None else fred_key):
        out.discard("fred_hy")
    if not ((EIA_API_KEY and EIA_RESPONDENT) if eia_ok is None else eia_ok):
        out.discard("eia_grid")
    return out


# Vein source groups -> collector sets (the Veins pane's scoping toggles).
GROUP_COLLECTORS = {
    "grp_financial": {"treasury", "vix", "fred_hy"},
    "grp_geophysical": {"usgs", "gdacs"},
    "grp_civic": {"fema", "federal_register"},
    "grp_grid": {"eia_grid"},
}


def vein_sentinels(opts: dict | None = None, fred_key: str | None = None,
                   eia_ok: bool | None = None) -> set[str] | None:
    """The collector set the vein's stored source-group choices select, or None when the
    deployment has made no explicit choice (the env/region auto-gating applies instead).
    Key-gated members still skip quietly when their keys are absent."""
    from . import pulse_veins
    if opts is None:
        if not pulse_veins.has_stored_options("signals"):
            return None
        opts = pulse_veins.option_values("signals")
    allow: set[str] = set()
    for gid, names in GROUP_COLLECTORS.items():
        if opts.get(gid):
            allow |= names
    if not (FRED_API_KEY if fred_key is None else fred_key):
        allow.discard("fred_hy")
    if not ((EIA_API_KEY and EIA_RESPONDENT) if eia_ok is None else eia_ok):
        allow.discard("eia_grid")
    return allow


# --------------------------------------------------------------------------- evaluators (pure)


def _eval_quakes(readings):
    trips = []
    for q in readings.get("quakes", []):
        if q["dist_km"] is not None and q["dist_km"] <= NEAR_KM and q["mag"] >= QUAKE_NEAR_MIN:
            trips.append({"sentinel": "earthquake", "tier": "alert",
                          "title": f"M{q['mag']:.1f} earthquake {q['dist_km']} km away",
                          "detail": f"{q['place']}, within {NEAR_KM:.0f} km of home.",
                          "sources": [_src(q["place"], q["url"])]})
        elif q["mag"] >= QUAKE_GLOBAL_MIN:
            trips.append({"sentinel": "earthquake", "tier": "notice",
                          "title": f"M{q['mag']:.1f} earthquake · {q['place']}",
                          "detail": "Major global seismic event.",
                          "sources": [_src(q["place"], q["url"])]})
    return trips


def _eval_gdacs(readings):
    trips = []
    for e in readings.get("events", []):
        tier = "alert" if e["level"] == "red" else "notice"
        trips.append({"sentinel": "disaster", "tier": tier,
                      "title": f"GDACS {e['level']} · {e['type']}: {e['name']}",
                      "detail": "Global disaster alert.",
                      "sources": [_src(e["name"], e["url"])]})
    return trips


def _eval_fema(readings):
    return [{"sentinel": "fema", "tier": "alert",
             "title": f"FEMA declaration ({HOME_STATE}): {d['title']}",
             "detail": f"{d['incident']} declared {d['date']}.",
             "sources": [_src(d["title"], d["url"])]}
            for d in readings.get("declarations", [])]


def _eval_federal_register(readings):
    return [{"sentinel": "federal_register", "tier": "notice",
             "title": f"Emergency-related presidential document: {d['title']}",
             "detail": f"Published {d['date']}.",
             "sources": [_src(d["title"], d["url"])]}
            for d in readings.get("docs", [])]


def _eval_grid(reading):
    """Grid load-stress proxy -> notice/alert (never critical). Only positive deviations matter
    (actual exceeding forecast = more load than planned)."""
    if not reading:
        return []
    dev = reading.get("dev_pct")
    if dev is None or dev < GRID_DEV_NOTICE:
        return []
    tier = "alert" if dev >= GRID_DEV_ALERT else "notice"
    src = _src(f"EIA grid monitor ({reading['respondent']})",
               f"https://www.eia.gov/electricity/gridmonitor/dashboard/electric_overview/balancing_authority/{reading['respondent']}")
    return [{"sentinel": "grid", "tier": tier,
             "title": f"{reading['respondent']} grid load {dev:+.0f}% over forecast",
             "detail": f"Actual demand {reading['demand_mw']:.0f} MW vs {reading['forecast_mw']:.0f} MW "
                       f"forecast ({reading['period']}), carrying more load than planned.",
             "sources": [src]}]


def _eval_economic(treasury, vix, hy):
    """Multi-factor economic sentinel — one strategy among many possible: independent stress
    factors (deep yield inversion, volatility regime, credit blowout) must COINCIDE before the
    critical tier fires. Factor thresholds and the coincidence count (ECON_CRITICAL_FACTORS)
    are env-tuned. >= ECON_CRITICAL_FACTORS true -> one critical trip."""
    factors, srcs = [], []
    if treasury and treasury.get("spread_2s10s") is not None:
        if treasury["spread_2s10s"] <= YIELD_INVERSION_PCT:
            factors.append(f"2s10s yield inversion at {treasury['spread_2s10s']:+.2f} pts")
            srcs.append(_src("US Treasury daily par yield curve",
                             "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"))
    if vix and vix.get("vix") is not None and vix["vix"] >= VIX_REGIME:
        factors.append(f"VIX stress regime at {vix['vix']:.1f}")
        srcs.append(_src("CBOE VIX", "https://www.cboe.com/tradable_products/vix/"))
    if hy and hy.get("hy_oas_pct") is not None and hy["hy_oas_pct"] >= HY_SPREAD_PCT:
        factors.append(f"high-yield credit spread blowout at {hy['hy_oas_pct']:.2f}%")
        srcs.append(_src("ICE BofA High Yield OAS (FRED)", "https://fred.stlouisfed.org/series/BAMLH0A0HYM2"))
    if len(factors) >= ECON_CRITICAL_FACTORS:
        return [{"sentinel": "economic", "tier": "critical",
                 "title": "Economic stress: multiple factors tripped",
                 "detail": "; ".join(factors) + ".", "sources": srcs}]
    return []


def _eval_news_candidates(cands):
    """Map judged news candidates to trips. Democratic critical requires an anchored official act;
    everything else caps at alert; supply/severe/geopolitical never reach critical."""
    trips = []
    for c in cands:
        if not c.get("corroborated") or (c.get("severity") or 0) < NEWS_MIN_SEVERITY:
            continue
        cat, sev = c.get("category"), c.get("severity") or 0
        srcs = [_src(s.get("title", "source"), s.get("url", "")) for s in (c.get("sources") or []) if s.get("url")]
        if cat == "democratic" and c.get("anchored") and c.get("anchor_url") and sev >= 5:
            tier = "critical"
            if c.get("anchor_url") not in [s["url"] for s in srcs]:
                srcs.insert(0, _src("Primary-source act", c["anchor_url"]))
        elif sev >= 5 and cat in ("supply", "severe", "geopolitical"):
            tier = "alert"
        else:
            tier = "alert" if sev >= 5 else "notice"
        trips.append({"sentinel": cat or "news", "tier": tier,
                      "title": c.get("headline", "signal"),
                      "detail": (c.get("why") or "") + (f" Prep: {c['prep']}" if c.get("prep") else ""),
                      "sources": srcs})
    return trips


# --------------------------------------------------------------------------- composer


# Each card is ONE situation's focused, cited briefing. With SIGNALS_IMPACT_GOODS on,
# supply/geopolitical situations also get the grounded "Affected goods" line.
SIGNAL_CARD_SYS = (
    f"Write a focused signal briefing for {owner()} about ONE situation. NO alarmism. Ground every claim "
    "in the numbered sources and cite them inline as [n].\n"
    "Output EXACTLY this format and nothing else:\n"
    "SUMMARY: <one complete sentence, <= 24 words, previewing the situation>\n"
    "===\n"
    "<body: 2-4 sentences of GitHub-flavored markdown — what happened (concrete: who / where / scale / "
    "numbers drawn from the sources), why it matters for the household, and what (if anything) to do "
    "about it. {impact_instr}End with a single 'Watching:' line — a plain comma-separated list of 1-4 "
    "specific things to keep monitoring (no JSON, no brackets, just short noun phrases).>\n"
    "No severity labels, no preamble before SUMMARY."
)
# Goods-impact line on supply/geopolitical cards — an orientation choice, off unless the vein's
# impact_goods option (seeded by SIGNALS_IMPACT_GOODS) asks for it.
IMPACT_INSTR = (
    "Then, on its own new line, write the bold label '**Affected goods:**' followed by a "
    "comma-separated list of the concrete goods CATEGORIES the sources implicate — the actual commodities / "
    "fuels / food groups they flag, derived from the sources; do not invent specifics or reference any "
    "household inventory. "
)


def _numbered(sources):
    return "\n".join(f"[{s['n']}] {s['title']}: {(s.get('content') or '')[:500]}" for s in sources)


async def _compose_signal(headline, members, sources, deepen):
    """Write ONE situation's (summary, body, watch) from its member trips and researched sources.
    `deepen` flags a supply/geopolitical situation that also gets the grounded 'Affected goods' line.
    `watch` is the "Watching:" line's topics — the material her journal authoring pass receives."""
    sysp = SIGNAL_CARD_SYS.format(impact_instr=(IMPACT_INSTR if deepen else ""))
    usr = (f"Situation: {headline}\n\nVetted signal facts:\n"
           + json.dumps([{"title": m["title"], "detail": m["detail"]} for m in members], indent=2)
           + "\n\nNumbered sources:\n" + _numbered(sources))
    raw = (await _vera([{"role": "system", "content": voiced(sysp)}, {"role": "user", "content": usr}],
                       temperature=0.4)).strip()
    head, sep, rest = raw.partition("===")
    if sep:
        m = re.search(r"SUMMARY:\s*(.+)", head)
        summary = m.group(1).strip().strip('"') if m else None
        body = rest.strip()
    else:  # model ignored the format — salvage: strip a leading SUMMARY line, use the rest as body
        summary = None
        body = re.sub(r"^\s*SUMMARY:.*\n?", "", raw).strip()
    # Force the trailing labels onto their own paragraphs — single newlines render as spaces in markdown,
    # so without a blank line "Affected goods…" and "Watching:" run inline into the prose.
    body = re.sub(r"\s*\*\*Affected goods\b", "\n\n**Affected goods", body)
    body = re.sub(r"\s*(?<!\*)\bWatching:", "\n\nWatching:", body)
    # Parse the human "Watching:" line deterministically (no model JSON — the model conflates an
    # inline JSON block with the prose line and leaks it into the card).
    watch = []
    mline = re.search(r"(?im)^\s*Watching:\s*(.+)$", body)
    if mline:
        watch = [t for t in (it.strip().strip(".").strip()
                             for it in re.split(r",|;|\band\b", mline.group(1)))
                 if t and len(t) <= 60][:4]
    return (summary.replace("\n", " ") if summary else None), body.strip(), watch


# --------------------------------------------------------------------------- endpoint


@router.post("/signals/check", tags=["signals"])
async def check(req: SignalsRequest):
    from . import pulse_veins
    if not pulse_veins.is_enabled("signals"):
        return {"ok": False, "disabled": True, "detail": pulse_veins.gate_reason("signals")}
    vein_opts = pulse_veins.option_values("signals")
    folder = req.pulse_folder_id or DEFAULT_FOLDER
    out = {"ok": True, "trips": [], "injected": False, "severity": None, "errors": [],
           "readings": {}, "considered": []}

    async with aiohttp.ClientSession() as session:
        # 1) collectors — only the enabled sentinels run; each isolated; a failure is
        # recorded, never fabricated. Skipped sentinels are surfaced so a quiet vein is legible.
        # The vein's stored source-group choices are the authority; with none stored, the
        # env/region auto-gating decides.
        enabled = vein_sentinels()
        if enabled is None:
            enabled = enabled_sentinels()
        skipped = sorted(set(COLLECTORS) - enabled)
        if skipped:
            out["skipped_sentinels"] = skipped
        readings = {}
        for name, (fn, _) in COLLECTORS.items():
            if name not in enabled:
                continue
            try:
                readings[name] = await fn(session)
            except Exception as e:
                out["errors"].append(f"{name}: {e}")
        out["readings"] = readings

        # 2) quantitative evaluation (pure)
        trips = []
        trips += _eval_quakes(readings.get("usgs", {}))
        trips += _eval_gdacs(readings.get("gdacs", {}))
        trips += _eval_fema(readings.get("fema", {}))
        trips += _eval_federal_register(readings.get("federal_register", {}))
        trips += _eval_grid(readings.get("eia_grid"))
        trips += _eval_economic(readings.get("treasury"), readings.get("vix"), readings.get("fred_hy"))

        # 3) qualitative sentinel (news judge) — democratic / supply / severe / geopolitical.
        # Off when the vein's news group is toggled off (an explicit stored choice).
        news_off = pulse_veins.has_stored_options("signals") and not vein_opts.get("grp_news")
        if not req.skip_news and not news_off:
            try:
                corpus = (await _collect_news(session))["corpus"]
                if corpus.strip():
                    raw = await _vera(
                        [{"role": "system", "content": news_judge_sys(effective_orientation())},
                         {"role": "user", "content": f"Today: {time.strftime('%Y-%m-%d')}.\n\n{corpus}"}],
                        temperature=0.2)
                    parsed = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
                    cands = parsed.get("candidates", []) if isinstance(parsed, dict) else []
                    out["considered"] = cands
                    trips += _eval_news_candidates(cands)
            except Exception as e:
                out["errors"].append(f"news: {e}")

    out["trips"] = trips
    if not trips:
        _log_run(out, req)
        return out  # silent baseline — vein stays "quiet"

    # 4) emit ONE card per distinct situation: cluster the trips, then research and write each on its own.
    top = max(trips, key=lambda t: TIER_RANK.get(t["tier"], 0))["tier"]
    out["severity"] = top
    if not req.inject:
        _log_run(out, req)
        return out  # assess-only (tuning) mode

    # Robust no-dup: rebuild the vein each run — drop the current active signals cards, then post one
    # fresh card per distinct situation, so the vein always shows exactly this run's events and never
    # stacks. (Bookmarked/promoted cards are preserved; LLM clustering is unstable run-to-run, so we
    # don't try to match situations across runs — we just rebuild.)
    for c in [x for x in store.list_cards()
              if x.get("kind") == "signals" and x.get("status") in ("new", "seen")
              and not (x.get("category") or "").startswith("watch:")]:  # watch-update cards are not ours to sweep
        store.delete_card(c["id"])

    # cluster trips into distinct situations; fall back to one-per-trip if the LLM call/parse fails
    clusters = []
    try:
        listing = json.dumps([{"i": i, "sentinel": t["sentinel"], "tier": t["tier"],
                               "title": t["title"], "detail": t["detail"]} for i, t in enumerate(trips)], indent=2)
        raw = await _vera([{"role": "system", "content": CLUSTER_SYS},
                           {"role": "user", "content": f"Today's trips:\n{listing}"}], temperature=0.2)
        clusters = json.loads(raw[raw.index("{"):raw.rindex("}") + 1]).get("situations", [])
    except Exception as e:
        out["errors"].append(f"cluster: {e}")
    if not clusters:
        def _clip(s, n=48):
            s = s.strip()
            return s if len(s) <= n else (s[:n].rsplit(" ", 1)[0] or s[:n])
        clusters = [{"headline": _clip(t["title"]), "members": [i], "query": t["title"]}
                    for i, t in enumerate(trips)]

    out["cards"] = []
    for cl in clusters:
        members = [trips[i] for i in cl.get("members", []) if isinstance(i, int) and 0 <= i < len(trips)]
        if not members:
            continue
        tier = max((m["tier"] for m in members), key=lambda x: TIER_RANK.get(x, 0))
        deepen = bool(vein_opts.get("impact_goods")) and any(
            m.get("sentinel") in ("supply", "geopolitical") for m in members)
        headline = (cl.get("headline") or members[0]["title"]).strip().rstrip(".,;:… ")

        # research THIS situation: the cluster query, plus a goods-framed query when deepening
        sources, seen = [], set()
        for m in members:  # seed with the trips' own already-vetted sources
            for s in m.get("sources", []):
                if s.get("url") and s["url"] not in seen:
                    seen.add(s["url"])
                    sources.append({"n": len(sources) + 1, "title": s["title"], "url": s["url"], "content": ""})
        queries = [cl.get("query") or headline]
        if deepen:
            queries.append(f"{headline} which goods fuel food prices affected supply")
        for q in queries:
            try:
                rs = await web_search(SearchRequest(query=q, fetch_pages=2, max_results=4))
                for x in rs.results:
                    u = getattr(x, "url", None)
                    if u and u not in seen:
                        seen.add(u)
                        sources.append({"n": len(sources) + 1, "title": getattr(x, "title", "") or u,
                                        "url": u, "content": getattr(x, "content", "") or ""})
            except Exception as e:
                out["errors"].append(f"research {headline}: {e}")

        summary, body, watch = await _compose_signal(headline, members, sources, deepen)
        if not body:
            continue
        prefix = "Critical" if tier == "critical" else "Signal watch"
        title = f"{prefix} · {headline}"
        card_sources = [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in sources]
        res = await _inject(title, body, kind="signals", severity=tier,
                            sources=card_sources, summary=summary)
        out["cards"].append(res.get("id"))
        # Land the situation as a watch node: the store's cosine merge folds a repeat of a known
        # situation onto its node, two unrelated situations stay two nodes, and the Pulse pipeline
        # surfaces material changes from there.
        try:
            from . import editor
            await editor.author_watch(headline, facts=[body],
                                      resolve_condition=", ".join(watch) or None, origin="self")
        except Exception as e:
            out["errors"].append(f"journal {headline}: {e}")

    out["injected"] = bool(out["cards"])
    _log_run(out, req)
    return out
