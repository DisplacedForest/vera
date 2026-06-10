"""Wyoming ASR server for vera-voice.

Bridges the engine wrappers (ParakeetEngine / WhisperEngine) to the Wyoming
protocol so any Wyoming-compatible client (Home Assistant, wyoming-satellite, …)
can use vera-voice for streaming speech-to-text.

  TCP port : 10300  (default Wyoming ASR port)
  Env vars : VERA_STT_ENGINE   — "parakeet" (default) | "whisper"
             VERA_PARAKEET_MODEL / VERA_STT_MODEL — forwarded to engine ctors
"""
import asyncio
import audioop
import logging
import os
import time
from contextlib import AsyncExitStack
from functools import partial
from typing import Optional

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.asr import Transcript
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.server import AsyncEventHandler, AsyncServer

log = logging.getLogger("vera-voice.asr")

# Shown by every Wyoming client (e.g. Home Assistant) next to this service.
ATTRIBUTION_URL = os.environ.get("VERA_ATTRIBUTION_URL", "")


# ---------------------------------------------------------------------------
# Engine factory — unit-testable (import without starting the server)
# ---------------------------------------------------------------------------

def _select_engine_class(name: str | None) -> type:
    """Return the engine *class* for the given name — no instantiation, no model load.

    Maps "whisper" → WhisperEngine; everything else (including None, empty
    string, "parakeet", unrecognised values) → ParakeetEngine.

    Imports are lazy so importing asr_server itself stays cheap.
    """
    if name and name.lower().strip() == "whisper":
        from engines import WhisperEngine
        return WhisperEngine
    from engines import ParakeetEngine
    return ParakeetEngine


def select_engine():
    """Return an instantiated, warmed ASR engine selected by VERA_STT_ENGINE.

    Reads VERA_STT_ENGINE (default "parakeet"). Any unrecognised value also
    falls back to parakeet so callers always get a functional engine.
    """
    engine_name = os.environ.get("VERA_STT_ENGINE", "parakeet")
    engine_cls = _select_engine_class(engine_name)
    engine = engine_cls()

    log.info("Warming %s engine…", engine.name)
    t0 = time.monotonic()
    engine.warm()
    log.info("%s warmed in %.1fs", engine.name, time.monotonic() - t0)
    return engine


# ---------------------------------------------------------------------------
# Audio conversion helpers
# ---------------------------------------------------------------------------

_TARGET_RATE = 16000
_TARGET_WIDTH = 2   # int16
_TARGET_CHANNELS = 1


def _to_16k_mono_int16(audio: bytes, rate: int, width: int, channels: int) -> bytes:
    """Convert arbitrary PCM bytes to int16 mono 16 kHz little-endian.

    Handles width conversion, mono downmix, and sample-rate resampling.
    Returns the input unchanged if it already matches the target format.
    """
    if rate == _TARGET_RATE and width == _TARGET_WIDTH and channels == _TARGET_CHANNELS:
        return audio

    # 1. Normalise width to 2 bytes (int16) first so audioop.tomono / ratecv work cleanly.
    if width != 2:
        audio = audioop.lin2lin(audio, width, 2)
        width = 2

    # 2. Downmix to mono.
    if channels != 1:
        audio = audioop.tomono(audio, width, 0.5, 0.5)

    # 3. Resample to 16 kHz.
    if rate != _TARGET_RATE:
        audio, _ = audioop.ratecv(audio, width, 1, rate, _TARGET_RATE, None)

    return audio


# ---------------------------------------------------------------------------
# Wyoming event handler (one instance per client connection)
# ---------------------------------------------------------------------------

class VeraAsrHandler(AsyncEventHandler):
    """Handle one Wyoming ASR client connection, driving the shared engine."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        engine,
    ) -> None:
        super().__init__(reader, writer)
        self._engine = engine
        self._stream_ctx: Optional[AsyncExitStack] = None
        self._adapter = None       # _StreamAdapter while a session is open
        self._audio_start: Optional[AudioStart] = None
        self._t_stop: Optional[float] = None   # wall-clock time of AudioStop

    # ------------------------------------------------------------------
    async def handle_event(self, event: Event) -> bool:
        """Route Wyoming events; return True to keep the connection open."""

        # --- Describe → Info -------------------------------------------
        if Describe.is_type(event.type):
            asr_model = AsrModel(
                name=self._engine.name,
                attribution=Attribution(
                    name="vera-voice",
                    url=ATTRIBUTION_URL,
                ),
                installed=True,
                languages=["en"],
            )
            asr_program = AsrProgram(
                name="vera-voice-asr",
                attribution=Attribution(
                    name="vera-voice",
                    url=ATTRIBUTION_URL,
                ),
                installed=True,
                models=[asr_model],
            )
            info = Info(asr=[asr_program])
            await self.write_event(info.event())
            return True

        # --- AudioStart → open a stream session -----------------------
        if AudioStart.is_type(event.type):
            self._audio_start = AudioStart.from_event(event)
            # Enter the engine's context manager synchronously (it's not async).
            self._stream_ctx = self._engine.stream()
            self._adapter = self._stream_ctx.__enter__()
            log.debug("AudioStart: rate=%d width=%d ch=%d",
                      self._audio_start.rate, self._audio_start.width,
                      self._audio_start.channels)
            return True

        # --- AudioChunk → feed engine ---------------------------------
        if AudioChunk.is_type(event.type):
            if self._adapter is None:
                log.warning("AudioChunk received before AudioStart — ignored")
                return True
            chunk = AudioChunk.from_event(event)
            # Determine format from AudioStart (chunk.rate/width/channels may be 0
            # when the client omits them per-chunk and relies on the AudioStart header).
            rate = chunk.rate or (self._audio_start.rate if self._audio_start else _TARGET_RATE)
            width = chunk.width or (self._audio_start.width if self._audio_start else _TARGET_WIDTH)
            channels = chunk.channels or (self._audio_start.channels if self._audio_start else _TARGET_CHANNELS)
            pcm = _to_16k_mono_int16(chunk.audio, rate, width, channels)
            self._adapter.add_audio(pcm)
            return True

        # --- AudioStop → finalise + respond ---------------------------
        if AudioStop.is_type(event.type):
            t_stop = time.monotonic()
            if self._adapter is None:
                log.warning("AudioStop received but no active stream")
                await self.write_event(Transcript(text="").event())
                return True

            text = self._adapter.text

            # Close the engine context manager.
            try:
                self._stream_ctx.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                log.warning("stream context exit error: %s", exc)
            finally:
                self._stream_ctx = None
                self._adapter = None

            latency = time.monotonic() - t_stop
            log.info("Transcript [%.3fs]: %r", latency, text)
            await self.write_event(Transcript(text=text).event())
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
    engine = select_engine()
    host = "0.0.0.0"
    port = int(os.environ.get("VERA_ASR_PORT", "10300"))
    uri = f"tcp://{host}:{port}"
    log.info("Starting Wyoming ASR server on %s (engine=%s)", uri, engine.name)
    server = AsyncServer.from_uri(uri)
    await server.run(partial(VeraAsrHandler, engine=engine))


if __name__ == "__main__":
    asyncio.run(main())
