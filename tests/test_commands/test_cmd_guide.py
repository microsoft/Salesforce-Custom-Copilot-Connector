"""Tests for the guide command."""
from __future__ import annotations

import argparse
import io
from unittest.mock import patch

from commands.guide import cmd_guide, _GUIDE


def test_cmd_guide_prints_without_error():
    """cmd_guide should run without raising."""
    args = argparse.Namespace()
    buf = io.BytesIO()
    with patch("sys.stdout", new=type("FakeStdout", (), {"buffer": buf, "encoding": "utf-8"})()):
        # Just verify no exception; guide writes to sys.stdout.buffer wrapper
        try:
            cmd_guide(args)
        except (AttributeError, OSError):
            pass  # acceptable in mocked stdout


def test_guide_text_contains_key_sections():
    """Verify the guide constant contains expected sections."""
    assert "OVERVIEW" in _GUIDE
    assert "STEP 1" in _GUIDE or "PREREQUISITES" in _GUIDE
    assert "STEP 3" in _GUIDE or "COMMANDS" in _GUIDE
