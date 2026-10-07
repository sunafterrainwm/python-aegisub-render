"""agi::Color (libaegisub/common/color.cpp + the color grammar in parser.cpp)."""

import re
from dataclasses import dataclass

_HEX = "0123456789abcdefABCDEF"
_BLANK = " \t"


@dataclass(slots=True)
class Color:
    r: int = 0
    g: int = 0
    b: int = 0
    a: int = 0

    @staticmethod
    def from_abgr(v: int) -> Color:
        v &= 0xFFFFFFFF
        return Color(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF)

    @staticmethod
    def parse(text: str) -> Color:
        """agi::Color(std::string_view): parse with the boost::spirit grammar, keeping whatever
        the grammar's semantic actions assigned even when the full parse fails."""
        dst = Color()
        if text:
            _parse_into(dst, text)
        return dst

    def ass_style(self) -> str:
        return "&H%02X%02X%02X%02X" % (self.a, self.b, self.g, self.r)

    def ass_override(self) -> str:
        return "&H%02X%02X%02X&" % (self.b, self.g, self.r)


def _hex_exact(s: str, pos: int, n: int) -> tuple[int, int] | None:
    seg = s[pos : pos + n]
    if len(seg) == n and all(c in _HEX for c in seg):
        return int(seg, 16), pos + n
    return None


def _skip_blank(s: str, pos: int) -> int:
    while pos < len(s) and s[pos] in _BLANK:
        pos += 1
    return pos


def _rgb_component(s: str, pos: int) -> tuple[int, int] | None:
    # qi::uint_parser<unsigned char, 10, 1, 3>
    m = re.match(r"\d{1,3}", s[pos:])
    if not m:
        return None
    v = int(m.group(0))
    if v > 255:
        return None
    return v, pos + len(m.group(0))


def _comma(s: str, pos: int) -> int | None:
    pos = _skip_blank(s, pos)
    if pos < len(s) and s[pos] == ",":
        return _skip_blank(s, pos + 1)
    return None


def _css(dst: Color, s: str) -> int | None:
    """Returns the end position on success; assigns components as they are parsed."""
    for prefix, count in (("rgb(", 3), ("rgba(", 4)):
        if s.startswith(prefix):
            pos = _skip_blank(s, len(prefix))
            ok = True
            fields = ("r", "g", "b", "a")
            for i in range(count):
                if i:
                    c = _comma(s, pos)
                    if c is None:
                        ok = False
                        break
                    pos = c
                comp = _rgb_component(s, pos)
                if comp is None:
                    ok = False
                    break
                setattr(dst, fields[i], comp[0])
                pos = comp[1]
            if ok:
                pos = _skip_blank(s, pos)
                if pos < len(s) and s[pos] == ")":
                    return pos + 1
    if s.startswith("#"):
        pos = 1
        vals: list[int] = []
        for _ in range(3):
            h = _hex_exact(s, pos, 2)
            if h is None:
                break
            vals.append(h[0])
            pos = h[1]
        for name, v in zip(("r", "g", "b"), vals, strict=False):
            setattr(dst, name, v)
        if len(vals) == 3:
            return pos
        pos = 1
        vals = []
        for _ in range(3):
            h = _hex_exact(s, pos, 1)
            if h is None:
                break
            vals.append(h[0] * 17)
            pos = h[1]
        for name, v in zip(("r", "g", "b"), vals, strict=False):
            setattr(dst, name, v)
        if len(vals) == 3:
            return pos
    return None


def _parse_into(dst: Color, s: str) -> bool:
    end = _css(dst, s)
    if end is not None:
        return end == len(s)

    # ass_color = (int_ | -'&' >> -('H'|'h') >> (hex8 | hex6) >> -'&')[unpack] >> blank >> eoi
    value: int | None = None
    pos = 0
    m = re.match(r"[+-]?\d+", s)
    if m and -(2**31) <= int(m.group(0)) < 2**31:
        value = int(m.group(0))
        pos = len(m.group(0))
    else:
        p = 0
        if p < len(s) and s[p] == "&":
            p += 1
        if p < len(s) and s[p] in "Hh":
            p += 1
        h = _hex_exact(s, p, 8) or _hex_exact(s, p, 6)
        if h is not None:
            value, p = h
            if p < len(s) and s[p] == "&":
                p += 1
            pos = p
    if value is None:
        return False
    c = Color.from_abgr(value)
    dst.r, dst.g, dst.b, dst.a = c.r, c.g, c.b, c.a
    return _skip_blank(s, pos) == len(s)
