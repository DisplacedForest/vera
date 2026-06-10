"""Smoke test for the Wyoming TTS server.

Connects to a running tts_server.py, sends a Synthesize request, collects the
AudioStart / AudioChunk / AudioStop sequence, reassembles PCM into a WAV, and
asserts the result is non-trivial.

Usage (from the vera-voice directory with .venv-wyoming activated, server
already running):
    VERA_TTS_PORT=10200 python tests/smoke_tts.py

Or let the test manage the server itself by passing --self-start:
    python tests/smoke_tts.py --self-start
"""
import asyncio
import io
import os
import sys
import time
import wave

_SERVER_URI = f"tcp://localhost:{os.environ.get('VERA_TTS_PORT', '10200')}"
_SYNTH_TEXT = "Hello, I am Vera."
_MIN_PCM_BYTES = 20_000   # sanity floor: any real speech should exceed this
_STARTUP_TIMEOUT = 120    # seconds to wait for Kokoro warm-up


async def run_smoke() -> None:
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.client import AsyncClient
    from wyoming.tts import Synthesize

    async with AsyncClient.from_uri(_SERVER_URI) as client:
        t0 = time.monotonic()
        await client.write_event(Synthesize(text=_SYNTH_TEXT).event())

        chunks: list[bytes] = []
        rate = width = channels = 0
        t_first_chunk: float | None = None

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            event = await asyncio.wait_for(client.read_event(), timeout=60)
            if event is None:
                break

            if AudioStart.is_type(event.type):
                start = AudioStart.from_event(event)
                rate, width, channels = start.rate, start.width, start.channels
                continue

            if AudioChunk.is_type(event.type):
                chunk = AudioChunk.from_event(event)
                chunks.append(chunk.audio)
                if t_first_chunk is None:
                    t_first_chunk = time.monotonic()
                continue

            if AudioStop.is_type(event.type):
                break

        total_pcm = b"".join(chunks)
        elapsed = time.monotonic() - t0
        first_chunk_latency = (t_first_chunk - t0) if t_first_chunk else float("nan")

        print(f"[smoke] chunk count      : {len(chunks)}")
        print(f"[smoke] total PCM bytes  : {len(total_pcm)}")
        print(f"[smoke] first-chunk latency: {first_chunk_latency:.3f}s")
        print(f"[smoke] total wall-clock : {elapsed:.2f}s")
        print(f"[smoke] audio format     : {rate} Hz  {width*8}-bit  {channels}ch")

        assert len(total_pcm) > _MIN_PCM_BYTES, (
            f"PCM too small: {len(total_pcm)} bytes (expected > {_MIN_PCM_BYTES})"
        )

        # Write to a file for optional manual inspection.
        out_path = "/tmp/smoke_tts_out.wav"
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(width)
            wf.setframerate(rate)
            wf.writeframes(total_pcm)
        print(f"[smoke] WAV written to   : {out_path}")
        print("[smoke] PASS")


def wait_for_port(port: int, timeout: float = _STARTUP_TIMEOUT) -> bool:
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def main() -> None:
    port = int(os.environ.get("VERA_TTS_PORT", "10200"))
    print(f"[smoke] Waiting up to {_STARTUP_TIMEOUT}s for TTS server on port {port}…")
    if not wait_for_port(port, _STARTUP_TIMEOUT):
        print("[smoke] FAIL: server did not start in time", file=sys.stderr)
        sys.exit(1)
    print("[smoke] Server ready. Sending Synthesize request…")
    asyncio.run(run_smoke())


if __name__ == "__main__":
    main()
