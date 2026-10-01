"""Tests for bridge_logging handlers."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

TG_ROOT = Path(__file__).resolve().parents[1]
if str(TG_ROOT) not in sys.path:
    sys.path.insert(0, str(TG_ROOT))

from bridge_logging import _DateRotatingFileHandler, _SafeStreamHandler


def test_emoji_roundtrip(tmp_path: Path) -> None:
    """A log line containing emoji must be read back verbatim from the log file."""
    handler = _DateRotatingFileHandler(tmp_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_emoji_roundtrip")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    msg = "🚀 BUY XAUUSD @ 3000 ✅ SL: 2990 TP: 3010"
    logger.info(msg)
    handler.close()
    logger.removeHandler(handler)

    log_files = list(tmp_path.glob("tradingo_*.log"))
    assert log_files, "No log file was created"
    content = log_files[0].read_text(encoding="utf-8")
    assert msg in content, f"Emoji message not found. Content: {content!r}"


def test_date_rotation_creates_new_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Handler must open a new file when the calendar date changes."""
    from datetime import datetime

    call_count = 0
    dates = ["20260818", "20260819"]

    original_now = datetime.now

    def fake_now():
        nonlocal call_count
        d = dates[min(call_count, len(dates) - 1)]
        call_count += 1
        dt = original_now()
        return dt.replace(
            year=int(d[:4]), month=int(d[4:6]), day=int(d[6:8])
        )

    monkeypatch.setattr("bridge_logging.datetime", type("_DT", (), {"now": staticmethod(fake_now)})())

    handler = _DateRotatingFileHandler(tmp_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_date_rotation")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("day one message")
    logger.info("day two message")
    handler.close()
    logger.removeHandler(handler)

    log_files = sorted(tmp_path.glob("tradingo_*.log"))
    assert len(log_files) == 2, f"Expected 2 log files, got {[f.name for f in log_files]}"
    assert "20260818" in log_files[0].name
    assert "20260819" in log_files[1].name


def test_safe_stream_handler_tolerates_emoji(tmp_path: Path) -> None:
    """_SafeStreamHandler must not drop lines with emoji on cp1252 streams."""
    import io

    # Simulate a cp1252 stream that cannot encode emoji
    buf = io.StringIO()

    class _Cp1252Stream:
        encoding = "cp1252"
        errors = "strict"

        def write(self, s: str) -> None:
            s.encode("cp1252")  # raises UnicodeEncodeError on emoji
            buf.write(s)

        def flush(self) -> None:
            pass

    handler = _SafeStreamHandler(stream=_Cp1252Stream())  # type: ignore[arg-type]
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_safe_stream")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    msg = "🚀 BUY XAUUSD @ 3000 ✅"

    # Must not raise, and the line must be written (with replacements)
    logger.info(msg)
    logger.removeHandler(handler)

    content = buf.getvalue()
    assert content, "Nothing was written to the stream — line was dropped entirely"
    assert "BUY XAUUSD" in content, (
        f"Payload text missing from output. Buffer: {content!r}"
    )
    # Emoji should be replaced, not present verbatim
    assert "🚀" not in content, f"Emoji survived cp1252 encoding — replacement did not happen. Buffer: {content!r}"
