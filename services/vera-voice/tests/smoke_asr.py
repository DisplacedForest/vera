"""Smoke test for the Wyoming ASR server.

Starts asr_server.py in a subprocess, sends a real WAV through the Wyoming
protocol, and asserts a non-empty Transcript comes back.

Usage (from vera-voice directory with .venv-wyoming activated):
    VERA_STT_ENGINE=parakeet python tests/smoke_asr.py
"""
import asyncio
import os
import struct
import subprocess
import sys
import time
import wave

_SERVER_URI = "tcp://localhost:10300"
_WAV_PATH = os.path.expanduser("~/vera-voice/kokoro_afheart2_000.wav")
_CHUNK_FRAMES = 4096  # frames per AudioChunk send
_STARTUP_TIMEOUT = 90  # seconds to wait for engine warm


async def run_smoke(wav_path: str) -> None:
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.asr import Transcript
    from wyoming.client import AsyncClient

    with wave.open(wav_path) as wf:
        rate = wf.getframerate()
        width = wf.getsampwidth()
        channels = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())

    async with AsyncClient.from_uri(_SERVER_URI) as client:
        # Start audio stream
        await client.write_event(AudioStart(rate=rate, width=width, channels=channels).event())

        # Send audio in chunks
        chunk_bytes = _CHUNK_FRAMES * width * channels
        for i in range(0, len(frames), chunk_bytes):
            chunk = frames[i: i + chunk_bytes]
            await client.write_event(
                AudioChunk(rate=rate, width=width, channels=channels, audio=chunk).event()
            )

        # Signal end of audio and record the wall time
        t_stop = time.monotonic()
        await client.write_event(AudioStop().event())

        # Wait for Transcript
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            event = await asyncio.wait_for(client.read_event(), timeout=30)
            if event is None:
                break
            if Transcript.is_type(event.type):
                t = Transcript.from_event(event)
                latency = time.monotonic() - t_stop
                print(f"[smoke] AudioStop→Transcript latency: {latency:.3f}s")
                print(f"[smoke] Transcript: {t.text!r}")
                assert t.text.strip(), "Transcript was empty!"
                print("[smoke] PASS")
                return
        raise RuntimeError("Timeout waiting for Transcript")


def wait_for_server(timeout: float = _STARTUP_TIMEOUT) -> bool:
    """Poll TCP 10300 until the server accepts connections."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 10300), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def main() -> None:
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server_py = os.path.join(script_dir, "asr_server.py")

    env = os.environ.copy()
    env.setdefault("VERA_STT_ENGINE", "parakeet")
    env["VERA_WARM"] = "0"  # don't also warm the old app.py models

    print("[smoke] Starting ASR server…")
    proc = subprocess.Popen(
        [sys.executable, server_py],
        cwd=script_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        print(f"[smoke] Waiting up to {_STARTUP_TIMEOUT}s for server on port 10300…")
        if not wait_for_server(_STARTUP_TIMEOUT):
            # Drain and print server output to help diagnose
            out, _ = proc.communicate(timeout=5)
            print("[smoke] Server output:\n", out)
            raise RuntimeError(f"Server did not start within {_STARTUP_TIMEOUT}s")
        print("[smoke] Server ready. Sending WAV…")
        asyncio.run(run_smoke(_WAV_PATH))
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
            if out:
                print("[smoke] Server log:\n", out[-2000:])  # last 2 kB
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
