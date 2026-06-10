"""Whisper-MLX batch ASR engine — fallback when Parakeet is unavailable.

Buffers all PCM chunks during the stream session and transcribes as a batch
when .text is read. Mirrors the ParakeetEngine duck-typed interface.
"""
import os
import tempfile
import wave
from contextlib import contextmanager
from typing import Generator

import mlx_whisper

WHISPER_MODEL_ID = os.environ.get("VERA_STT_MODEL", "mlx-community/whisper-small-mlx")
_SAMPLE_RATE = 16000


class _WhisperStreamAdapter:
    """Buffers int16 PCM bytes; transcribes the whole buffer on .text read."""

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self._buf: bytearray = bytearray()
        self._cached_text: str | None = None

    def add_audio(self, pcm_bytes: bytes) -> None:
        """Buffer int16 mono 16 kHz little-endian PCM bytes."""
        self._buf.extend(pcm_bytes)
        self._cached_text = None  # invalidate cache on new audio

    @property
    def text(self) -> str:
        """Transcribe the full buffer (batch) and return the result."""
        if not self._buf:
            return ""
        if self._cached_text is not None:
            return self._cached_text
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            with wave.open(tmp_path, "w") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(_SAMPLE_RATE)
                # _buf is already little-endian int16 PCM — write directly
                wf.writeframes(bytes(self._buf))
            r = mlx_whisper.transcribe(tmp_path, path_or_hf_repo=self._model_id)
            self._cached_text = r["text"].strip()
        finally:
            os.unlink(tmp_path)
        return self._cached_text


class WhisperEngine:
    """Batch ASR engine backed by mlx-whisper; mirrors ParakeetEngine interface."""

    name = "whisper"

    def __init__(self, model_id: str = WHISPER_MODEL_ID) -> None:
        self._model_id = model_id

    def warm(self) -> None:
        """Prime Whisper by transcribing a short silent buffer (caches model weights)."""
        silence = (b"\x00\x00") * 3200  # 0.2s silence as int16 little-endian bytes
        with self.stream() as s:
            s.add_audio(silence)
            _ = s.text

    @contextmanager
    def stream(self) -> Generator[_WhisperStreamAdapter, None, None]:
        """Context manager yielding a _WhisperStreamAdapter for one transcription session."""
        yield _WhisperStreamAdapter(self._model_id)
