"""
Logging handlers for tradingo_bridge.

_DateRotatingFileHandler  — writes to tradingo_YYYYMMDD.log and rotates the
                             file at midnight without renaming the previous one.
_SafeStreamHandler        — tolerates unencodable characters on legacy consoles
                             (cp1252) by replacing them instead of dropping
                             the entire log line.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


class _DateRotatingFileHandler(logging.Handler):
    """Writes to tradingo_YYYYMMDD.log; rotates at midnight without renaming."""

    def __init__(self, log_dir: Path, encoding: str = "utf-8") -> None:
        super().__init__()
        self._log_dir = log_dir
        self._encoding = encoding
        self._current_date: str = ""
        self._fh: logging.FileHandler | None = None
        self._rotate()

    def _rotate(self) -> None:
        if self._fh is not None:
            self._fh.close()
        today = datetime.now().strftime("%Y%m%d")
        self._fh = logging.FileHandler(
            self._log_dir / f"tradingo_{today}.log",
            encoding=self._encoding,
        )
        if self.formatter:
            self._fh.setFormatter(self.formatter)
        self._current_date = today

    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        super().setFormatter(fmt)
        if self._fh is not None:
            self._fh.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        today = datetime.now().strftime("%Y%m%d")
        if today != self._current_date:
            self._rotate()
        assert self._fh is not None
        self._fh.emit(record)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        super().close()


class _SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that replaces unencodable characters instead of dropping the line."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            enc = getattr(stream, "encoding", None) or "utf-8"
            try:
                stream.write(msg + self.terminator)
            except UnicodeEncodeError:
                safe = msg.encode(enc, errors="replace").decode(enc)
                stream.write(safe + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)
