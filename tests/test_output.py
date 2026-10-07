"""stderr output: the progress bar and the --dry-run diff."""

import io

from aegisub_render.output import Output


def final_line(text: str) -> str:
    """What the console shows last: the bar redraws itself after a carriage return."""
    return text.rstrip("\n").split("\r")[-1].rstrip()


def test_progress_bar_stays_on_the_console() -> None:
    s = io.StringIO()
    out = Output(stream=s, progress=True)
    out.progress_begin("Apply karaoke template")
    out.progress(percent=40, task="Applying templates...")
    out.write("partial")
    out.progress(percent=60)  # waits: the bar cannot be drawn in the middle of a line
    out.write(" line\n")
    out.progress_end(complete=True)
    text = s.getvalue()
    assert "partial line\n" in text
    assert text.endswith("\n")
    bar = final_line(text)
    assert bar.startswith("Apply karaoke template: 100%|") and bar.endswith(", Applying templates...")


def test_progress_bar_of_a_failed_run_keeps_its_percentage() -> None:
    s = io.StringIO()
    out = Output(stream=s, progress=True)
    out.progress_begin("m")
    out.progress(percent=40)
    out.progress(title="renamed", percent=float("nan"))
    out.progress_end(complete=False)
    assert final_line(s.getvalue()).startswith("renamed:   0%|")


def test_no_progress_bar_when_disabled() -> None:
    for out in (Output(stream=(s := io.StringIO()), progress=False), Output(stream=s, quiet=True, progress=True)):
        out.progress_begin("m")
        out.progress(percent=50, task="t")
        out.progress_end(complete=True)
        assert s.getvalue() == ""


def test_diff_without_differences() -> None:
    s = io.StringIO()
    out = Output(stream=s, quiet=True)
    out.diff([])
    assert s.getvalue() == "No differences found.\n"
