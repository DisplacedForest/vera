"""Kokoro TTS engine — extracted from app.py for reuse by Wyoming TTS server.

Provides a simple synthesize() function that returns WAV bytes (24 kHz mono).
Model loading is lazy and cached (same pattern as app.py's tts_model()).
"""
import glob
import os
import tempfile

from mlx_audio.tts.generate import generate_audio
from mlx_audio.tts.utils import load_model

TTS_MODEL_ID = os.environ.get("VERA_TTS_MODEL", "prince-canuma/Kokoro-82M")
DEFAULT_VOICE = os.environ.get("VERA_TTS_VOICE", "af_heart")
# 'a' = American English via misaki G2P (Kokoro's intended frontend: correct year/number
# normalization, better pronunciation). The default 'en' falls back to espeak, which reads
# years as cardinals ("one thousand seven hundred…"). 'b' = British English.
TTS_LANG = os.environ.get("VERA_TTS_LANG", "a")

_tts_model = None


def _model():
    """Lazy-load + cache the Kokoro model."""
    global _tts_model
    if _tts_model is None:
        _tts_model = load_model(TTS_MODEL_ID)
    return _tts_model


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Synthesize text to WAV bytes (24 kHz mono) using Kokoro via mlx-audio."""
    with tempfile.TemporaryDirectory() as d:
        generate_audio(
            text=text,
            model=_model(),
            voice=voice,
            lang_code=TTS_LANG,
            output_path=d,
            file_prefix="out",
            audio_format="wav",
            join_audio=True,
            save=True,
            verbose=False,
        )
        wavs = sorted(glob.glob(os.path.join(d, "*.wav")))
        if not wavs:
            raise RuntimeError("Kokoro produced no audio output")
        return open(wavs[0], "rb").read()
