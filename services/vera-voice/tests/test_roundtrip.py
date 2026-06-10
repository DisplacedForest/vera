"""Integration test: full TTS → ASR round-trip over Wyoming protocol.

Text is synthesised via the TTS server (port 10200), the resulting PCM is fed
to the ASR server (port 10300), and the returned transcript is compared to the
original input.

Mark: @pytest.mark.slow — this test loads real ML models (~5 s each) and may
take 30–60 s total.  Run with:

    pytest tests/test_roundtrip.py -v -s
"""
import asyncio
import os
import socket
import subprocess
import sys
import time

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ASR_PORT = int(os.environ.get("VERA_ASR_PORT", "10300"))
_TTS_PORT = int(os.environ.get("VERA_TTS_PORT", "10200"))

_TEXT = "The kitchen lights are on and the storm arrives at four."

_STARTUP_TIMEOUT = 120   # seconds — generous for model warm-up
_SYNTH_TIMEOUT   = 120   # seconds — synthesis may be slow on first call
_TRANSCRIPT_TIMEOUT = 60  # seconds — transcription budget

# Minimum Jaccard-style word recall of input words in transcript
_MIN_OVERLAP = 0.6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wait_for_port(port: int, timeout: float = _STARTUP_TIMEOUT) -> bool:
    """Poll until the port accepts connections or timeout elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def _word_overlap(reference: str, hypothesis: str) -> float:
    """Recall of reference words found in hypothesis (case-insensitive)."""
    ref_words = set(reference.lower().split())
    hyp_words = set(hypothesis.lower().split())
    if not ref_words:
        return 1.0
    return len(ref_words & hyp_words) / len(ref_words)


# ---------------------------------------------------------------------------
# Fixture: launch both servers, wait until ready, tear down after the test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def both_servers():
    """Start tts_server.py and asr_server.py as subprocesses, yield, then kill them."""
    env = os.environ.copy()
    env["VERA_TTS_PORT"] = str(_TTS_PORT)
    env["VERA_ASR_PORT"] = str(_ASR_PORT)
    # Use parakeet for ASR (fastest on Apple Silicon)
    env.setdefault("VERA_STT_ENGINE", "parakeet")

    tts_proc = subprocess.Popen(
        [sys.executable, os.path.join(_ROOT, "tts_server.py")],
        cwd=_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    asr_proc = subprocess.Popen(
        [sys.executable, os.path.join(_ROOT, "asr_server.py")],
        cwd=_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        print(f"\n[roundtrip] Waiting for TTS server on port {_TTS_PORT}…")
        if not _wait_for_port(_TTS_PORT, _STARTUP_TIMEOUT):
            out = _drain(tts_proc)
            pytest.fail(
                f"TTS server did not start within {_STARTUP_TIMEOUT}s.\n"
                f"Server output:\n{out}"
            )

        print(f"[roundtrip] Waiting for ASR server on port {_ASR_PORT}…")
        if not _wait_for_port(_ASR_PORT, _STARTUP_TIMEOUT):
            out = _drain(asr_proc)
            pytest.fail(
                f"ASR server did not start within {_STARTUP_TIMEOUT}s.\n"
                f"Server output:\n{out}"
            )

        print("[roundtrip] Both servers ready.")
        yield {"tts_port": _TTS_PORT, "asr_port": _ASR_PORT}

    finally:
        for proc in (tts_proc, asr_proc):
            proc.terminate()
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()

        # Confirm the ports are no longer occupied.
        for port in (_TTS_PORT, _ASR_PORT):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        time.sleep(0.5)
                except OSError:
                    break

        print("[roundtrip] Servers stopped, ports released.")


def _drain(proc: subprocess.Popen, limit: int = 4000) -> str:
    """Read up to `limit` bytes of output from a subprocess without blocking long."""
    try:
        out, _ = proc.communicate(timeout=5)
        return out[-limit:] if out else ""
    except subprocess.TimeoutExpired:
        proc.kill()
        return ""


# ---------------------------------------------------------------------------
# Round-trip coroutine
# ---------------------------------------------------------------------------

async def _run_roundtrip(tts_port: int, asr_port: int) -> str:
    """Synthesize _TEXT via TTS, pipe PCM into ASR, return transcript text."""
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.asr import Transcript
    from wyoming.client import AsyncClient
    from wyoming.tts import Synthesize

    tts_uri = f"tcp://localhost:{tts_port}"
    asr_uri = f"tcp://localhost:{asr_port}"

    # ---- Step 1: TTS → collect PCM ----------------------------------------
    chunks: list[bytes] = []
    tts_rate = tts_width = tts_channels = 0

    async with AsyncClient.from_uri(tts_uri) as tts:
        await tts.write_event(Synthesize(text=_TEXT).event())

        deadline = asyncio.get_event_loop().time() + _SYNTH_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            event = await asyncio.wait_for(tts.read_event(), timeout=60)
            if event is None:
                break
            if AudioStart.is_type(event.type):
                s = AudioStart.from_event(event)
                tts_rate, tts_width, tts_channels = s.rate, s.width, s.channels
                continue
            if AudioChunk.is_type(event.type):
                c = AudioChunk.from_event(event)
                chunks.append(c.audio)
                continue
            if AudioStop.is_type(event.type):
                break

    assert chunks, "TTS returned no audio chunks"
    pcm = b"".join(chunks)
    print(
        f"\n[roundtrip] TTS produced {len(pcm)} PCM bytes  "
        f"({tts_rate} Hz, {tts_width*8}-bit, {tts_channels}ch)"
    )

    # ---- Step 2: PCM → ASR → transcript ------------------------------------
    _CHUNK_FRAMES = 4096
    chunk_bytes = _CHUNK_FRAMES * tts_width * tts_channels

    async with AsyncClient.from_uri(asr_uri) as asr:
        await asr.write_event(
            AudioStart(rate=tts_rate, width=tts_width, channels=tts_channels).event()
        )
        for offset in range(0, len(pcm), chunk_bytes):
            segment = pcm[offset: offset + chunk_bytes]
            await asr.write_event(
                AudioChunk(
                    rate=tts_rate, width=tts_width, channels=tts_channels, audio=segment
                ).event()
            )
        t_stop = asyncio.get_event_loop().time()
        await asr.write_event(AudioStop().event())

        deadline = asyncio.get_event_loop().time() + _TRANSCRIPT_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            event = await asyncio.wait_for(asr.read_event(), timeout=30)
            if event is None:
                break
            if Transcript.is_type(event.type):
                t = Transcript.from_event(event)
                latency = asyncio.get_event_loop().time() - t_stop
                print(f"[roundtrip] AudioStop→Transcript latency: {latency:.3f}s")
                return t.text

    raise RuntimeError("Timeout waiting for ASR Transcript")


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_tts_asr_roundtrip(both_servers):
    """Synthesize a sentence, transcribe the audio, check word overlap ≥ 0.6."""
    transcript = asyncio.run(
        _run_roundtrip(both_servers["tts_port"], both_servers["asr_port"])
    )

    print(f"\n[roundtrip] Input    : {_TEXT!r}")
    print(f"[roundtrip] Transcript: {transcript!r}")

    overlap = _word_overlap(_TEXT, transcript)
    print(f"[roundtrip] Word recall: {overlap:.2f}  (threshold: {_MIN_OVERLAP})")

    assert transcript.strip(), "Transcript was empty"
    assert overlap >= _MIN_OVERLAP, (
        f"Word recall {overlap:.2f} < {_MIN_OVERLAP}\n"
        f"  input      : {_TEXT!r}\n"
        f"  transcript : {transcript!r}"
    )
