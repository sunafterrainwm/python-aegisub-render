"""The styling half of the WebVTT port: CSS colours, `::cue` rules, and the override-tag stack.

None of this is Aegisub: WebVTT is not one of its formats, so there is no upstream to be faithful
to. The mapping chosen here is the one a libass renderer would draw the same way a browser does,
as far as ASS can express it:

=========================== ==========================================================
WebVTT                      ASS
=========================== ==========================================================
``color``                   ``\\c`` (+ ``\\1a`` when the colour is not opaque)
``background-color``        ``\\4c`` (+ ``\\4a``); ASS draws it as the shadow colour, so
                            it only shows with ``BorderStyle: 3``, which a ``::cue``
                            rule with a background sets on the style
``font-family``             ``\\fn`` (the first family, unquoted)
``font-size``               ``\\fs`` (``px`` only; ``%``/``em`` are relative to a size
                            WebVTT never states, so they are ignored)
``font-weight``             ``\\b`` (``bold`` or >= 600)
``font-style``              ``\\i`` (``italic``/``oblique``)
``text-decoration``         ``\\u`` (``underline``), ``\\s`` (``line-through``)
=========================== ==========================================================

A rule reached through a selector that names the whole cue (``::cue``) or a voice
(``::cue(v[voice="X"])``) becomes an `AssStyle`; one reached through a class
(``::cue(.loud)``) becomes inline tags, because a class applies to a span inside a cue.
Everything else in the CSS is ignored.
"""

import re
from dataclasses import dataclass, field, replace

from aegisub_render.ass.color import Color
from aegisub_render.ass.entries import AssStyle

# The eight colours (and their backgrounds) WebVTT gives every player, spec 6.2 "WebVTT cue text
# span start tag": the class names a <c> tag may use without any CSS of its own.
DEFAULT_CLASS_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "lime": (0, 255, 0),
    "cyan": (0, 255, 255),
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "magenta": (255, 0, 255),
    "blue": (0, 0, 255),
    "black": (0, 0, 0),
}

# The CSS colour keywords worth having: the 16 HTML 4 ones plus the two WebVTT needs.
_CSS_COLORS: dict[str, tuple[int, int, int, int]] = {
    "black": (0, 0, 0, 0),
    "silver": (192, 192, 192, 0),
    "gray": (128, 128, 128, 0),
    "grey": (128, 128, 128, 0),
    "white": (255, 255, 255, 0),
    "maroon": (128, 0, 0, 0),
    "red": (255, 0, 0, 0),
    "purple": (128, 0, 128, 0),
    "fuchsia": (255, 0, 255, 0),
    "magenta": (255, 0, 255, 0),
    "green": (0, 128, 0, 0),
    "lime": (0, 255, 0, 0),
    "olive": (128, 128, 0, 0),
    "yellow": (255, 255, 0, 0),
    "navy": (0, 0, 128, 0),
    "blue": (0, 0, 255, 0),
    "teal": (0, 128, 128, 0),
    "aqua": (0, 255, 255, 0),
    "cyan": (0, 255, 255, 0),
    "orange": (255, 165, 0, 0),
    "transparent": (0, 0, 0, 255),
}

_RGB_FUNC_RE = re.compile(r"rgba?\(([^)]*)\)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")


def parse_css_color(value: str) -> Color | None:
    """A CSS colour as an agi::Color (whose `a` is ASS transparency: 0 opaque, 255 invisible)."""
    value = value.strip().lower()
    if not value:
        return None
    if value in _CSS_COLORS:
        r, g, b, a = _CSS_COLORS[value]
        return Color(r, g, b, a)
    m = _RGB_FUNC_RE.fullmatch(value)
    if m is not None:
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",") if p.strip()]
        nums: list[float] = []
        for part in parts:
            n = _NUMBER_RE.match(part)
            if n is None:
                return None
            scale = 2.55 if part.endswith("%") else 1.0
            nums.append(float(n.group(0)) * scale)
        if len(nums) < 3:
            return None
        alpha = 0
        if len(nums) >= 4:
            # a CSS alpha of 0..1 (or 0..255 after the % scaling above) is ASS transparency
            # counted the other way round
            opacity = nums[3] / 255.0 if parts[3].endswith("%") else nums[3]
            alpha = max(0, min(255, round((1.0 - opacity) * 255)))
        red, green, blue = (max(0, min(255, round(n))) for n in nums[:3])
        return Color(red, green, blue, alpha)
    if value.startswith("#"):
        return Color.parse(value)  # agi::Color already reads #rgb and #rrggbb
    return None


@dataclass(slots=True, frozen=True)
class CueStyle:
    """What one `::cue` rule sets; `None` means the rule says nothing about that property."""

    color: Color | None = None
    background: Color | None = None
    font: str | None = None
    fontsize: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strikeout: bool | None = None

    def __bool__(self) -> bool:
        return any(getattr(self, f) is not None for f in self.__slots__)

    def merge(self, other: CueStyle) -> CueStyle:
        """This style with `other`'s properties on top (the inner rule wins)."""
        changes = {f: v for f in self.__slots__ if (v := getattr(other, f)) is not None}
        return replace(self, **changes)

    def apply_to(self, style: AssStyle) -> None:
        """Write this rule into an AssStyle (a `::cue` or `::cue(v[voice=..])` selector)."""
        if self.color is not None:
            style.primary = Color(self.color.r, self.color.g, self.color.b, self.color.a)
        if self.background is not None:
            # ASS only paints a box behind the text with BorderStyle 3, where BackColour is it
            style.shadow = Color(self.background.r, self.background.g, self.background.b, self.background.a)
            style.borderstyle = 3
        if self.font is not None:
            style.font = self.font
        if self.fontsize is not None:
            style.fontsize = self.fontsize
        if self.bold is not None:
            style.bold = self.bold
        if self.italic is not None:
            style.italic = self.italic
        if self.underline is not None:
            style.underline = self.underline
        if self.strikeout is not None:
            style.strikeout = self.strikeout

    def tags(self) -> dict[str, str]:
        """This rule as override tags, keyed by tag name so a nested span can restore them."""
        out: dict[str, str] = {}
        if self.color is not None:
            out["c"] = "{\\c%s}" % self.color.ass_override()
            if self.color.a:
                out["1a"] = "{\\1a&H%02X&}" % self.color.a
        if self.background is not None:
            out["4c"] = "{\\4c%s}" % self.background.ass_override()
            if self.background.a:
                out["4a"] = "{\\4a&H%02X&}" % self.background.a
        if self.font is not None:
            out["fn"] = "{\\fn%s}" % self.font
        if self.fontsize is not None:
            size = self.fontsize
            out["fs"] = "{\\fs%s}" % (f"{size:g}")
        for name, value in (("b", self.bold), ("i", self.italic), ("u", self.underline), ("s", self.strikeout)):
            if value is not None:
                out[name] = "{\\%s%d}" % (name, int(value))
        return out


BOLD = CueStyle(bold=True)
ITALIC = CueStyle(italic=True)
UNDERLINE = CueStyle(underline=True)


def _font_family(value: str) -> str:
    first = value.split(",", maxsplit=1)[0].strip()
    if len(first) >= 2 and first[0] in "'\"" and first[-1] == first[0]:
        first = first[1:-1]
    return first


def _font_size(value: str) -> float | None:
    value = value.strip().lower()
    if not value.endswith("px"):
        return None  # %, em, rem and the keywords are relative to a size WebVTT never states
    m = _NUMBER_RE.fullmatch(value[:-2].strip())
    return float(m.group(0)) if m is not None else None


def _font_weight(value: str) -> bool | None:
    value = value.strip().lower()
    if value in ("bold", "bolder"):
        return True
    if value in ("normal", "lighter"):
        return False
    m = _NUMBER_RE.fullmatch(value)
    return float(m.group(0)) >= 600 if m is not None else None


def declarations_to_style(decls: dict[str, str]) -> CueStyle:
    """One CSS rule body, as the properties this port understands."""
    fields: dict[str, object] = {}
    for prop, value in decls.items():
        if prop == "color":
            colour = parse_css_color(value)
            if colour is not None:
                fields["color"] = colour
        elif prop in ("background-color", "background"):
            colour = parse_css_color(value)
            if colour is not None:
                fields["background"] = colour
        elif prop == "font-family":
            fields["font"] = _font_family(value)
        elif prop == "font-size":
            size = _font_size(value)
            if size is not None:
                fields["fontsize"] = size
        elif prop == "font-weight":
            weight = _font_weight(value)
            if weight is not None:
                fields["bold"] = weight
        elif prop == "font-style":
            fields["italic"] = value.strip().lower() in ("italic", "oblique")
        elif prop == "text-decoration":
            words = value.strip().lower().split()
            if "underline" in words:
                fields["underline"] = True
            if "line-through" in words:
                fields["strikeout"] = True
            if "none" in words:
                fields["underline"] = False
                fields["strikeout"] = False
    return CueStyle(**fields)  # pyright: ignore[reportArgumentType]


_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CUE_SELECTOR_RE = re.compile(r"::cue(?:\((.*)\))?\s*$", re.DOTALL)
_VOICE_SELECTOR_RE = re.compile(r"""^v\[voice\s*=\s*['"]?([^'"\]]*)['"]?\]$""")


@dataclass(slots=True)
class CueRules:
    """Everything the STYLE blocks of a file say, sorted by what the selector applies to."""

    cue: CueStyle = CueStyle()
    classes: dict[str, CueStyle] = field(default_factory=dict[str, CueStyle])
    voices: dict[str, CueStyle] = field(default_factory=dict[str, CueStyle])

    def class_style(self, name: str) -> CueStyle:
        """The rule for a `<c.name>` class: its CSS, or the colour WebVTT gives the name."""
        lower = name.lower()
        if lower in self.classes:
            return self.classes[lower]
        if lower.startswith("bg_") and lower[3:] in DEFAULT_CLASS_COLORS:
            return CueStyle(background=Color(*DEFAULT_CLASS_COLORS[lower[3:]]))
        if lower in DEFAULT_CLASS_COLORS:
            return CueStyle(color=Color(*DEFAULT_CLASS_COLORS[lower]))
        return CueStyle()

    def add_css(self, css: str) -> None:
        """Read one STYLE block."""
        css = re.sub(r"/\*.*?\*/", " ", css, flags=re.DOTALL)
        for rule in _RULE_RE.finditer(css):
            style = declarations_to_style(_declarations(rule.group(2)))
            if not style:
                continue
            for selector in rule.group(1).split(","):
                self._add_rule(selector.strip(), style)

    def _add_rule(self, selector: str, style: CueStyle) -> None:
        m = _CUE_SELECTOR_RE.search(selector)
        if m is None:
            return  # not a ::cue rule: nothing in a WebVTT file's CSS can match anything else
        inner = (m.group(1) or "").strip()
        if not inner:
            self.cue = self.cue.merge(style)
            return
        voice = _VOICE_SELECTOR_RE.match(inner)
        if voice is not None:
            name = voice.group(1)
            self.voices[name] = self.voices.get(name, CueStyle()).merge(style)
            return
        if inner.startswith("."):
            for name in inner[1:].split("."):
                if name:
                    self.classes[name.lower()] = self.classes.get(name.lower(), CueStyle()).merge(style)


def _declarations(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for decl in body.split(";"):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        out[prop.strip().lower()] = value.strip()
    return out


RESET_TAGS = {
    "c": "{\\c}",
    "1a": "{\\1a}",
    "4c": "{\\4c}",
    "4a": "{\\4a}",
    "fn": "{\\fn}",
    "fs": "{\\fs}",
    "b": "{\\b}",
    "i": "{\\i}",
    "u": "{\\u}",
    "s": "{\\s}",
}


class TagStack:
    """The override tags in force inside nested WebVTT spans.

    Generalises what SrtTagParser does for `<font>`: each span pushes the tags its style sets,
    only the ones that changed are written, and closing it restores the enclosing values -- or
    the bare tag (`{\\c}`, `{\\b}`), which resets that property to the line's style.
    """

    def __init__(self) -> None:
        self.frames: list[dict[str, str]] = [{}]

    @property
    def current(self) -> dict[str, str]:
        return self.frames[-1]

    def push(self, style: CueStyle, out: list[str]) -> None:
        old = self.current
        new = {**old, **style.tags()}
        for key, value in new.items():
            if old.get(key, "") != value:
                out.append(value)
        self.frames.append(new)

    def pop(self, out: list[str]) -> None:
        if len(self.frames) == 1:
            return
        cur = self.frames.pop()
        old = self.current
        for key, value in cur.items():
            if old.get(key, "") != value:
                out.append(old.get(key) or RESET_TAGS.get(key, ""))
