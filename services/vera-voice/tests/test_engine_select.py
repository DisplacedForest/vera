"""Unit tests for asr_server._select_engine_class — fast, no model loading.

These tests verify that the engine-class selector returns the correct class
(not an instance) without ever instantiating or warming a model.
"""
import sys
import os

import pytest

# Ensure the vera-voice root is on the path so imports resolve.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from asr_server import _select_engine_class
from engines import ParakeetEngine, WhisperEngine


class TestSelectEngineClass:
    """_select_engine_class() maps engine names to classes, never instantiates."""

    def test_whisper_returns_whisper_engine(self):
        cls = _select_engine_class("whisper")
        assert cls is WhisperEngine

    def test_parakeet_returns_parakeet_engine(self):
        cls = _select_engine_class("parakeet")
        assert cls is ParakeetEngine

    def test_parakeet_uppercase_returns_parakeet_engine(self):
        cls = _select_engine_class("PARAKEET")
        assert cls is ParakeetEngine

    def test_unknown_name_returns_parakeet_engine(self):
        cls = _select_engine_class("bogus")
        assert cls is ParakeetEngine

    def test_empty_string_returns_parakeet_engine(self):
        cls = _select_engine_class("")
        assert cls is ParakeetEngine

    def test_none_returns_parakeet_engine(self):
        cls = _select_engine_class(None)
        assert cls is ParakeetEngine

    def test_returns_a_class_not_an_instance(self):
        """Result must be the class object, not an instantiated engine."""
        for name in ("whisper", "parakeet", "bogus", "", None):
            cls = _select_engine_class(name)
            assert isinstance(cls, type), (
                f"_select_engine_class({name!r}) returned {cls!r}, expected a class"
            )
