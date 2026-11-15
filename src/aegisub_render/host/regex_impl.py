"""boost::u32regex (perl syntax) as used by libaegisub/lua/modules/re.cpp, emulated with `regex`.

Positions crossing the ffi boundary are byte offsets into the UTF-8 subject, like the
boost::cmatch over `const char *` that Aegisub uses.
"""

import bisect
from dataclasses import dataclass

import regex

# Bit values for the flags handed to Lua (re.moon only ORs them together, the values are opaque).
FLAGS: dict[str, int] = {
    "ICASE": 1 << 0,
    "NOSUB": 1 << 1,
    "COLLATE": 1 << 2,
    "NEWLINE_ALT": 1 << 3,
    "NO_MOD_M": 1 << 4,
    "NO_MOD_S": 1 << 5,
    "MOD_S": 1 << 6,
    "MOD_X": 1 << 7,
    "NO_EMPTY_SUBEXPRESSIONS": 1 << 8,
}


@dataclass(slots=True)
class CompiledRegex:
    pattern: regex.Pattern[str]
    nosubs: bool


@dataclass(slots=True)
class Subject:
    """A UTF-8 subject decoded once, with char <-> byte offset maps."""

    text: str
    byte_offsets: list[int]  # byte offset of each char index (len(text) + 1 entries)

    @staticmethod
    def decode(data: bytes) -> Subject:
        text = data.decode("utf-8")  # boost::u32regex throws on invalid UTF-8 as well
        offsets = [0] * (len(text) + 1)
        pos = 0
        for i, ch in enumerate(text):
            offsets[i] = pos
            pos += len(ch.encode("utf-8"))
        offsets[len(text)] = pos
        return Subject(text, offsets)

    def char_index(self, byte_offset: int) -> int:
        return bisect.bisect_left(self.byte_offsets, byte_offset)


def _newline_alt(pattern: str) -> str:
    out: list[str] = []
    escaped = False
    for ch in pattern:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            out.append(ch)
            escaped = True
        elif ch == "\n":
            out.append("|")
        else:
            out.append(ch)
    return "".join(out)


def compile_regex(pattern: str, flags: int) -> CompiledRegex:
    # boost perl syntax defaults: ^/$ match at embedded newlines, '.' matches newlines
    f = regex.VERSION0 | regex.MULTILINE | regex.DOTALL
    if flags & FLAGS["ICASE"]:
        f |= regex.IGNORECASE
    if flags & FLAGS["NO_MOD_M"]:
        f &= ~regex.MULTILINE
    if flags & FLAGS["NO_MOD_S"]:
        f &= ~regex.DOTALL
    if flags & FLAGS["MOD_S"]:
        f |= regex.DOTALL
    if flags & FLAGS["MOD_X"]:
        f |= regex.VERBOSE
    if flags & FLAGS["NEWLINE_ALT"]:
        pattern = _newline_alt(pattern)
    try:
        compiled = regex.compile(pattern, f)
    except regex.error as e:
        raise ValueError(f"{e}.  The error occurred while parsing the regular expression: '{pattern}'.") from None
    return CompiledRegex(compiled, bool(flags & FLAGS["NOSUB"]))


def search(re: CompiledRegex, subject: Subject, start_byte: int) -> regex.Match[str] | None:
    return re.pattern.search(subject.text, subject.char_index(start_byte))


def group_span_bytes(m: regex.Match[str], subject: Subject, idx: int, nosubs: bool) -> tuple[int, int] | None:
    count = 1 if nosubs else (m.re.groups + 1)
    if idx >= count:
        return None
    start, end = m.span(idx)
    if start < 0:
        return None
    return subject.byte_offsets[start], subject.byte_offsets[end]


_ESCAPES = {"a": "\a", "e": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def format_replacement(m: regex.Match[str], fmt: str, subject: str) -> str:
    """boost::match_results::format with the default (perl) format flags."""
    out: list[str] = []
    case_mode = ""  # "U", "L" until \E
    one_shot = ""  # "u", "l" for the next character

    def emit(s: str) -> None:
        nonlocal one_shot
        if not s:
            return
        if one_shot:
            s = (s[0].upper() if one_shot == "u" else s[0].lower()) + s[1:]
            one_shot = ""
        if case_mode == "U":
            s = s.upper()
        elif case_mode == "L":
            s = s.lower()
        out.append(s)

    def group(n: int) -> str:
        if n > m.re.groups:
            return ""
        return m.group(n) or ""

    i, n = 0, len(fmt)
    while i < n:
        c = fmt[i]
        if c == "$" and i + 1 < n:
            d = fmt[i + 1]
            if d == "$":
                emit("$")
                i += 2
            elif d == "&":
                emit(m.group(0))
                i += 2
            elif d == "`":
                emit(subject[: m.start()])
                i += 2
            elif d == "'":
                emit(subject[m.end() :])
                i += 2
            elif d == "+":
                last = max((g for g in range(1, m.re.groups + 1) if m.start(g) >= 0), default=0)
                emit(group(last) if last else "")
                i += 2
            elif d == "{":
                close = fmt.find("}", i + 2)
                inner = fmt[i + 2 : close] if close >= 0 else ""
                if close >= 0 and inner.isdigit():
                    emit(group(int(inner)))
                    i = close + 1
                else:
                    emit("$")
                    i += 1
            elif d.isdigit():
                j = i + 1
                while j < n and fmt[j].isdigit():
                    j += 1
                emit(group(int(fmt[i + 1 : j])))
                i = j
            else:
                emit("$")
                i += 1
        elif c == "\\" and i + 1 < n:
            d = fmt[i + 1]
            i += 2
            if d in _ESCAPES:
                emit(_ESCAPES[d])
            elif d == "x":
                if i < n and fmt[i] == "{":
                    close = fmt.find("}", i)
                    hexpart = fmt[i + 1 : close] if close >= 0 else ""
                    i = close + 1 if close >= 0 else n
                else:
                    hexpart = fmt[i : i + 2]
                    i += len(hexpart)
                try:
                    emit(chr(int(hexpart, 16)))
                except ValueError:
                    pass
            elif d == "c" and i < n:
                emit(chr(ord(fmt[i]) % 32))
                i += 1
            elif d in "ul":
                one_shot = d
            elif d in "UL":
                case_mode = d
            elif d == "E":
                case_mode = ""
            elif d.isdigit():
                emit(group(int(d)))
            else:
                emit(d)
        else:
            emit(c)
            i += 1
    return "".join(out)


def replace(re: CompiledRegex, replacement: str, subject: Subject, max_count: int) -> str:
    text = subject.text
    out: list[str] = []
    last = 0
    count = 0
    for m in re.pattern.finditer(text):
        if count >= max_count:
            break
        out.append(text[last : m.start()])
        out.append(format_replacement(m, replacement, text))
        last = m.end()
        count += 1
    out.append(text[last:])
    return "".join(out)
