"""Parakeet-MLX streaming ASR engine wrapper.

Exposes a uniform duck-typed interface so the Wyoming ASR server is engine-agnostic.
Uses parakeet-mlx 0.5.x context-manager streaming API (no finalize/reset).
"""
import os
from contextlib import contextmanager
from typing import Generator

import mlx.core as mx
import numpy as np
from parakeet_mlx import from_pretrained

PARAKEET_MODEL_ID = os.environ.get(
    "VERA_PARAKEET_MODEL", "mlx-community/parakeet-tdt-0.6b-v2"
)
_CONTEXT_SIZE = (256, 256)  # (left, right) encoder attention window
_WARM_SAMPLES = 3200        # 0.2s of silence at 16 kHz


class _StreamAdapter:
    """Thin adapter around StreamingParakeet exposing .add_audio(pcm_bytes) and .text."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def add_audio(self, pcm_bytes: bytes) -> None:
        """Accept int16 mono 16 kHz little-endian PCM bytes and feed to the model."""
        samples = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        self._inner.add_audio(mx.array(samples))

    @property
    def text(self) -> str:
        """Current best transcript (incremental; may be empty before speech)."""
        return self._inner.result.text


class ParakeetEngine:
    """Streaming ASR engine backed by Parakeet-TDT-MLX."""

    name = "parakeet"

    def __init__(self) -> None:
        self._model = from_pretrained(PARAKEET_MODEL_ID)

    def warm(self) -> None:
        """Prime the model with a short silent buffer so the first real call is fast."""
        silence = (np.zeros(_WARM_SAMPLES, dtype=np.int16)).tobytes()
        with self.stream() as s:
            s.add_audio(silence)
            _ = s.text  # force evaluation

    @contextmanager
    def stream(self) -> Generator[_StreamAdapter, None, None]:
        """Context manager that yields a _StreamAdapter for one transcription session."""
        with self._model.transcribe_stream(context_size=_CONTEXT_SIZE) as inner:
            yield _StreamAdapter(inner)
