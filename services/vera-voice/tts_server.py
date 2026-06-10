"""Wyoming TTS server for vera-voice.

Wraps the Kokoro engine (engines/kokoro.py) in the Wyoming protocol so any
Wyoming-compatible client (Home Assistant, wyoming-satellite, …) can use
vera-voice for text-to-speech.

  TCP port : 10200  (default)
  Env vars : VERA_TTS_PORT  — override listen port (default 10200)
             VERA_TTS_VOICE — default voice passed to Kokoro (default af_heart)
"""
import asyncio
import io
import logging
import os
import time
import wave
from functools import partial

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize

log = logging.getLogger("vera-voice.tts")

_DEFAULT_VOICE = os.environ.get("VERA_TTS_VOICE", "af_heart")
# Shown by every Wyoming client (e.g. Home Assistant) next to this service.
ATTRIBUTION_URL = os.environ.get("VERA_ATTRIBUTION_URL", "")
_PCM_CHUNK_SAMPLES = 1024   # frames per AudioChunk (low latency without excess overhead)


# ---------------------------------------------------------------------------
# Wyoming event handler (one instance per client connection)
# ---------------------------------------------------------------------------

class VeraTtsHandler(AsyncEventHandler):
    """Handle one Wyoming TTS client connection."""

    async def handle_event(self, event: Event) -> bool:
        """Route Wyoming events; return True to keep the connection open."""

        # --- Describe → Info -------------------------------------------
        if Describe.is_type(event.type):
            voice = TtsVoice(
                name=_DEFAULT_VOICE,
                attribution=Attribution(
                    name="vera-voice",
                    url=ATTRIBUTION_URL,
                ),
                installed=True,
                description=None,
                version=None,
                languages=["en"],
            )
            program = TtsProgram(
                name="vera-voice-tts",
                attribution=Attribution(
                    name="vera-voice",
                    url=ATTRIBUTION_URL,
                ),
                installed=True,
                description=None,
                version=None,
                voices=[voice],
            )
            await self.write_event(Info(tts=[program]).event())
            return True

        # --- Synthesize → produce audio --------------------------------
        if Synthesize.is_type(event.type):
            synth = Synthesize.from_event(event)
            text = synth.text.strip()
            # Honour optional per-request voice override.
            voice_name = (
                synth.voice.name
                if synth.voice and synth.voice.name
                else _DEFAULT_VOICE
            )

            log.info("Synthesize: voice=%r text=%r", voice_name, text[:80])
            t0 = time.monotonic()

            from engines.kokoro import synthesize as kokoro_synthesize
            wav_bytes = kokoro_synthesize(text, voice=voice_name)

            synth_elapsed = time.monotonic() - t0
            log.info("Kokoro synthesis done in %.2fs (%d WAV bytes)", synth_elapsed, len(wav_bytes))

            # Parse the WAV container — strip the RIFF header, extract PCM frames.
            with wave.open(io.BytesIO(wav_bytes)) as wf:
                rate = wf.getframerate()
                width = wf.getsampwidth()
                channels = wf.getnchannels()
                pcm = wf.readframes(wf.getnframes())

            chunk_bytes = _PCM_CHUNK_SAMPLES * width * channels

            await self.write_event(
                AudioStart(rate=rate, width=width, channels=channels).event()
            )

            t_first = None
            for offset in range(0, len(pcm), chunk_bytes):
                chunk = pcm[offset: offset + chunk_bytes]
                await self.write_event(
                    AudioChunk(rate=rate, width=width, channels=channels, audio=chunk).event()
                )
                if t_first is None:
                    t_first = time.monotonic()
                    log.info(
                        "First AudioChunk latency: %.3fs  (PCM total: %d bytes, rate=%d)",
                        t_first - t0, len(pcm), rate,
                    )

            await self.write_event(AudioStop().event())
            log.info("AudioStop sent. Total wall-clock: %.2fs", time.monotonic() - t0)
            return True

        # Unknown event — keep connection open.
        return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Warm Kokoro once so the first real request is fast.
    log.info("Warming Kokoro TTS engine…")
    t0 = time.monotonic()
    try:
        from engines.kokoro import synthesize as kokoro_synthesize
        kokoro_synthesize("Ready.")
        log.info("Kokoro warmed in %.1fs", time.monotonic() - t0)
    except Exception:  # noqa: BLE001
        log.exception("Kokoro warm-up failed — will retry on first request")

    host = "0.0.0.0"
    port = int(os.environ.get("VERA_TTS_PORT", "10200"))
    uri = f"tcp://{host}:{port}"
    log.info("Starting Wyoming TTS server on %s (voice=%s)", uri, _DEFAULT_VOICE)
    server = AsyncServer.from_uri(uri)
    await server.run(partial(VeraTtsHandler))


if __name__ == "__main__":
    asyncio.run(main())
