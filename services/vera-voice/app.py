"""vera-voice — local STT + TTS HTTP service.

Runs on Apple Silicon via MLX and is invoked over the LAN by the Vera app
from other machines as well as the voice host itself. Bind 0.0.0.0 (see run.sh).

  POST /tts  {text, voice?}        -> audio/wav   (Kokoro via mlx-audio)
  POST /stt  multipart file=...    -> {text,seconds} (whisper-large-v3-turbo via mlx-whisper)
  GET  /health
"""
import asyncio
import glob
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Body, FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, Response

import mlx_whisper
from mlx_audio.tts.generate import generate_audio
from mlx_audio.tts.utils import load_model

# MLX GPU streams are per-thread: every model call runs on this one persistent thread.
_MLX = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")


async def _on_mlx(fn, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(_MLX, lambda: fn(*args, **kwargs))

def _ensure_ffmpeg() -> None:
    """mlx-whisper shells out to `ffmpeg`, and launchd starts services with a minimal
    PATH that won't include it. Resolve it explicitly — VERA_FFMPEG, then PATH, then the
    standard install prefixes — and fail loud at startup instead of 500ing per request."""
    import shutil
    found = os.environ.get("VERA_FFMPEG") or shutil.which("ffmpeg") or next(
        (p for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if os.path.exists(p)), None)
    if not found:
        raise SystemExit("ffmpeg not found — install it (e.g. brew install ffmpeg) or set VERA_FFMPEG")
    os.environ["PATH"] = os.path.dirname(found) + os.pathsep + os.environ.get("PATH", "")


_ensure_ffmpeg()

TTS_MODEL_ID = os.environ.get("VERA_TTS_MODEL", "prince-canuma/Kokoro-82M")
# whisper-small: ~0.5s warm on an M4 (vs ~1.7s for large-v3-turbo) with near-equal accuracy on
# clear speech — chosen for the low-latency in-app voice path. Set VERA_STT_MODEL=
# mlx-community/whisper-large-v3-turbo to trade ~1.2s of latency for best-in-class accuracy.
STT_MODEL_ID = os.environ.get("VERA_STT_MODEL", "mlx-community/whisper-small-mlx")
DEFAULT_VOICE = os.environ.get("VERA_TTS_VOICE", "af_heart")

app = FastAPI(title="vera-voice", version="0.1.0")
_tts_model = None


def tts_model():
    """Lazy-load + cache the Kokoro model (avoids reloading per request)."""
    global _tts_model
    if _tts_model is None:
        _tts_model = load_model(TTS_MODEL_ID)
    return _tts_model


def _warm():
    """Eagerly load STT + TTS models at startup so the first request isn't a ~40s cold load.
    Whisper caches the loaded model per repo in-process; Kokoro is cached by tts_model()."""
    t0 = time.time()
    try:
        tts_model()
    except Exception as e:  # noqa: BLE001
        print(f"[warm] TTS load failed: {e}", flush=True)
    try:
        import struct
        import wave
        p = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        with wave.open(p, "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(struct.pack("<3200h", *([0] * 3200)))  # 0.2s silence
        mlx_whisper.transcribe(p, path_or_hf_repo=STT_MODEL_ID)
        os.unlink(p)
    except Exception as e:  # noqa: BLE001
        print(f"[warm] STT load failed: {e}", flush=True)
    print(f"[warm] models ready in {time.time() - t0:.1f}s (stt={STT_MODEL_ID})", flush=True)


@app.get("/health")
def health():
    return {"ok": True, "stt": STT_MODEL_ID, "tts": TTS_MODEL_ID, "voice": DEFAULT_VOICE}


def _tts_sync(text: str, voice: str) -> bytes | None:
    """Generate one wav (runs on the MLX thread)."""
    with tempfile.TemporaryDirectory() as d:
        generate_audio(
            text=text, model=tts_model(), voice=voice, output_path=d,
            file_prefix="out", audio_format="wav", join_audio=True, save=True, verbose=False,
        )
        wavs = sorted(glob.glob(os.path.join(d, "*.wav")))
        return open(wavs[0], "rb").read() if wavs else None


@app.post("/tts")
async def tts(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    voice = payload.get("voice") or DEFAULT_VOICE
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    t0 = time.time()
    data = await _on_mlx(_tts_sync, text, voice)
    if data is None:
        return JSONResponse({"error": "no audio produced"}, status_code=500)
    return Response(
        content=data, media_type="audio/wav",
        headers={"X-Synth-Seconds": f"{time.time() - t0:.2f}", "X-Voice": voice},
    )


@app.post("/stt")
async def stt(file: UploadFile = File(...)):
    t0 = time.time()
    suffix = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(await file.read())
        tmp.close()
        r = await _on_mlx(mlx_whisper.transcribe, tmp.name, path_or_hf_repo=STT_MODEL_ID)
    finally:
        os.unlink(tmp.name)
    return {"text": r["text"].strip(), "seconds": round(time.time() - t0, 2)}


# Warm models at import so uvicorn serves a hot service (one-time ~cold-load cost on
# startup). Warming runs ON the MLX thread so the GPU stream lives where all later
# calls execute.
if os.environ.get("VERA_WARM", "1") == "1":
    _MLX.submit(_warm).result()
