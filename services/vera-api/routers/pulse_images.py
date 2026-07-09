import base64
import logging
import os
import re
from urllib.parse import urljoin

import aiohttp

from .images import ImageSearchRequest, search as image_search
from .pulse_llm import OWUI_BASE, OWUI_KEY

log = logging.getLogger("vera.pulse")

VERA_IMAGE_BASE = os.environ.get("VERA_IMAGE_BASE", "")          # optional image-gen service; cards skip cover art without it


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


# Rotated per card so the feed feels fresh (ChatGPT-Pulse varies art styles deliberately).
STYLE_PALETTE = [
    "photorealistic product photography, soft natural daylight, shallow depth of field",
    "clean single continuous-line art on warm cream paper, minimal",
    "soft painterly gouache illustration, muted earthy palette",
    "mixed-media collage, torn paper and natural textures, editorial",
    "isometric 3D miniature diorama, tilt-shift, soft studio light",
    "flat vector illustration, bold simple shapes, muted retro palette",
]

IMAGE_SYS = (
    "You write a single vivid image prompt for a briefing card's cover art. Given the card, output ONE "
    "sentence describing a concrete subject/scene that captures its vibe — objects, setting, mood, light. "
    "Ground the scene in what the story is actually about. Disambiguate proper nouns: an organization, "
    "team, or place whose name contains a common noun must be depicted as that entity's world (a football "
    "club named after a forest gets a stadium scene, never trees). "
    "No text, words, letters, logos, charts, or UI in the image. No art-style words (style is set separately). "
    "Output only the sentence."
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


async def make_cover(headline, summary, body, topic, inline_images, idx, errs):
    # Cover art: Vera writes a vibe-matching prompt from the card's own synthesis (headline +
    # summary + story), not the triage working title; style rotates for a fresh feed.
    from . import pulse
    image_url = tint = None
    cover_generated = False
    try:
        img_usr = (f"Headline: {headline or topic.get('title')}\n"
                   f"Summary: {summary or ''}\n"
                   f"Story: {body[:600]}")
        log.info("cover prompt input: %s", img_usr.replace("\n", " | "))
        img_prompt = (
            await pulse._vera(
                [{"role": "system", "content": IMAGE_SYS},
                 {"role": "user", "content": img_usr}],
                temperature=0.8,
            )
        ).strip().strip('"')
        image_url, tint = await pulse._gen_image(img_prompt, STYLE_PALETTE[idx % len(STYLE_PALETTE)], idx)
        cover_generated = image_url is not None
    except Exception as e:
        errs.append(f"cover {topic.get('title')}: {e}")
    # Fallback: if generation failed (image service offline/contended), promote the best real
    # image we already gathered to the cover so cards are never imageless.
    if not image_url and inline_images:
        image_url = inline_images[0]["url"]
    return image_url, tint, cover_generated
