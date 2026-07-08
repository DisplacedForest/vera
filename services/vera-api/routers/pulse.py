"""Pulse — proactive overnight briefings.

One endpoint, POST /pulse/run, does the whole pipeline:
  cleanup sweep -> gather memories -> triage (Vera) -> per-topic search + synthesize
  -> inject one card per topic into the Pulse store.

The scheduler's only job is to trigger this each morning. All logic lives here, in Python.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from . import pulse_veins
from . import pulse_store as store
from . import user_profile_store as up
from . import vera_interests_store as vi
from .persona import think_kwargs, voiced
from .images import ImageSearchRequest, search as image_search
from .websearch import SearchRequest, search as web_search

router = APIRouter()
log = logging.getLogger("vera.pulse")

OWUI_BASE = os.environ.get("OWUI_BASE", "").rstrip("/")          # Open WebUI (memory, chat promotion)
OWUI_KEY = os.environ.get("OWUI_KEY", "")
VERA_BASE = os.environ.get("VERA_BASE", "").rstrip("/")          # main LLM, any OpenAI-compatible /v1
MODEL = os.environ.get("VERA_MODEL", "")
DEFAULT_FOLDER = os.environ.get("PULSE_FOLDER_ID", "")
VERA_IMAGE_BASE = os.environ.get("VERA_IMAGE_BASE", "")          # optional image-gen service; cards skip cover art without it
TZ = ZoneInfo(os.environ.get("HOME_TZ", "UTC"))  # untouched cards expire the day after creation (ChatGPT-Pulse daily freshness)

# The delivery contract per run: keep re-triaging (bounded rounds) until at least the floor
# of NOVEL cards lands; the ceiling caps a run no matter how much looks interesting. The
# dedup gate never loosens — a gated topic costs the run a retry, not a card.
PULSE_MIN_CARDS = int(os.environ.get("PULSE_MIN_CARDS", "2"))
PULSE_MAX_CARDS = int(os.environ.get("PULSE_MAX_CARDS", "8"))
PULSE_TRIAGE_ROUNDS = int(os.environ.get("PULSE_TRIAGE_ROUNDS", "3"))
# Variety guarantee within one run: at most this many research cards per standing interest.
PULSE_MAX_PER_INTEREST = int(os.environ.get("PULSE_MAX_PER_INTEREST", "1"))

# Optional warm-up/release hooks bracketing the end-of-run claim-audit phase. When set,
# the run POSTs the wake URL before auditing (so an on-demand audit model can load once
# for the whole batch) and the release URL after — unless the wake reply said the model
# was already up, in which case it is not this run's to release. Unset = no hook calls;
# an always-resident audit endpoint needs neither.
AUDIT_WAKE_URL = os.environ.get("AUDIT_WAKE_URL", "").strip()
AUDIT_RELEASE_URL = os.environ.get("AUDIT_RELEASE_URL", "").strip()

# Image generation resolves through the integrations registry's 'image_gen' entry
# (env-seeded by VERA_IMAGE_BASE / IMAGE_PROTOCOL, editable in the plugin store) — the
# same pattern as the coder's tool protocol. Protocol: the standard OpenAI Images API
# unless 'vera' selects the bespoke reference contract (style/steps/seed determinism +
# the vision pause/resume extension).
def _image_registry() -> dict:
    try:
        from . import integrations
        return integrations.integration("image_gen") or {}
    except Exception:
        return {}


def _image_base() -> str:
    """The image endpoint base — registry value, else VERA_IMAGE_BASE directly."""
    return (_image_registry().get("url") or VERA_IMAGE_BASE).rstrip("/")


def image_protocol() -> str:
    """The active image protocol: 'openai' unless the image_gen integration's protocol
    field (pinned by IMAGE_PROTOCOL when set in env) says 'vera'. Read at call time —
    the one place the flag is interpreted."""
    raw = _image_registry().get("protocol") or os.environ.get("IMAGE_PROTOCOL", "")
    return "vera" if raw.strip().lower() == "vera" else "openai"


def _parse_template_kwargs() -> dict | None:
    """Server-specific chat-template options (VERA_CHAT_TEMPLATE_KWARGS, JSON object —
    e.g. a hybrid-thinking toggle on llama.cpp/vLLM). Unset/invalid/empty means the
    chat request stays pure OpenAI: the field is omitted entirely."""
    raw = os.environ.get("VERA_CHAT_TEMPLATE_KWARGS", "").strip()
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) and v else None


CHAT_TEMPLATE_KWARGS = _parse_template_kwargs()

# Rotated per card so the feed feels fresh (ChatGPT-Pulse varies art styles deliberately).
STYLE_PALETTE = [
    "photorealistic product photography, soft natural daylight, shallow depth of field",
    "clean single continuous-line art on warm cream paper, minimal",
    "soft painterly gouache illustration, muted earthy palette",
    "mixed-media collage, torn paper and natural textures, editorial",
    "isometric 3D miniature diorama, tilt-shift, soft studio light",
    "flat vector illustration, bold simple shapes, muted retro palette",
]


class PulseRequest(BaseModel):
    interests: list[str] = []
    max_cards: int | None = None  # explicit cap; defaults to PULSE_MAX_CARDS and never exceeds it
    pulse_folder_id: str | None = None
    sweep_only: bool = False  # run just the cleanup sweep, skip generation
    user_id: str | None = None    # who this briefing is for (defaults to the household owner)
    user_name: str | None = None  # display name for the briefing voice


def _headers():
    return {"Authorization": f"Bearer {OWUI_KEY}", "Content-Type": "application/json"}


def _chat_payload(messages, temperature, think=None) -> dict:
    """The /chat/completions body — pure OpenAI unless template kwargs are configured.
    An explicit `think` mode ("on"/"off") resolves per-mode kwargs via persona.think_kwargs;
    no mode means the global kwargs."""
    p = {"model": MODEL, "stream": False, "temperature": temperature, "messages": messages}
    kwargs = think_kwargs(think) if think else CHAT_TEMPLATE_KWARGS
    if kwargs:
        p["chat_template_kwargs"] = kwargs
    return p


async def _vera(messages, temperature=0.4, think=None):
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{VERA_BASE}/chat/completions",
            json=_chat_payload(messages, temperature, think),
            timeout=aiohttp.ClientTimeout(total=300),
        ) as r:
            d = await r.json()
    return d["choices"][0]["message"]["content"]


def _parse_topics(txt):
    try:
        j = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        topics = j.get("topics")
        return topics if isinstance(topics, list) else []
    except Exception:
        return []


def _marker_body(c):
    """Encode a card's structure as vera-* markers + body, so a promoted OWUI chat is self-describing
    (the app reconstructs the rich card from these on load — sources, inline photos, tint, cover)."""
    m = []
    if c.get("image_url"):
        m.append(f"<!--vera-image {c['image_url']}-->")
    if c.get("tint"):
        m.append(f"<!--vera-tint {c['tint']}-->")
    if c.get("summary"):
        m.append(f"<!--vera-summary {c['summary']}-->")
    for s in (c.get("sources") or []):
        title_clean = re.sub(r"\s+", " ", str(s["title"]).replace("|", " ")).strip()
        m.append(f"<!--vera-source {s['n']}|{title_clean}|{s['url']}-->")
    for im in (c.get("inline_images") or []):
        m.append(f"<!--vera-inline {im['n']}|{im['url']}|{_clean_caption(im.get('caption', ''))}|{im.get('sourceN') or 0}-->")
    body = c.get("body", "")
    return ("\n".join(m) + "\n\n" + body) if m else body


async def _create_owui_chat(card):
    """Promotion: create a real OWUI chat (no folder) seeded with the marker-encoded card. Returns id."""
    mid = str(uuid.uuid4())
    ts = int(time.time())
    title = card.get("title", "")
    body = _marker_body(card)
    chat = {
        "id": "",
        "title": f"Pulse · {title}",
        "models": [MODEL],
        "params": {},
        "history": {
            "currentId": mid,
            "messages": {mid: {"id": mid, "parentId": None, "childrenIds": [], "role": "assistant",
                               "content": body, "timestamp": ts, "models": [MODEL]}},
        },
        "messages": [{"role": "assistant", "content": body}],
        "tags": [],
        "files": [],
        "timestamp": ts * 1000,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{OWUI_BASE}/api/v1/chats/new",
            headers=_headers(),
            json={"chat": chat},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            obj = await r.json()
    return obj.get("id")


async def _inject(title, body, folder_id=None, image_url=None, tint=None, sources=None,
                  summary=None, inline_images=None, action=None, kind="research", severity=None,
                  user_id=None, provenance="scheduled", category=None, change_set=None, items=None):
    """Store a Pulse card. Compat shim for the helper routers (health/signals/kitchen/weather/
    heartbeat) that surface cards. `folder_id` is ignored — Pulse is store-backed.
    `kind`/`severity` place the card in an ambient vein; default is the research feed.
    `user_id` is the person the card is FOR; defaults to the household owner.
    `provenance` records how the card was triggered — "scheduled" vs "heartbeat".
    `items` carries a multi-item digest's per-row actions. Returns the new card id."""
    src = [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in (sources or [])]
    imgs = [{"n": i, "url": im["url"], "caption": im.get("caption", ""), "sourceN": im.get("srcN", 0)}
            for i, im in enumerate(inline_images or [], start=1)]
    cid = str(uuid.uuid4())
    store.insert_card({
        "id": cid, "created_at": int(time.time()),
        "day": datetime.now(TZ).date().isoformat(), "status": "new",
        "title": title, "summary": summary or "", "body": body,
        "image_url": image_url, "tint": tint, "sources": src, "inline_images": imgs,
        "promoted_chat_id": None, "action": action, "kind": kind, "severity": severity,
        "user_id": user_id or store.DEFAULT_USER, "provenance": provenance,
        "category": category, "change_set": change_set, "items": items,
    })
    return {"ok": True, "id": cid}


async def _get_memories():
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{OWUI_BASE}/api/v1/memories/",
            headers=_headers(),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            return await r.json()


TRIAGE_SYS = (
    'You\'re planning {who}\'s proactive morning briefing ("Pulse") for {today}. '
    "From their standing interests and what you know about them, pick AT MOST {n} topics "
    "genuinely worth briefing on today. Quality over quantity, and SPREAD: never two topics "
    "serving the same interest. If nothing is genuinely worth surfacing, return an empty "
    "list. No filler. For each topic give a concise web search query that surfaces the "
    "latest on it, and name the standing interest it serves (copied verbatim from the list, "
    "or null when it serves none). Return ONLY JSON: "
    '{{"topics":[{{"title":"short card title","angle":"why it matters today",'
    '"query":"web search query","interest":"the standing interest it serves or null"}}]}}.'
)

TRIAGE_RETRY = (
    "\n\nEverything in the excluded list above is already covered. Do NOT propose a rewording "
    "of any of them — branch instead: an adjacent subject, a different facet of an interest, "
    "or a genuinely new development."
)

_TRIAGE_TEMPS = (0.4, 0.7, 0.9)  # hotter each round to break convergence on the same proposals

THREAD_SYS = (
    "Today is {today}. You're planning how to deepen one Pulse briefing into real research. "
    "From the topic and corpus, identify the 2-4 SPECIFIC threads most worth digging into: "
    "concrete entities, claims, numbers, or people that deserve expansion (e.g. a record transfer fee, "
    "a named signing, a release statistic, a key person). Only include a thread if there is genuinely more "
    "worth knowing; return fewer, or an empty list, if the corpus already covers it. For each thread give a "
    "focused web search query that would surface details and statistics. "
    'Return ONLY JSON: {{"threads":[{{"focus":"what to deepen","query":"search query"}}]}}.'
)

CARD_SYS = (
    "Today is {today}. Write one deep-research Pulse briefing for {who}, in the first person, "
    "like a sharp analyst who knows them.\n"
    "FIRST line of your output: 'HEADLINE: ' followed by a short card headline derived from the "
    "sources. Every person, organization, and competition named in the headline must appear in "
    "the numbered sources verbatim — when the sources disagree with the topic title you were "
    "given, the sources win. Then a blank line, then the briefing.\n"
    "Open the briefing with ONE sentence beginning 'I'm surfacing this because' that says why it matters to them today.\n"
    "Then write the briefing as GitHub-flavored markdown. Expand the key claims with concrete numbers, names, "
    "dates, and context drawn ONLY from the numbered sources. Do not invent facts; people, organizations, and "
    "competition names may be used only as they appear in the sources. Current-state attributions — who "
    "holds a role, who manages, who employs whom — may be stated only when a numbered source establishes "
    "them as current; otherwise leave the holder unnamed. When a claim is notable "
    "(a record, a stat, a named person), say the actual figure or detail rather than gesturing at it.\n"
    "Anchor the briefing in time: state when events happened, prefer the most recent sources when they "
    "conflict, and never present a dated event as current. Every dated event gets an absolute date — "
    "month and year (e.g. 'in January 2025'), never a bare month or 'recently'. If everything in the "
    "sources predates today by a season or more, present the briefing as background or a retrospective, "
    "not as news.\n"
    "End EVERY paragraph with citation references to the sources you used for it, in square brackets: [2] or [1,4].\n"
    "Close with a short 'so what' paragraph: the implication, or what to watch next.\n"
    "Let depth follow the evidence. Write only as many paragraphs as the sources genuinely support (hard ceiling: "
    "9). Never pad to reach a length; a tight 3-paragraph brief beats a padded one.\n"
    "When the material is genuinely quantitative, present it by shape (at most one or two blocks, only when they "
    "beat prose; never decorate): comparing the same metrics across 2+ ENTITIES -> a GitHub-flavored markdown "
    "table; tracking ONE metric across an ordered SEQUENCE (seasons, months, years) -> a chart, not a table. If "
    "you catch yourself listing a metric season-by-season, emit a chart:\n"
    "```vera:chart\n"
    "{{\"type\":\"bar|line|groupedBar\",\"title\":\"...\",\"yLabel\":\"goals\",\"series\":[{{\"name\":\"Openda\",\"points\":[{{\"x\":\"23-24\",\"y\":14}}]}}]}}\n"
    "```\n"
    "OR stat cards for 2-4 headline numbers as a fenced block:\n"
    "```vera:stats\n"
    "{{\"cards\":[{{\"value\":\"33\",\"label\":\"goals\",\"sub\":\"69 games\"}}]}}\n"
    "```\n"
    "Use real values only.\n"
    "{img_instr}"
    "Output only the briefing markdown. No title heading, no separate Sources list (the app renders sources)."
)

IMAGE_SYS = (
    "You write a single vivid image prompt for a briefing card's cover art. Given the card, output ONE "
    "sentence describing a concrete subject/scene that captures its vibe — objects, setting, mood, light. "
    "Ground the scene in what the story is actually about. Disambiguate proper nouns: an organization, "
    "team, or place whose name contains a common noun must be depicted as that entity's world (a football "
    "club named after a forest gets a stadium scene, never trees). "
    "No text, words, letters, logos, charts, or UI in the image. No art-style words (style is set separately). "
    "Output only the sentence."
)

SUMMARY_SYS = (
    "Summarize this briefing for a card preview. ONE complete sentence, max 28 words, plain text only "
    "(no markdown, no links, no quotes). It must read as a finished sentence, not a fragment. Output only it."
)


def _image_request(prompt, style, idx, protocol) -> tuple[str, dict]:
    """(path, payload) for the given image protocol. openai: the standard Images API —
    style folds into the prompt text; seed/steps determinism is a vera-protocol feature."""
    if protocol == "vera":
        return "/generate", {"prompt": prompt, "style": style, "width": 1024, "height": 768,
                             "steps": 20, "seed": 1000 + idx}
    full = f"{prompt}. Art style: {style}." if style else prompt
    return "/v1/images/generations", {"prompt": full, "size": "1024x768", "n": 1,
                                      "response_format": "b64_json"}


def _image_b64(d: dict, protocol) -> str | None:
    """The base64 image out of either protocol's response shape (None if absent)."""
    if protocol == "vera":
        return d.get("image_base64")
    data = d.get("data") or []
    return (data[0] or {}).get("b64_json") if data else None


async def _gen_image(prompt, style, idx):
    """Call the image-gen endpoint → returns (content_url, tint) or (None, None). The card
    tint only exists in the vera protocol's response; openai-mode cards tint client-side."""
    try:
        proto = image_protocol()
        path, payload = _image_request(prompt, style, idx, proto)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{_image_base()}{path}", json=payload,
                              timeout=aiohttp.ClientTimeout(total=1200)) as r:
                d = await r.json()
        b64 = _image_b64(d, proto)
        if not b64:
            return None, None
        png = base64.b64decode(b64)
        url = await _upload_image(png, f"pulse-{idx}.png")
        return url, d.get("dominant")
    except Exception:
        return None, None


async def _vision(pause: bool):
    """The vera image protocol's vision pause/resume extension: ask the image service to
    pause / resume its resident vision model around a generation batch, per that contract.
    The standard Images API has no such concept, so this is a no-op in openai mode.
    Best-effort — never blocks the run."""
    if image_protocol() != "vera":
        return
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(f"{_image_base()}/vision/{'pause' if pause else 'resume'}",
                         timeout=aiohttp.ClientTimeout(total=40))
    except Exception:
        pass


async def _upload_image(img_bytes, filename, content_type="image/png"):
    """Upload an image to OWUI files → its content URL (or None)."""
    form = aiohttp.FormData()
    form.add_field("file", img_bytes, filename=filename, content_type=content_type)
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{OWUI_BASE}/api/v1/files/",
            headers={"Authorization": f"Bearer {OWUI_KEY}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            obj = await r.json()
    fid = obj.get("id")
    return f"{OWUI_BASE}/api/v1/files/{fid}/content" if fid else None


# ---- deep-research helpers ----

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Vera"
_OG_PATTERNS = [
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
]


def _img_kind(b):
    """Sniff image type from magic bytes → (ext, mime). Defaults to png."""
    if b[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp", "image/webp"
    return "png", "image/png"


def _clean_caption(s):
    return re.sub(r"\s+", " ", (s or "").replace("|", " ")).strip()[:120]


def _numbered_corpus(sources):
    return "\n\n".join(
        f"[{s['n']}] {s['title']}"
        + (f" (published {s['published']})" if s.get("published") else "")
        + f"\nURL: {s['url']}\n{(s.get('content') or '')[:1500]}"
        for s in sources
    )


def _split_headline(body):
    """Pull the 'HEADLINE: ...' first line off a synthesis output. Returns (headline|None, rest).
    A missing or empty headline leaves the body untouched — the caller keeps its working title."""
    m = re.match(r"\s*HEADLINE:\s*(.+?)\s*\n+(.*)", body or "", re.S)
    if not m or not m.group(1).strip():
        return None, body
    return m.group(1).strip(), m.group(2).strip()


def _parse_threads(txt):
    try:
        j = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        th = j.get("threads")
        return th if isinstance(th, list) else []
    except Exception:
        return []


async def _download(url):
    """Fetch an image URL → (bytes, mime) or (None, None)."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url, headers={"User-Agent": _UA}, timeout=aiohttp.ClientTimeout(total=25)
            ) as r:
                if r.status != 200:
                    return None, None
                data = await r.read()
        if len(data) < 2048:  # skip 1x1 trackers / broken thumbs
            return None, None
        ext, mime = _img_kind(data)
        return data, mime
    except Exception:
        return None, None


async def _fetch_og_image(page_url):
    """Pull a page's og:image / twitter:image (absolute URL), or None."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                page_url, headers={"User-Agent": _UA}, timeout=aiohttp.ClientTimeout(total=12)
            ) as r:
                html = await r.text(errors="ignore")
    except Exception:
        return None
    for pat in _OG_PATTERNS:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            img = m.group(1).strip()
            if img.startswith("//"):
                img = "https:" + img
            elif img.startswith("/"):
                img = urljoin(page_url, img)
            if img.startswith("http"):
                return img
    return None


async def _gather_images(idx, entity_query, top_sources):
    """Retrieve 2-4 real photos (image search + og:images), re-hosted in OWUI.

    Returns [{url, caption, srcN}] — srcN links an og:image to its numbered source (0 if none).
    """
    images, seen = [], set()
    # 1) image search for the key entity (real subject photos)
    try:
        hits = (await image_search(ImageSearchRequest(query=entity_query, max_results=6))).results
    except Exception:
        hits = []
    for h in hits:
        if len(images) >= 2:
            break
        if not h.img_src or h.img_src in seen:
            continue
        data, mime = await _download(h.img_src)
        if not data:
            continue
        ext, _ = _img_kind(data)
        url = await _upload_image(data, f"pulse-{idx}-img{len(images)}.{ext}", mime)
        if url:
            images.append({"url": url, "caption": _clean_caption(h.title), "srcN": 0})
            seen.add(h.img_src)
    # 2) og:image from the top cited sources (context imagery)
    for n, title, src_url in top_sources[:3]:
        if len(images) >= 4:
            break
        og = await _fetch_og_image(src_url)
        if not og or og in seen:
            continue
        data, mime = await _download(og)
        if not data:
            continue
        ext, _ = _img_kind(data)
        url = await _upload_image(data, f"pulse-{idx}-src{n}.{ext}", mime)
        if url:
            images.append({"url": url, "caption": _clean_caption(title), "srcN": n})
            seen.add(og)
    return images


def _recent_for_user(user_id, days=7):
    """Active cards + anything injected in the last `days`, deduped by id. The corpus the
    dedup gate checks a candidate against — so 'I did this yesterday' still counts after it sweeps."""
    cutoff = int(time.time()) - days * 86400
    active = store.list_cards(include_expired=False, user_id=user_id)
    recent = [c for c in store.list_cards(include_expired=True, user_id=user_id)
              if (c.get("created_at") or 0) >= cutoff]
    seen, out = set(), []
    for c in active + recent:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        out.append(c)
    return out


DEDUP_SYS = (
    "You are deduplicating a research feed. Given a CANDIDATE topic and a numbered list of cards "
    "ALREADY in the feed, decide whether any existing card already covers the same thing — same "
    "subject AND claim. Reworded titles still count as the same (e.g. 'New study on X' is the same "
    "as 'Recent findings on X'). A genuinely different angle on a shared interest is NOT the same. "
    "Reply with ONLY 'YES <n>' naming the matching card number, or 'NO'."
)


async def already_covered(topic, user_id):
    """Has Vera already produced a card for this candidate? Returns the matching card dict,
    or None. The deterministic dedup gate — semantic (catches re-wording), per-user, fail-open."""
    cards = _recent_for_user(user_id)
    if not cards:
        return None
    listing = "\n".join(f"{i + 1}. {c['title']} — {(c.get('summary') or '')[:200]}"
                        for i, c in enumerate(cards))
    cand = f"{topic.get('title')} | {topic.get('angle', '')} | {topic.get('query', '')}"
    try:
        raw = await _vera(
            [{"role": "system", "content": DEDUP_SYS},
             {"role": "user", "content": f"CANDIDATE: {cand}\n\nEXISTING:\n{listing}"}],
            temperature=0.0,
        )
        m = re.match(r"\s*YES\s+(\d+)", raw or "", re.I)
        if m and 1 <= int(m.group(1)) <= len(cards):
            return cards[int(m.group(1)) - 1]
    except Exception:
        return None  # fail-open: a gate error must never silently suppress all research
    return None


FRESH_SYS = (
    "Today is {today}. You judge whether a Pulse briefing topic is STALE NEWS: a time-sensitive "
    "news topic (a signing, a release, an announcement, a result) whose newest available coverage "
    "is too old for the event to still be briefed as news today. Evergreen subjects — research "
    "findings, techniques, background, analysis of standing situations — are NEVER stale, whatever "
    "their source age. Answer ONLY the single word STALE or FRESH."
)


def _newest_published(sources):
    """The newest publish date (YYYY-MM-DD) across the corpus, or None when nothing is dated."""
    return max((s["published"] for s in sources if s.get("published")), default=None)


COHERENT_SYS = (
    "You check whether a research corpus actually covers a proposed briefing topic. Adjacent "
    "context is fine — coverage of the topic's club, field, or surrounding situation counts. "
    "The bar is whether the corpus is SUBSTANTIALLY about a different subject than the topic. "
    'Answer ONLY one line: "ON-TOPIC" or "OFF-TOPIC <what the corpus is actually about>".'
)


def _corpus_overview(sources, chars=200):
    """The corpus as numbered titles + snippet heads — enough for a subject check, not a read."""
    return "\n".join(f"[{s['n']}] {s['title']}: {(s.get('content') or '')[:chars]}" for s in sources)


async def is_off_topic(topic, sources):
    """The coherence gate — (True, what the corpus is about) when the broad search drifted to a
    different subject than the topic; (False, None) otherwise. Fail-open: an error can never
    suppress research."""
    try:
        raw = await _vera(
            [{"role": "system", "content": COHERENT_SYS},
             {"role": "user", "content": (f"Topic: {topic.get('title')}\n"
                                          f"Angle: {topic.get('angle', '')}\n\n"
                                          f"Corpus:\n{_corpus_overview(sources)}")}],
            temperature=0.0,
        )
        m = re.match(r"\s*OFF-TOPIC\b[:\s]*(.*)", raw or "", re.I)
        if m:
            return True, (m.group(1).strip() or "a different subject")
    except Exception:
        pass
    return False, None


async def is_stale_news(topic, newest):
    """The freshness gate — True only when the topic is time-sensitive news whose newest coverage
    (`newest`, a YYYY-MM-DD string or None) is too old to brief as news today. An undated corpus
    passes without consulting the model: with no date evidence there is nothing to judge, and
    engines that omit dates must never read as staleness. Fail-open: an error or an unparseable
    verdict can never suppress research."""
    if not newest:
        return False
    try:
        raw = await _vera(
            [{"role": "system", "content": FRESH_SYS.format(today=time.strftime("%Y-%m-%d"))},
             {"role": "user", "content": (f"Topic: {topic.get('title')}\n"
                                          f"Angle: {topic.get('angle', '')}\n"
                                          f"Newest source date: {newest or 'no dated sources'}")}],
            temperature=0.0,
        )
        return bool(re.match(r"\s*STALE\b", raw or "", re.I))
    except Exception:
        return False


AUDIT_SYS = (
    "You are a strict fact auditor. Given a briefing and its numbered sources, list the briefing's "
    "key factual assertions — above all CURRENT-STATE claims (who holds a role, who manages, who "
    "employs whom, who plays where today), plus headline figures and named events. For each, give "
    "the number of one source that supports it, or the word UNSUPPORTED when no source does. Judge "
    "ONLY against the sources; what you believe about the world does not count. Return ONLY JSON: "
    '{"claims":[{"claim":"...","source":3},{"claim":"...","source":"UNSUPPORTED"}]}'
)

REVISE_SYS = (
    "Revise the briefing below with surgical precision: the listed claims are NOT supported by its "
    "sources and must be removed or hedged — drop the unsupported attribution, never substitute a "
    "different specific. Change nothing else: keep every other sentence, citation marker, image "
    "token, and block exactly as written. Output starts with the same 'HEADLINE: ' line (rewritten "
    "only if it contains an unsupported claim), then a blank line, then the briefing."
)


async def _auditor(messages):
    """The audit model: the coder endpoint when configured AND reachable — a DIFFERENT model
    than the writer, so the writer's priors can't validate their own fabrication. The coder is
    typically an on-demand server, so unreachable is a normal state, not an error: fall back to
    a main-model self-audit (weaker, still better than none) and name the fallback. Returns
    (reply_text, auditor_name, provenance_stamp) — the stamp is what the card records."""
    from . import coder  # lazy: avoids a circular load at import time
    base, model = coder._endpoint()
    if base:
        try:
            # Explicit generation budget: a full claims enumeration overruns the small
            # default cap some servers apply, truncating the verdict JSON mid-object.
            msg = await coder._llm(messages, 0.0, max_tokens=3000)
            return (msg.get("content") or ""), "coder", f"cross-model ({model or 'coder'})"
        except Exception:
            return await _vera(messages, temperature=0.0), "main model (coder unreachable)", "self (fallback)"
    return await _vera(messages, temperature=0.0), "main model (coder unconfigured)", "self (fallback)"


def _parse_audit(raw):
    """The audit verdict's unsupported claims, or None when the reply is unparseable."""
    try:
        j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        claims = j.get("claims")
        if not isinstance(claims, list):
            return None
        return [str(c.get("claim", "")).strip() for c in claims
                if str(c.get("source", "")).strip().upper() == "UNSUPPORTED"
                and str(c.get("claim", "")).strip()]
    except Exception:
        return None


async def audit_claims(headline, body, sources, errs, title):
    """Cross-model claim validation: the auditor checks the body against its own corpus;
    unsupported claims go back to the main model for ONE surgical revision (re-audited for the
    record only — the revision ships regardless). Returns (headline, body, audit_stamp, info), the
    text possibly revised; the stamp is 'none' when no effective audit happened, and info is
    {verdict (clean|revised|unavailable), unsupported (count), auditor}. Machinery failure ships
    the original: the feed never starves on audit plumbing."""
    corpus = _numbered_corpus(sources)

    async def verdict():
        raw, auditor, stamp = await _auditor(
            [{"role": "system", "content": AUDIT_SYS},
             {"role": "user", "content": f"Numbered sources:\n{corpus}\n\nBriefing:\n{body}"}])
        return _parse_audit(raw), auditor, stamp

    from . import editor
    today = datetime.now(TZ).date().isoformat()
    stale = editor.stale_current_claims(body, sources, today)

    try:
        unsupported, auditor, stamp = await verdict()
        parse_failed = unsupported is None
        flagged = list(dict.fromkeys((unsupported or []) + stale))
        if not flagged:
            if parse_failed:
                errs.append(f"claim audit: {title} — audit unavailable (unparseable verdict from {auditor})")
                return headline, body, "none", {"verdict": "unavailable", "unsupported": 0, "auditor": auditor}
            errs.append(f"claim audit: {title} — clean ({auditor})")
            return headline, body, stamp, {"verdict": "clean", "unsupported": 0, "auditor": auditor}
        eff_stamp = stamp if not parse_failed else "date check (verdict unparseable)"
        revised = (await _vera(
            [{"role": "system", "content": REVISE_SYS},
             {"role": "user", "content": ("Unsupported claims:\n- " + "\n- ".join(flagged)
                                          + f"\n\nBriefing:\nHEADLINE: {headline or title}\n\n{body}")}],
            temperature=0.2,
        )).strip()
        new_headline, new_body = _split_headline(revised)
        info = {"verdict": "revised", "unsupported": len(flagged), "auditor": auditor}
        if not new_body:
            errs.append(f"claim audit: {title} — {len(flagged)} unsupported, revision empty; shipped original")
            return headline, body, eff_stamp, info
        stale_note = f", {len(stale)} stale-dated" if stale else ""
        record = f"claim audit: {title} — {len(flagged)} unsupported{stale_note}, revised ({auditor})"
        try:
            body = new_body  # re-audit the revision for the record only
            still, _, _ = await verdict()
            if still:
                record += f"; {len(still)} still flagged"
        except Exception:
            pass
        errs.append(record)
        return (new_headline or headline), new_body, eff_stamp, info
    except Exception as e:
        errs.append(f"claim audit: {title} — audit unavailable ({e})")
        return headline, body, "none", {"verdict": "unavailable", "unsupported": 0, "auditor": None}


async def _select_topics(rnd, *, who, persona, all_interests, memories, exclusions, want,
                         recent_texts=None):
    """The run's topic source. When the Profile Graph has live nodes the selection is the
    Scout -> Analyst pipeline (cheap-rank-first: rank hundreds, keep the top `want`); the
    Analyst delivers its ranked best in one pass, so later rounds yield nothing. With an empty
    graph it falls back to v1 `_triage`, so the feed keeps running until extraction is deployed."""
    from . import scout, analyst, editor
    try:
        live = scout.select_live_nodes()
    except Exception:
        live = []
    if live:
        if rnd > 0:
            return []
        found = await scout.scout()
        ranked = await analyst.rank(found.get("candidates", []), recent_card_texts=recent_texts or [],
                                    max_cards=want)
        return editor.survivors_to_topics(ranked.get("chosen", []))
    return await _triage(who, persona, all_interests, memories, exclusions, want, rnd)


def _synthesis_user_prompt(topic, sources, who):
    """The synthesis user message: the topic, its numbered sources, and (when the topic carries
    a Profile Graph seed) the active neighbour nodes the LLM may draw a cross-domain link to."""
    usr = (f"Topic: {topic.get('title')}\nWhy it surfaced: {topic.get('angle', '')}\n\n"
           f"Numbered sources:\n{_numbered_corpus(sources)}")
    seed = topic.get("seed_node_id")
    if seed:
        from . import editor
        usr += editor.connections_block(who, editor.cross_domain_links(seed))
    return usr


async def research_topic(topic, *, who, user_id, idx=0, provenance="scheduled", errors=None,
                         defer_audit=False, outcome=None):
    """The per-topic deep-research pipeline: broad search -> thread extraction ->
    follow-up searches -> real imagery -> first-person synthesis -> summary -> cover art -> inject.

    Extracted from the /pulse/run loop so the scheduled morning briefing AND the heartbeat's
    for-you discovery create cards through ONE path (one feed, one bar). Returns the injected
    card dict, or None if synthesis produced nothing. `errors`, if given, collects the same
    non-fatal step-failure strings the run loop logs.

    `defer_audit=True` skips the inline claim audit and hands the full source corpus back on
    the returned card's ephemeral `_corpus` key (never persisted), so the run loop can audit
    the whole batch at end-of-run against one model wake. The card lands stamped
    `audit: none` until that phase overwrites it.

    `outcome`, if given, is a dict this fills with the structured result the run record keeps:
    on a gate kill `{gate, reason, detail}` (the same evidence the `errs` prose carries, kept
    as fields), and on a shipped card `{cover_generated}`. It mirrors the `errs` accumulator."""
    errs = errors if errors is not None else []
    oc = outcome if outcome is not None else {}

    # Dedup gate — if she's already produced a card for this, skip before spending any
    # research/image/synthesis on it. Catches the heartbeat AND scheduled paths (both land here).
    dup = await already_covered(topic, user_id)
    if dup:
        errs.append(f"skipped (already covered): {topic.get('title')} ≈ {dup['title']}")
        oc.update({"gate": "dedup", "reason": "already covered", "detail": dup.get("title")})
        return None

    # Numbered, deduped master source list accumulated across all searches.
    sources, url_to_n = [], {}

    def add_sources(results):
        for x in results:
            u = getattr(x, "url", None)
            if not u or u in url_to_n:
                continue
            n = len(sources) + 1
            url_to_n[u] = n
            sources.append({"n": n, "title": getattr(x, "title", "") or u,
                            "url": u, "content": getattr(x, "content", ""),
                            "published": getattr(x, "published", None)})

    # broad search
    broad = await web_search(
        SearchRequest(query=topic.get("query") or topic.get("title"), fetch_pages=4, max_results=8)
    )
    add_sources(broad.results)

    # Freshness gate — stale news skips like a dup, before any expensive work; the delivery
    # loop backfills the slot with a replacement topic.
    newest = _newest_published(sources)
    if await is_stale_news(topic, newest):
        errs.append(f"skipped (stale news): {topic.get('title')} — newest source {newest or 'undated'}")
        oc.update({"gate": "freshness", "reason": "stale news", "detail": newest or "undated"})
        return None

    # Coherence gate — a corpus that drifted to a different subject skips the same way;
    # a card must be about its topic, not about whatever the search happened to find.
    off, found = await is_off_topic(topic, sources)
    if off:
        errs.append(f"skipped (off-topic corpus): {topic.get('title')} — corpus about {found}")
        oc.update({"gate": "coherence", "reason": "off-topic corpus", "detail": found})
        return None

    # thread extraction — what's worth deepening (may be empty)
    threads = []
    try:
        traw = await _vera(
            [{"role": "system", "content": THREAD_SYS.format(today=time.strftime("%Y-%m-%d"))},
             {"role": "user", "content": f"Topic: {topic.get('title')}\nAngle: {topic.get('angle', '')}\n\n"
                                          f"Corpus:\n{_numbered_corpus(sources)}"}],
            temperature=0.3,
        )
        threads = _parse_threads(traw)[:4]
    except Exception as e:
        errs.append(f"threads {topic.get('title')}: {e}")

    # follow-up searches per thread (fold into master sources)
    for th in threads:
        try:
            fu = await web_search(
                SearchRequest(query=th.get("query") or th.get("focus") or topic.get("title"),
                              fetch_pages=2, max_results=4)
            )
            add_sources(fu.results)
        except Exception:
            pass

    # real imagery: image search on the key entity + og:images from top sources
    entity_query = (threads[0].get("focus") if threads else None) or topic.get("title")
    top_sources = [(s["n"], s["title"], s["url"]) for s in sources[:3]]
    try:
        inline_images = await _gather_images(idx, entity_query, top_sources)
    except Exception as e:
        inline_images = []
        errs.append(f"images {topic.get('title')}: {e}")

    # first-person deep synthesis with numbered citations + inline-image tokens
    img_instr = ""
    if inline_images:
        caps = "; ".join(f"{i + 1}: {im['caption']}" for i, im in enumerate(inline_images))
        span = "[[img:1]]" if len(inline_images) == 1 else f"[[img:1]] through [[img:{len(inline_images)}]]"
        img_instr = (f"There are {len(inline_images)} images available. Place each where it best "
                     f"illustrates the text, as a token on its own line: {span}. Use each token at most "
                     f"once. The images show: {caps}.\n")
    card_usr = _synthesis_user_prompt(topic, sources, who)
    body = (
        await _vera(
            [{"role": "system", "content": voiced(CARD_SYS.format(
                img_instr=img_instr, who=who, today=time.strftime("%Y-%m-%d")))},
             {"role": "user", "content": card_usr}],
            temperature=0.5,
        )
    ).strip()
    # The display headline comes from the synthesis (source-grounded); the triage title was
    # only the search plan and may name things that don't exist. Fall back to it if absent.
    headline, body = _split_headline(body)
    if not body:
        errs.append(f"skipped (empty synthesis): {topic.get('title')}")
        oc.update({"gate": "empty", "reason": "empty synthesis", "detail": None})
        return None  # nothing synthesized — don't inject an empty card

    # Cross-model claim validation — the coder audits the body against its own corpus and
    # Vera revises once, BEFORE cover art (so the art prompt sees the corrected body).
    # Deferred mode leaves the audit to the run's batched end-of-run phase, which amortizes
    # one audit-model wake across every card in the run.
    audit_stamp = "none"
    if not defer_audit:
        headline, body, audit_stamp, _audit_info = await audit_claims(headline, body, sources, errs, topic.get("title"))

    # Short, complete preview blurb (so the card face never truncates mid-word). Generated
    # before cover art so the image prompt can be built from the synthesis.
    try:
        summary = (
            await _vera(
                [{"role": "system", "content": SUMMARY_SYS}, {"role": "user", "content": body[:1500]}],
                temperature=0.3,
            )
        ).strip().strip('"').replace("\n", " ")
    except Exception:
        summary = None

    # Cover art: Vera writes a vibe-matching prompt from the card's own synthesis (headline +
    # summary + story), not the triage working title; style rotates for a fresh feed.
    image_url = tint = None
    cover_generated = False
    try:
        img_usr = (f"Headline: {headline or topic.get('title')}\n"
                   f"Summary: {summary or ''}\n"
                   f"Story: {body[:600]}")
        log.info("cover prompt input: %s", img_usr.replace("\n", " | "))
        img_prompt = (
            await _vera(
                [{"role": "system", "content": IMAGE_SYS},
                 {"role": "user", "content": img_usr}],
                temperature=0.8,
            )
        ).strip().strip('"')
        image_url, tint = await _gen_image(img_prompt, STYLE_PALETTE[idx % len(STYLE_PALETTE)], idx)
        cover_generated = image_url is not None
    except Exception as e:
        errs.append(f"cover {topic.get('title')}: {e}")
    # Fallback: if generation failed (image service offline/contended), promote the best real
    # image we already gathered to the cover so cards are never imageless.
    if not image_url and inline_images:
        image_url = inline_images[0]["url"]
    oc["cover_generated"] = cover_generated

    card = {
        "id": str(uuid.uuid4()),
        "created_at": int(time.time()),
        "day": datetime.now(TZ).date().isoformat(),
        "status": "new",
        "title": headline or topic.get("title"),
        "summary": summary or "",
        "body": body,
        "image_url": image_url,
        "tint": tint,
        "sources": [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in sources],
        "inline_images": [{"n": i, "url": im["url"], "caption": im["caption"], "sourceN": im.get("srcN", 0)}
                          for i, im in enumerate(inline_images, start=1)],
        "promoted_chat_id": None,
        "kind": "research",
        "provenance": provenance,
        "user_id": user_id,
        "audit": audit_stamp,
    }
    store.insert_card(card)
    if defer_audit:
        card["_corpus"] = sources  # full texts for the end-of-run audit; never persisted
    return card


# Skip-marker prefix -> gate name, for the per-run kill tally that makes a starved run
# (gates ate the proposals) distinguishable from a quiet news day (triage had nothing).
_GATE_MARKERS = (
    ("skipped (already covered)", "dedup"),
    ("skipped (stale news)", "freshness"),
    ("skipped (off-topic corpus)", "coherence"),
    ("skipped (empty synthesis)", "empty"),
)


def _gate_kind(new_errors):
    for e in new_errors:
        for prefix, kind in _GATE_MARKERS:
            if e.startswith(prefix):
                return kind
    return "empty"


def _stamp_interest(interest):
    """Put a shipped interest on the fixation cooldown so consecutive runs and for-you
    ticks rotate to something else. Best-effort — never blocks a card."""
    try:
        vi.observe(interest, source="chat", salience_bump=0.0)
        vi.touch(interest)
    except Exception:
        pass


async def _triage(who, persona, interests, memories, exclusions, want, rnd):
    """One triage round: propose up to `want` topics, avoiding `exclusions`. Retry rounds
    (rnd > 0) carry an explicit branch-out instruction and a hotter temperature so the model
    stops converging on its favorite proposals."""
    usr = (
        (f"About {who}: {persona}\n\n" if persona else "")
        + "Standing interests:\n- "
        + ("\n- ".join(interests) if interests else "(none yet)")
        + "\n\nWhat I know about them (memory):\n- "
        + ("\n- ".join(memories) if memories else "(none)")
        + (("\n\nAlready in the feed (do NOT repeat these — pick different topics):\n- "
            + "\n- ".join(exclusions)) if exclusions else "")
        + (TRIAGE_RETRY if rnd > 0 and exclusions else "")
    )
    raw = await _vera(
        [
            {"role": "system", "content": TRIAGE_SYS.format(today=time.strftime("%Y-%m-%d"), n=want, who=who)},
            {"role": "user", "content": usr},
        ],
        temperature=_TRIAGE_TEMPS[min(rnd, len(_TRIAGE_TEMPS) - 1)],
    )
    return _parse_topics(raw)[:want]


async def _audit_hook(url):
    """POST one of the configured audit warm-up/release hooks. The timeout is generous because
    a wake may cold-load a model. A non-2xx reply raises — an error body is JSON too, and a
    failed wake must read as failed, never as success. Returns the response JSON when there is
    any ({} otherwise) — a wake reply may carry {"already_up": true}."""
    async with aiohttp.ClientSession() as s:
        async with s.post(url, timeout=aiohttp.ClientTimeout(total=600)) as r:
            r.raise_for_status()
            try:
                return await r.json()
            except Exception:
                return {}


async def _audit_phase(pending, errs, items_by_card=None):
    """The batched end-of-run claim audit: one optional model wake amortized across every card
    injected this run. `pending` is [(card, full_sources)]. Each card is audited with the same
    audit_claims machinery as the inline path; revisions and the provenance stamp are applied
    to the stored card. The release hook fires only if this run's wake actually started the
    model — a model that was already up belongs to whoever started it.

    `items_by_card`, if given, maps card id -> the run record's structured item, and each card's
    audit verdict is written onto its item so the drill-in can show per-card audit detail."""
    if not pending:
        return
    woke = False
    if AUDIT_WAKE_URL:
        try:
            reply = await _audit_hook(AUDIT_WAKE_URL)
            woke = not reply.get("already_up")
            errs.append("audit wake: ok" + ("" if woke else " (already up — not ours to release)"))
        except Exception as e:
            errs.append(f"audit wake failed: {e} — auditing via fallback")
    try:
        for card, sources in pending:
            try:
                headline, body, stamp, info = await audit_claims(
                    card["title"], card["body"], sources, errs, card["title"])
                store.apply_audit(card["id"], headline or card["title"], body, stamp)
                if items_by_card and card["id"] in items_by_card:
                    items_by_card[card["id"]]["audit"] = info
            except Exception as e:
                errs.append(f"claim audit: {card.get('title')} — audit phase error ({e})")
    finally:
        if AUDIT_RELEASE_URL and woke:
            try:
                await _audit_hook(AUDIT_RELEASE_URL)
            except Exception as e:
                errs.append(f"audit release failed: {e}")


async def _do_run(req: PulseRequest):
    """The full synchronous Pulse pipeline (sweep -> triage -> per-topic research/inject). Returns the
    result dict. The HTTP endpoint runs this in the background so no caller holds a long request open."""
    out = {"ok": True, "topics": [], "injected": [], "skipped": [], "expired": 0, "errors": []}

    # 0) cleanup sweep (best-effort): expire untouched prior-day cards in the store
    try:
        out["expired"] = store.sweep(datetime.now(TZ).date().isoformat())
    except Exception as e:
        out["errors"].append(f"sweep: {e}")

    if req.sweep_only:
        return out

    # Who is this briefing for? Default to the household owner for backward-compat.
    user_id = req.user_id or store.DEFAULT_USER
    profile = up.get(user_id)
    who = req.user_name or profile.get("name") or "them"

    # 1) gather + triage. Ground in this person's profile interests + whatever the caller passed.
    #    Only the owner's OWUI memories are read here (other users' memories must not leak).
    memories = []
    if user_id == store.DEFAULT_USER:
        try:
            memories = [m.get("content") for m in (await _get_memories() or []) if m.get("content")]
        except Exception as e:
            out["errors"].append(f"memories: {e}")

    all_interests = list(dict.fromkeys(list(req.interests) + [i["topic"] for i in profile.get("interests", [])]))
    # Fixation cooldown: an interest that just shipped a card (here or via a for-you tick)
    # sits out until its cooldown lapses, so consecutive runs rotate instead of replaying
    # whichever interest happens to search best.
    try:
        cooling = vi.cooled(all_interests)
    except Exception:
        cooling = set()
    if cooling:
        all_interests = [t for t in all_interests if t not in cooling]
    persona = profile.get("persona")

    # 2) the novelty loop: triage -> per-topic research (deep-research -> illustrate ->
    # synthesize -> cover art -> inject; the per-topic pipeline lives in research_topic() so
    # the heartbeat shares it). When the dedup gate kills proposals, re-triage — up to
    # PULSE_TRIAGE_ROUNDS — until at least PULSE_MIN_CARDS novel cards land. The feed corpus
    # seeds the exclusion list (the gate stays the guarantee; exclusions save wasted
    # triage->gate round trips), and every proposal joins it so a retry can't re-pitch a
    # rewording of a topic the gate just killed.
    exclusions = [c["title"] for c in _recent_for_user(user_id)]
    target = min(req.max_cards or PULSE_MAX_CARDS, PULSE_MAX_CARDS)
    out["rounds"] = []
    out["items"] = []  # structured per-candidate outcomes for the drill-in (additive to rounds/gates)
    items_by_card = {}  # card id -> its item, so the audit phase can stamp per-card verdicts
    attempt = 0  # running per-topic index across rounds (rotates cover-art styles)
    gates = {"dedup": 0, "freshness": 0, "coherence": 0, "empty": 0, "interest_cap": 0}
    shipped_per_interest = {}  # lowercased interest -> cards shipped this run
    pending_audit = []  # (card, full sources) — audited in one batch after the cover loop

    await _vision(pause=True)  # ask the image service to make room for cover-gen (restored below)
    try:
        for rnd in range(PULSE_TRIAGE_ROUNDS):
            want = target - len(out["injected"])
            if want <= 0:
                break
            try:
                topics = await _select_topics(rnd, who=who, persona=persona,
                                              all_interests=all_interests, memories=memories,
                                              exclusions=exclusions, want=want,
                                              recent_texts=exclusions)
            except Exception as e:
                out["errors"].append(f"triage round {rnd + 1}: {e}")
                break
            if not topics:
                break  # nothing genuinely new left to propose
            record = {"proposed": [t.get("title") for t in topics], "injected": [], "skipped": []}
            exclusions.extend(t["title"] for t in topics if t.get("title"))
            out["topics"].extend(record["proposed"])
            for t in topics:
                if len(out["injected"]) >= target:
                    break
                interest = (t.get("interest") or "").strip()
                item = {"round": rnd + 1, "title": t.get("title"), "angle": t.get("angle"),
                        "interest": interest or None}
                if interest and shipped_per_interest.get(interest.lower(), 0) >= PULSE_MAX_PER_INTEREST:
                    gates["interest_cap"] += 1
                    out["errors"].append(
                        f"skipped (interest cap): {t.get('title')} — '{interest}' already shipped this run")
                    out["skipped"].append(t.get("title"))
                    record["skipped"].append(t.get("title"))
                    item.update({"status": "cap", "gate": "interest_cap", "reason": "interest cap",
                                 "detail": interest})
                    out["items"].append(item)
                    continue
                before = len(out["errors"])
                oc = {}
                try:
                    card = await research_topic(t, who=who, user_id=user_id, idx=attempt,
                                                provenance="scheduled", errors=out["errors"],
                                                defer_audit=True, outcome=oc)
                    if card:
                        pending_audit.append((card, card.pop("_corpus", [])))
                        if t.get("seed_node_id"):
                            from . import learn_store
                            learn_store.record_card(card["id"], [t["seed_node_id"]],
                                                    t.get("scores") or {}, int(time.time()))
                        out["injected"].append(card["title"])
                        record["injected"].append(card["title"])
                        item.update({"title": card["title"], "status": "injected",
                                     "card_id": card["id"],
                                     "cover_generated": oc.get("cover_generated", False),
                                     "audit": None})  # filled by the audit phase below
                        items_by_card[card["id"]] = item
                        if interest:
                            shipped_per_interest[interest.lower()] = \
                                shipped_per_interest.get(interest.lower(), 0) + 1
                            _stamp_interest(interest)
                    else:
                        gates[_gate_kind(out["errors"][before:])] += 1
                        out["skipped"].append(t.get("title"))  # a gate fired or synthesis was empty
                        record["skipped"].append(t.get("title"))
                        item.update({"status": "killed", "gate": oc.get("gate"),
                                     "reason": oc.get("reason"), "detail": oc.get("detail")})
                    out["items"].append(item)
                except Exception as e:
                    out["errors"].append(f"{t.get('title')}: {e}")
                    item.update({"status": "error", "reason": str(e)})
                    out["items"].append(item)
                attempt += 1
            out["rounds"].append(record)
        # Batched claim audit, strictly after the cover loop and before vision resumes: the
        # image model is done, so the (possibly woken) audit model never runs alongside it.
        await _audit_phase(pending_audit, out["errors"], items_by_card)
    finally:
        await _vision(pause=False)  # bring the vision model back up after the image batch

    out["gates"] = gates
    if len(out["injected"]) < target and any(gates.values()):
        msg = (f"starved run: {len(out['injected'])}/{target} cards after "
               f"{len(out['rounds'])} triage round(s); gate kills: "
               + ", ".join(f"{k}={v}" for k, v in gates.items()))
        out["errors"].append(msg)
        log.warning("%s", msg)
    floor = min(PULSE_MIN_CARDS, target)  # an explicit small max_cards lowers the floor too
    if len(out["injected"]) < floor:
        out["errors"].append(
            f"under floor: {len(out['injected'])}/{floor} novel cards "
            f"after {len(out['rounds'])} triage round(s)")
    return out


# ---- async run trigger + status (the scheduler triggers, then polls run_status to completion) ----

_inflight = False  # in-process guard against overlapping runs


def _is_running() -> bool:
    """A run is in flight only if the store says 'running' AND it isn't stale (get_run_status applies
    the stale override) AND this process still has the task flag set."""
    return _inflight and store.get_run_status().get("state") == "running"


async def _runner(fn, req, run_id, kind):
    """Run `fn(req)` in the background, recording terminal status. Never raises."""
    global _inflight
    started = int(time.time())
    try:
        out = await fn(req)
        if kind == "run_all":
            injected = [t for u in out.get("users", []) for t in (u.get("injected") or [])]
            topics, errors = [], [e for u in out.get("users", []) for e in (u.get("errors") or [])]
            gates = {}
            for u in out.get("users", []):
                for k, v in (u.get("gates") or {}).items():
                    gates[k] = gates.get(k, 0) + v
        else:
            injected, topics, errors = out.get("injected", []), out.get("topics", []), out.get("errors", [])
            gates = out.get("gates", {})
        store.set_run_status({"run_id": run_id, "state": "ok", "kind": kind, "started_at": started,
                              "finished_at": int(time.time()), "topics": topics,
                              "injected": injected, "errors": errors, "gates": gates,
                              "rounds": out.get("rounds", []) if kind != "run_all" else [],
                              "items": out.get("items", []) if kind != "run_all" else []})
    except Exception as e:
        store.set_run_status({"run_id": run_id, "state": "error", "kind": kind, "started_at": started,
                              "finished_at": int(time.time()), "topics": [], "injected": [],
                              "errors": [str(e)]})
    finally:
        _inflight = False


def _start(fn, req, kind):
    """Shared trigger: refuse if a run is already in flight, else launch the background task."""
    global _inflight
    if _is_running():
        cur = store.get_run_status()
        return {"ok": True, "already_running": True, "run_id": cur.get("run_id"), "state": "running"}
    run_id = str(int(time.time()))
    _inflight = True
    store.set_run_status({"run_id": run_id, "state": "running", "kind": kind, "started_at": int(time.time()),
                          "finished_at": None, "topics": [], "injected": [], "errors": []})
    asyncio.create_task(_runner(fn, req, run_id, kind))
    return {"ok": True, "run_id": run_id, "state": "running"}


@router.post("/pulse/run", tags=["pulse"])
async def run(req: PulseRequest):
    """Trigger a Pulse run. A sweep-only call runs inline (fast); a real run returns 202
    immediately and executes in the background — poll GET /pulse/run_status for the outcome."""
    if req.sweep_only:
        return await _do_run(req)
    return _start(_do_run, req, "run")


@router.get("/pulse/run_status", tags=["pulse"])
async def run_status():
    """The current/last run state — {run_id, state(idle|running|ok|error|stale), kind,
    started_at, finished_at, topics, injected, errors, gates}. Schedulers poll this to completion."""
    return store.get_run_status()


async def run_outcome(trigger, poll_secs=10, timeout=2700):
    """Follow a /pulse/run 202 trigger to the run's terminal record and distill the outcome
    a scheduler should keep: state, shipped cards, starvation warnings, per-gate kill counts.
    Inline results (sweep_only) pass through untouched; a run still going when the wait
    expires reports itself as such rather than blocking the job slot forever."""
    if trigger.get("state") != "running":
        return trigger
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_secs)
        st = store.get_run_status()
        if st.get("state") == "running":
            continue
        errors = st.get("errors") or []
        return {"state": st.get("state"),
                "warnings": [e for e in errors if e.startswith(("starved run", "under floor"))],
                "gates": st.get("gates") or {},
                "injected": st.get("injected") or []}
    return {"state": "running", "warnings": [f"run still going after {timeout}s — see /pulse/run_status"]}


async def _active_users():
    """OWUI accounts — the people Vera serves. Each gets their own briefing/feed."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OWUI_BASE}/api/v1/users/", headers=_headers(),
                             timeout=aiohttp.ClientTimeout(total=20)) as r:
                d = await r.json()
        users = d.get("users") if isinstance(d, dict) else d
        return [{"id": u.get("id"), "name": u.get("name")} for u in (users or []) if u.get("id")]
    except Exception:
        return []


class RunAllRequest(BaseModel):
    max_cards: int | None = None  # explicit cap; defaults to PULSE_MAX_CARDS and never exceeds it


async def _do_run_all(req: RunAllRequest):
    """Produce each person's OWN briefing: one per-user Pulse run grounded in their profile.
    Returns the full result dict; the endpoint runs this in the background."""
    users = await _active_users()
    if not users:
        users = [{"id": store.DEFAULT_USER, "name": None}]
    out = {"ok": True, "users": []}
    for u in users:
        r = await _do_run(PulseRequest(user_id=u["id"], user_name=u.get("name"),
                                       max_cards=req.max_cards, sweep_only=False))
        out["users"].append({"user_id": u["id"], "name": u.get("name"),
                             "injected": r.get("injected", []), "errors": r.get("errors", []),
                             "gates": r.get("gates", {}), "rounds": r.get("rounds", [])})
    return out


@router.post("/pulse/run_all", tags=["pulse"])
async def run_all(req: RunAllRequest):
    """The multi-user front door for the morning briefing. Returns 202 immediately
    and runs every person's briefing in the background — point the nightly schedule here."""
    return _start(_do_run_all, req, "run_all")


# ---- Pulse feed + lifecycle endpoints (standalone store, no OWUI folder) ----


class BookmarkBody(BaseModel):
    on: bool = True


class StatusCard(BaseModel):
    kind: str = "status"
    severity: str | None = None  # "notice" | "alert" | "critical" (null = neutral)
    category: str | None = None  # System-vein sub-group — vera | infra | health | update
    title: str
    summary: str = ""
    body: str = ""


class ReadBody(BaseModel):
    card_id: str
    user_id: str | None = None


@router.get("/pulse/cards", tags=["pulse"])
async def cards(user_id: str | None = None):
    """The feed for one person. Defaults to the household owner so existing callers that
    don't pass user_id keep seeing that feed; the app passes the signed-in user's id. Each card is
    annotated with `read` for this person so the vein overlay shows per-row state."""
    uid = user_id or store.DEFAULT_USER
    cards = store.list_cards(user_id=uid)
    read = store.read_ids(uid)
    for c in cards:
        c["read"] = c["id"] in read
    return {"cards": cards}


@router.get("/pulse/veins", tags=["pulse"])
async def veins(user_id: str | None = None):
    """The pinned ambient-vein catalog, each merged with this person's UNREAD count +
    max unread severity so the chip dot/count reflects what they haven't read."""
    uid = user_id or store.DEFAULT_USER
    counts = store.unread_counts(uid)
    out = []
    for vein in pulse_veins.veins():
        cnt = counts.get(vein["kind"], {})
        merged = {**vein, "unread": cnt.get("unread", 0), "max_severity": cnt.get("max_severity")}
        # The Weather chip's calm state shows live current conditions, not a static word.
        # Lazy import avoids a circular load (weather imports from pulse). N/A if the feed is down.
        if vein["kind"] == "weather":
            from . import weather
            merged["nominal_label"] = (await weather.current_label()) or "N/A"
        out.append(merged)
    return {"veins": out}


@router.post("/pulse/read", tags=["pulse"])
async def read(b: ReadBody):
    """Record that this person opened a card's detail. Idempotent. Fired only on detail
    open (not on a vein-list glance), so the chip's unread count reflects real reads."""
    store.mark_read(b.user_id or store.DEFAULT_USER, b.card_id)
    return {"ok": True}


@router.post("/pulse/status", tags=["pulse"])
async def status_card(s: StatusCard):
    """Producer front door for ambient/status cards: run-summaries and weather/health
    alerts. Action-less — cards that carry an action use /actions/propose_card instead."""
    await _inject(s.title, s.body, summary=s.summary, kind=s.kind, severity=s.severity, category=s.category)
    return {"ok": True}


@router.post("/pulse/{card_id}/promote", tags=["pulse"])
async def promote(card_id: str):
    c = store.get_card(card_id)
    if not c:
        return {"ok": False, "error": "not found"}
    if c.get("promoted_chat_id"):
        return {"ok": True, "chat_id": c["promoted_chat_id"]}
    chat_id = await _create_owui_chat(c)
    if chat_id:
        store.set_status(card_id, "promoted", promoted_chat_id=chat_id)
    return {"ok": bool(chat_id), "chat_id": chat_id}


@router.post("/pulse/{card_id}/bookmark", tags=["pulse"])
async def bookmark(card_id: str, body: BookmarkBody):
    c = store.get_card(card_id)
    if not c:
        return {"ok": False, "error": "not found"}
    chat_id = c.get("promoted_chat_id")
    if body.on:
        # Create the backing OWUI chat so the bookmark is openable from the sidebar; stays in feed.
        if not chat_id:
            chat_id = await _create_owui_chat(c)
        store.set_status(card_id, "bookmarked", promoted_chat_id=chat_id)
    elif c["status"] == "bookmarked":
        store.set_status(card_id, "seen")
    return {"ok": True, "chat_id": chat_id}


@router.delete("/pulse/{card_id}", tags=["pulse"])
async def remove(card_id: str):
    store.delete_card(card_id)
    return {"ok": True}
