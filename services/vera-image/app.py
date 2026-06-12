"""
vera-image — minimal HTTP image-generation service for an Apple Silicon host.

Wraps the saved 8-bit Qwen-Image (~/models/qwen-image-8bit) via the mflux CLI. Two
surfaces over the same pipeline:
  POST /v1/images/generations  — the standard OpenAI Images API (what callers use by
                                 default; any compatible client works)
  POST /generate               — the native contract: {prompt, style?, width?, height?,
                                 steps?, seed?} -> {image_base64, dominant, ...} with
                                 deterministic seeds and the card-tint extra

Subprocess-per-request (the CLI reloads the model each call — fine for the overnight
Pulse batch). Pause the vision agent before a batch so the much larger image model has
RAM headroom.
"""

import base64
import os
import subprocess
import tempfile
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image

app = FastAPI(title="vera-image")

MODEL = os.path.expanduser(os.environ.get("VERA_IMAGE_MODEL_PATH", "~/models/qwen-image-8bit"))
GEN = os.path.expanduser(os.environ.get("VERA_MFLUX_BIN", "~/venvs/vera/bin/mflux-generate-qwen"))

# The host's unified memory can't hold the image model alongside the vision model OR the on-demand
# coder. Callers (Pulse) bracket their image batch with /vision/pause .. /vision/resume so the 34GB
# model has headroom; pause evicts BOTH the resident vision agent and the coder (if running), so
# nothing competes. Resume only brings vision back — the coder is on-demand and re-starts when next
# used. Best-effort — never fails a request.
VISION_LABEL = os.environ.get("VERA_VISION_LABEL", "com.vera.vision")
VISION_PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{VISION_LABEL}.plist")
CODER_SCRIPT = os.path.expanduser(os.environ.get("VERA_CODER_SCRIPT", "~/.vera/vera-coder.sh"))


def _vision_ctl(action: str) -> bool:
    domain = f"gui/{os.getuid()}"
    try:
        if action == "pause":
            subprocess.run(["/bin/launchctl", "bootout", f"{domain}/{VISION_LABEL}"],
                           capture_output=True, timeout=30)
            # also free the coder's ~17GB if a coding session left it resident (it re-starts on demand)
            if os.path.exists(CODER_SCRIPT):
                subprocess.run(["/bin/sh", CODER_SCRIPT, "stop"], capture_output=True, timeout=30)
        else:
            subprocess.run(["/bin/launchctl", "bootstrap", domain, VISION_PLIST],
                           capture_output=True, timeout=30)
        return True
    except Exception:
        return False


class GenRequest(BaseModel):
    prompt: str
    style: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 20
    seed: int | None = None


def _dominant(img: Image.Image) -> str:
    """A representative, slightly muted hue for the card panel tint."""
    small = img.convert("RGB").resize((64, 64))
    q = small.quantize(colors=5, method=Image.Quantize.FASTOCTREE).convert("RGB")
    colors = q.getcolors(64 * 64) or []
    if not colors:
        r, g, b = small.resize((1, 1)).getpixel((0, 0))
    else:
        # Most common color, but skip near-black/near-white if a colorful option exists.
        colors.sort(reverse=True)
        r, g, b = colors[0][1]
        for _, (cr, cg, cb) in colors:
            mx, mn = max(cr, cg, cb), min(cr, cg, cb)
            if (mx - mn) > 30 and 25 < (cr + cg + cb) / 3 < 230:
                r, g, b = cr, cg, cb
                break
    return f"#{r:02x}{g:02x}{b:02x}"


@app.get("/health")
def health():
    return {"ok": True, "model": MODEL, "exists": os.path.isdir(MODEL)}


@app.post("/vision/pause")
def vision_pause():
    """Evict the vision model (free unified memory for image gen). Idempotent, best-effort."""
    return {"ok": _vision_ctl("pause")}


@app.post("/vision/resume")
def vision_resume():
    """Bring the vision service back up after an image batch."""
    return {"ok": _vision_ctl("resume")}


# Audit-model lifecycle hooks — the deployment-side targets for vera-api's AUDIT_WAKE_URL /
# AUDIT_RELEASE_URL. They shell to the coder's own ensure/stop script, so this service stays
# a thin HTTP presence over whatever lifecycle the host already has. `ensure` reports whether
# the model was already up: an already-up coder belongs to whoever started it (a human coding
# session), and the caller is expected to skip its release in that case.

@app.post("/admin/coder/ensure")
def coder_ensure():
    """Bring the coder model up (no-op when already up). Blocks through the cold load."""
    if not os.path.exists(CODER_SCRIPT):
        raise HTTPException(503, "coder script not present on this host")
    try:
        p = subprocess.run(["/bin/sh", CODER_SCRIPT, "ensure"], capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "coder did not come up in time")
    out = (p.stdout or b"").decode() + (p.stderr or b"").decode()
    if p.returncode != 0:
        raise HTTPException(500, f"coder ensure failed: {out[:300]}")
    return {"ok": True, "already_up": "already up" in out}


@app.post("/admin/coder/stop")
def coder_stop():
    """Release the coder model's unified memory."""
    if not os.path.exists(CODER_SCRIPT):
        raise HTTPException(503, "coder script not present on this host")
    try:
        p = subprocess.run(["/bin/sh", CODER_SCRIPT, "stop"], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "coder stop timed out")
    return {"ok": p.returncode == 0}


class OpenAIImageRequest(BaseModel):
    prompt: str
    n: int = 1
    size: str = "1024x1024"
    response_format: str = "b64_json"
    model: str | None = None  # accepted for client compatibility; this service has one model


@app.post("/v1/images/generations")
def openai_generate(req: OpenAIImageRequest):
    """OpenAI Images API over the same pipeline, so this service fills the standard
    protocol slot out of the box. One image per request (n is clamped — the model
    reloads per subprocess, so batching multiplies minutes, not value)."""
    if req.response_format != "b64_json":
        raise HTTPException(400, "only response_format=b64_json is supported")
    try:
        w, h = (int(x) for x in req.size.lower().split("x", 1))
    except ValueError:
        raise HTTPException(400, f"unparseable size '{req.size}' — expected WIDTHxHEIGHT")
    out = generate(GenRequest(prompt=req.prompt, width=w, height=h))
    return {"created": int(time.time()), "data": [{"b64_json": out["image_base64"]}]}


@app.post("/generate")
def generate(req: GenRequest):
    full = req.prompt + (f". Art style: {req.style}." if req.style else "")
    out = tempfile.mktemp(suffix=".png")
    cmd = [GEN, "--model", MODEL, "--prompt", full, "--low-ram",
           "--steps", str(req.steps), "--width", str(req.width), "--height", str(req.height),
           "--output", out]
    if req.seed is not None:
        cmd += ["--seed", str(req.seed)]
    try:
        subprocess.run(cmd, check=True, timeout=1200, capture_output=True)
        img = Image.open(out).convert("RGB")
        data = base64.b64encode(open(out, "rb").read()).decode()
        return {"image_base64": data, "dominant": _dominant(img), "width": img.width, "height": img.height}
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, f"mflux failed: {e.stderr.decode()[:300]}")
    finally:
        if os.path.exists(out):
            os.remove(out)
