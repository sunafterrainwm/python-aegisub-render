"""stderr output: script log (aegisub.debug.out), status lines, the --dry-run diff, and a progress bar."""

import io
import math
import os
import sys
import time
from typing import NoReturn, TextIO

from tqdm import tqdm

# "Apply karaoke template:  42%|████      | 00:03, Applying templates..."
_BAR_FORMAT = "{l_bar}{bar}| {elapsed}{postfix}"


def _color_diff_line(index: int, line: str) -> str:
    if index < 2:  # the ---/+++ header
        code = "1"
    elif line.startswith("@@"):
        code = "36"
    elif line.startswith("+"):
        code = "32"
    elif line.startswith("-"):
        code = "31"
    else:
        return line
    return f"\x1b[{code}m{line}\x1b[0m"


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
        terminal = bool(isatty and isatty())
        self.progress_enabled = (not quiet) and (progress if progress is not None else terminal)
        self.color = terminal and not os.environ.get("NO_COLOR")
        self._bar: tqdm[NoReturn] | None = None
        self._bar_shown = False
        self._at_line_start = True
        self._last_draw = 0.0

    def _hide_bar(self) -> None:
        if self._bar is not None and self._bar_shown:
            self._bar.clear()
            self._bar_shown = False

    def _draw_bar(self, throttle: bool = True) -> None:
        """The bar can only be drawn on a line of its own: after a partial line it waits for the rest."""
        if self._bar is None or not self._at_line_start:
            return
        now = time.monotonic()
        if throttle and now - self._last_draw < 0.1:
            return
        self._last_draw = now
        self._bar.refresh()
        self._bar_shown = True

    def write(self, text: str) -> None:
        """Text from the script (aegisub.debug.out / aegisub.log), written verbatim above the bar."""
        if not text:
            return
        self._hide_bar()
        self.stream.write(text)
        self._at_line_start = text.endswith("\n")
        self.stream.flush()
        self._draw_bar(throttle=False)

    def message(self, text: str) -> None:
        """A message from aegisub-render itself (ends with a newline)."""
        if self.quiet:
            return
        if not self._at_line_start:
            self.write("\n")
        self.write(text if text.endswith("\n") else text + "\n")

    def error(self, text: str) -> None:
        """An error from aegisub-render itself: printed even with --quiet."""
        if not self._at_line_start:
            self.write("\n")
        self.write(text if text.endswith("\n") else text + "\n")

    def diff(self, lines: list[str]) -> None:
        """A unified diff: asked for, so printed even with --quiet; colored on a terminal."""
        if not lines:
            lines = ["No differences found."]
        elif self.color:
            lines = [_color_diff_line(i, ln) for i, ln in enumerate(lines)]
        self.finish_line()
        self.write("\n".join(lines) + "\n")

    def status(self, text: str) -> None:
        if not self.quiet:
            self.message(f"[status] {text}")

    def progress_begin(self, title: str) -> None:
        """A macro or export filter starts running: Aegisub opens its progress dialog, titled after it."""
        self.progress_end(complete=False)
        if not self.progress_enabled:
            return
        self.finish_line()
        self._bar = tqdm(
            total=100, desc=title, bar_format=_BAR_FORMAT, file=self.stream, leave=True, dynamic_ncols=True
        )
        self._bar_shown = True
        self._last_draw = time.monotonic()

    def progress(self, percent: float | None = None, task: str | None = None, title: str | None = None) -> None:
        """aegisub.progress.set/task/title."""
        if self._bar is None:
            return
        if percent is not None:
            self._bar.n = min(max(percent, 0.0), 100.0) if math.isfinite(percent) else 0.0
        if task is not None:
            self._bar.set_postfix_str(task, refresh=False)
        if title is not None:
            self._bar.set_description_str(title, refresh=False)
        self._draw_bar()

    def progress_end(self, complete: bool) -> None:
        """The run is over: the bar stays on the console, filled up if the script succeeded."""
        bar = self._bar
        if bar is None:
            return
        self.finish_line()
        if complete:
            bar.n = 100
        bar.close()
        self._bar = None
        self._bar_shown = False

    def finish_line(self) -> None:
        """Leave the cursor at the start of a line (script output may not end with a newline)."""
        if not self._at_line_start:
            self.write("\n")
