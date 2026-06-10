"""Pulse — proactive overnight briefings.

One endpoint, POST /pulse/run, does the whole pipeline:
  cleanup sweep -> gather memories -> triage (Vera) -> per-topic search + synthesize
  -> inject one card per topic into the Pulse store.

The scheduler's only job is to trigger this each morning. All logic lives here, in Python.
"""

import asyncio
import base64
import json
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

from . import pulse_lanes
from . import pulse_store as store
from . import user_profile_store as up
from .persona import voiced
from .images import ImageSearchRequest, search as image_search
from .websearch import SearchRequest, search as web_search

router = APIRouter()

OWUI_BASE = os.environ.get("OWUI_BASE", "").rstrip("/")          # Open WebUI (memory, chat promotion)
OWUI_KEY = os.environ.get("OWUI_KEY", "")
VERA_BASE = os.environ.get("VERA_BASE", "").rstrip("/")          # main LLM, any OpenAI-compatible /v1
MODEL = os.environ.get("VERA_MODEL", "")
DEFAULT_FOLDER = os.environ.get("PULSE_FOLDER_ID", "")
VERA_IMAGE_BASE = os.environ.get("VERA_IMAGE_BASE", "")          # optional image-gen service; cards skip cover art without it
TZ = ZoneInfo(os.environ.get("HOME_TZ", "UTC"))  # untouched cards expire the day after creation (ChatGPT-Pulse daily freshness)

# Image generation resolves through the integrations registry's 'image_gen' entry
# (env-seeded by VERA_IMAGE_BASE / IMAGE_PROTOCOL, editable in the plugin store) — the
# same pattern as the coder's tool protocol. Protocol: the standard OpenAI Images API
# unless 'vera' selects the bespoke reference contract (style/steps/seed determinism +
# co-located vision arbitration).
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
    max_cards: int = 5
    pulse_folder_id: str | None = None
    sweep_only: bool = False  # run just the cleanup sweep, skip generation
    user_id: str | None = None    # who this briefing is for (defaults to the household owner)
    user_name: str | None = None  # display name for the briefing voice


def _headers():
    return {"Authorization": f"Bearer {OWUI_KEY}", "Content-Type": "application/json"}


def _chat_payload(messages, temperature) -> dict:
    """The /chat/completions body — pure OpenAI unless template kwargs are configured."""
    p = {"model": MODEL, "stream": False, "temperature": temperature, "messages": messages}
    if CHAT_TEMPLATE_KWARGS:
        p["chat_template_kwargs"] = CHAT_TEMPLATE_KWARGS
    return p


async def _vera(messages, temperature=0.4):
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{VERA_BASE}/chat/completions",
            json=_chat_payload(messages, temperature),
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
    `kind`/`severity` place the card in an ambient lane; default is the research feed.
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
    "genuinely worth briefing on today. Quality over quantity. If nothing is genuinely "
    "worth surfacing, return an empty list. No filler. For each topic give a concise web "
    "search query that surfaces the latest on it. Return ONLY JSON: "
    '{{"topics":[{{"title":"short card title","angle":"why it matters today","query":"web search query"}}]}}.'
)

THREAD_SYS = (
    "You're planning how to deepen one Pulse briefing into real research. "
    "From the topic and corpus, identify the 2-4 SPECIFIC threads most worth digging into: "
    "concrete entities, claims, numbers, or people that deserve expansion (e.g. a record transfer fee, "
    "a named signing, a release statistic, a key person). Only include a thread if there is genuinely more "
    "worth knowing; return fewer, or an empty list, if the corpus already covers it. For each thread give a "
    "focused web search query that would surface details and statistics. "
    'Return ONLY JSON: {"threads":[{"focus":"what to deepen","query":"search query"}]}.'
)

CARD_SYS = (
    "Write one deep-research Pulse briefing for {who}, in the first person, like a sharp "
    "analyst who knows them.\n"
    "Open with ONE sentence beginning 'I'm surfacing this because' that says why it matters to them today.\n"
    "Then write the briefing as GitHub-flavored markdown. Expand the key claims with concrete numbers, names, "
    "dates, and context drawn ONLY from the numbered sources. Do not invent facts. When a claim is notable "
    "(a record, a stat, a named person), say the actual figure or detail rather than gesturing at it.\n"
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
    """Co-located-service memory arbitration — part of the bespoke vera image protocol
    only: ask the image host to evict (pause) / restore (resume) its resident vision model
    so the much larger image model has headroom. The standard Images API has no such
    concept, so this is a no-op in openai mode. Best-effort — never blocks the run."""
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
        f"[{s['n']}] {s['title']}\nURL: {s['url']}\n{(s.get('content') or '')[:1500]}" for s in sources
    )


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


async def research_topic(topic, *, who, user_id, idx=0, provenance="scheduled", errors=None):
    """The per-topic deep-research pipeline: broad search -> thread extraction ->
    follow-up searches -> real imagery -> first-person synthesis -> cover art -> summary -> inject.

    Extracted from the /pulse/run loop so the scheduled morning briefing AND the heartbeat's
    for-you discovery create cards through ONE path (one feed, one bar). Returns the injected
    card dict, or None if synthesis produced nothing. `errors`, if given, collects the same
    non-fatal step-failure strings the run loop logs."""
    errs = errors if errors is not None else []

    # Dedup gate — if she's already produced a card for this, skip before spending any
    # research/image/synthesis on it. Catches the heartbeat AND scheduled paths (both land here).
    dup = await already_covered(topic, user_id)
    if dup:
        errs.append(f"skipped (already covered): {topic.get('title')} ≈ {dup['title']}")
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
                            "url": u, "content": getattr(x, "content", "")})

    # broad search
    broad = await web_search(
        SearchRequest(query=topic.get("query") or topic.get("title"), fetch_pages=4, max_results=8)
    )
    add_sources(broad.results)

    # thread extraction — what's worth deepening (may be empty)
    threads = []
    try:
        traw = await _vera(
            [{"role": "system", "content": THREAD_SYS},
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
    card_usr = (f"Topic: {topic.get('title')}\nWhy it surfaced: {topic.get('angle', '')}\n\n"
                f"Numbered sources:\n{_numbered_corpus(sources)}")
    body = (
        await _vera(
            [{"role": "system", "content": voiced(CARD_SYS.format(img_instr=img_instr, who=who))},
             {"role": "user", "content": card_usr}],
            temperature=0.5,
        )
    ).strip()
    if not body:
        return None  # nothing synthesized — don't inject an empty card

    # Cover art: Vera writes a vibe-matching prompt; style rotates for a fresh feed.
    image_url = tint = None
    try:
        img_prompt = (
            await _vera(
                [{"role": "system", "content": IMAGE_SYS},
                 {"role": "user", "content": f"Title: {topic.get('title')}\n\n{body[:900]}"}],
                temperature=0.8,
            )
        ).strip().strip('"')
        image_url, tint = await _gen_image(img_prompt, STYLE_PALETTE[idx % len(STYLE_PALETTE)], idx)
    except Exception as e:
        errs.append(f"cover {topic.get('title')}: {e}")
    # Fallback: if generation failed (image service offline/contended), promote the best real
    # image we already gathered to the cover so cards are never imageless.
    if not image_url and inline_images:
        image_url = inline_images[0]["url"]

    # Short, complete preview blurb (so the card face never truncates mid-word).
    try:
        summary = (
            await _vera(
                [{"role": "system", "content": SUMMARY_SYS}, {"role": "user", "content": body[:1500]}],
                temperature=0.3,
            )
        ).strip().strip('"').replace("\n", " ")
    except Exception:
        summary = None

    card = {
        "id": str(uuid.uuid4()),
        "created_at": int(time.time()),
        "day": datetime.now(TZ).date().isoformat(),
        "status": "new",
        "title": topic.get("title"),
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
    }
    store.insert_card(card)
    return card


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
    persona = profile.get("persona")
    # Show her what's already in the feed so she stops proposing dupes (the gate in
    # research_topic is the guarantee; this just saves wasted triage->gate round trips).
    feed_titles = [c["title"] for c in _recent_for_user(user_id)]
    triage_usr = (
        (f"About {who}: {persona}\n\n" if persona else "")
        + "Standing interests:\n- "
        + ("\n- ".join(all_interests) if all_interests else "(none yet)")
        + "\n\nWhat I know about them (memory):\n- "
        + ("\n- ".join(memories) if memories else "(none)")
        + (("\n\nAlready in the feed (do NOT repeat these — pick different topics):\n- "
            + "\n- ".join(feed_titles)) if feed_titles else "")
    )
    try:
        raw = await _vera(
            [
                {"role": "system", "content": TRIAGE_SYS.format(today=time.strftime("%Y-%m-%d"), n=req.max_cards, who=who)},
                {"role": "user", "content": triage_usr},
            ],
            temperature=0.4,
        )
        topics = _parse_topics(raw)[: req.max_cards]
    except Exception as e:
        out["errors"].append(f"triage: {e}")
        return out  # zero-floor: no topics, nothing injected

    out["topics"] = [t.get("title") for t in topics]

    # 2) per topic: deep-research loop -> illustrate -> synthesize -> cover art -> inject.
    # The per-topic pipeline lives in research_topic() so the heartbeat shares it.
    await _vision(pause=True)  # free the image host's memory so cover-gen fits (restored below)
    for idx, t in enumerate(topics):
        try:
            card = await research_topic(t, who=who, user_id=user_id, idx=idx,
                                        provenance="scheduled", errors=out["errors"])
            if card:
                out["injected"].append(card["title"])
            else:
                out["skipped"].append(t.get("title"))  # dup gate or empty synthesis
        except Exception as e:
            out["errors"].append(f"{t.get('title')}: {e}")

    await _vision(pause=False)  # bring the vision model back up after the image batch
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
        else:
            injected, topics, errors = out.get("injected", []), out.get("topics", []), out.get("errors", [])
        store.set_run_status({"run_id": run_id, "state": "ok", "kind": kind, "started_at": started,
                              "finished_at": int(time.time()), "topics": topics,
                              "injected": injected, "errors": errors})
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
    started_at, finished_at, topics, injected, errors}. Schedulers poll this to completion."""
    return store.get_run_status()


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
    max_cards: int = 5


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
                             "injected": r.get("injected", []), "errors": r.get("errors", [])})
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
    category: str | None = None  # System-lane sub-group — vera | infra | health | update
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
    annotated with `read` for this person so the lane overlay shows per-row state."""
    uid = user_id or store.DEFAULT_USER
    cards = store.list_cards(user_id=uid)
    read = store.read_ids(uid)
    for c in cards:
        c["read"] = c["id"] in read
    return {"cards": cards}


@router.get("/pulse/lanes", tags=["pulse"])
async def lanes(user_id: str | None = None):
    """The pinned ambient-lane catalog, each merged with this person's UNREAD count +
    max unread severity so the chip dot/count reflects what they haven't read."""
    uid = user_id or store.DEFAULT_USER
    counts = store.unread_counts(uid)
    out = []
    for lane in pulse_lanes.lanes():
        cnt = counts.get(lane["kind"], {})
        merged = {**lane, "unread": cnt.get("unread", 0), "max_severity": cnt.get("max_severity")}
        # The Weather chip's calm state shows live current conditions, not a static word.
        # Lazy import avoids a circular load (weather imports from pulse). N/A if the feed is down.
        if lane["kind"] == "weather":
            from . import weather
            merged["nominal_label"] = (await weather.current_label()) or "N/A"
        out.append(merged)
    return {"lanes": out}


@router.post("/pulse/read", tags=["pulse"])
async def read(b: ReadBody):
    """Record that this person opened a card's detail. Idempotent. Fired only on detail
    open (not on a lane-list glance), so the chip's unread count reflects real reads."""
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
