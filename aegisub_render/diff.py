"""--dry-run: how the result differs from the input, as a unified diff."""

import re
from difflib import SequenceMatcher

_NEWLINE = re.compile(r"\r\n|\r|\n")


def text_lines(data: bytes) -> list[str]:
    """A subtitle file's lines for display: UTF-16 with a BOM, else UTF-8 (bad bytes become U+FFFD)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", "replace")
    else:
        text = data.decode("utf-8-sig", "replace")
    lines = _NEWLINE.split(text)
    if lines[-1] == "":
        lines.pop()
    return lines


def unified_diff(old: list[str], new: list[str], old_name: str, new_name: str, context: int = 3) -> list[str]:
    """difflib.unified_diff, except that lines starting with ';' take no part (Script Info comments,
    among them the header naming the program that wrote the file). Hunk headers give the line
    numbers in the whole files and the number of lines shown."""
    a = [(n, s) for n, s in enumerate(old, 1) if not s.startswith(";")]
    b = [(n, s) for n, s in enumerate(new, 1) if not s.startswith(";")]
    matcher = SequenceMatcher(None, [s for _, s in a], [s for _, s in b])
    out: list[str] = []
    for group in matcher.get_grouped_opcodes(context):
        if not out:
            out += [f"--- {old_name}", f"+++ {new_name}"]
        first, last = group[0], group[-1]
        out.append(f"@@ -{_range(a, first[1], last[2])} +{_range(b, first[3], last[4])} @@")
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out += [" " + s for _, s in a[i1:i2]]
            else:
                out += ["-" + s for _, s in a[i1:i2]]
                out += ["+" + s for _, s in b[j1:j2]]
    return out


def _range(lines: list[tuple[int, str]], lo: int, hi: int) -> str:
    if lo == hi:  # nothing on this side: name the line before, as unified diffs do
        return f"{lines[lo - 1][0] if lo else 0},0"
    start = lines[lo][0]
    return str(start) if hi - lo == 1 else f"{start},{hi - lo}"
