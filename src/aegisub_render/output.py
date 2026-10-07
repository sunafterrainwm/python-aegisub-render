"""stderr output: script log (aegisub.debug.out), status lines, and a single-line progress display."""

import io
import sys
import time
from typing import TextIO


def use_utf8_for_pipes() -> None:
    """Consoles get proper Unicode from Python already; make redirected output UTF-8 too."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper) and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


class Output:
    def __init__(self, stream: TextIO | None = None, quiet: bool = False, progress: bool | None = None) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.quiet = quiet
        isatty = getattr(self.stream, "isatty", None)
        self.progress_enabled = (not quiet) and (progress if progress is not None else bool(isatty and isatty()))
        self._progress_shown = False
        self._task = ""
        self._title = ""
        self._percent = 0.0
        self._at_line_start = True
        self._last_draw = 0.0

    def _clear_progress(self) -> None:
        if self._progress_shown:
            self.stream.write("\r\x1b[2K")
            self._progress_shown = False

    def write(self, text: str) -> None:
        """Text from the script (aegisub.debug.out / aegisub.log), written verbatim."""
        if not text:
            return
        self._clear_progress()
        self.stream.write(text)
        self._at_line_start = text.endswith("\n")
        self.stream.flush()

    def message(self, text: str) -> None:
        """A message from aegisub-render itself (ends with a newline)."""
        if self.quiet:
            return
        if not self._at_line_start:
            self.write("\n")
        self.write(text if text.endswith("\n") else text + "\n")

    def status(self, text: str) -> None:
        if not self.quiet:
            self.message(f"[status] {text}")

    def progress(self, percent: float | None = None, task: str | None = None, title: str | None = None) -> None:
        if percent is not None:
            self._percent = percent
        if task is not None:
            self._task = task
        if title is not None:
            self._title = title
        if not self.progress_enabled or not self._at_line_start:
            return
        now = time.monotonic()
        if now - self._last_draw < 0.1:
            return
        self._last_draw = now
        label = " - ".join(x for x in (self._title, self._task) if x)
        self.stream.write(f"\r\x1b[2K[{self._percent:5.1f}%] {label}"[:200])
        self.stream.flush()
        self._progress_shown = True

    def progress_done(self) -> None:
        self._clear_progress()
        self.stream.flush()
