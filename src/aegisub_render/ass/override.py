"""Dialogue block / override tag parsing (src/ass_dialogue.cpp ParseTags, src/ass_override.cpp)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import override

from aegisub_render._crt import atof, atoi, strtol_hex
from aegisub_render.cutil import float_to_string, mid, trim


class VarType(Enum):
    INT = auto()
    FLOAT = auto()
    TEXT = auto()
    BOOL = auto()
    BLOCK = auto()


class ParamClass(Enum):
    NORMAL = auto()
    ABSOLUTE_SIZE_X = auto()
    ABSOLUTE_SIZE_Y = auto()
    ABSOLUTE_POS_X = auto()
    ABSOLUTE_POS_Y = auto()
    RELATIVE_SIZE_X = auto()
    RELATIVE_SIZE_Y = auto()
    RELATIVE_TIME_START = auto()
    RELATIVE_TIME_END = auto()
    KARAOKE = auto()
    DRAWING = auto()
    ALPHA = auto()
    COLOR = auto()


NOT_OPTIONAL = 0xFF
OPT_1, OPT_2, OPT_3, OPT_4 = 0x01, 0x02, 0x04, 0x08


@dataclass(slots=True)
class _ParamProto:
    optional: int
    type: VarType
    classification: ParamClass


@dataclass(slots=True)
class _TagProto:
    name: str
    params: list[_ParamProto] = field(default_factory=list[_ParamProto])


def _single(name: str, t: VarType, c: ParamClass = ParamClass.NORMAL) -> _TagProto:
    return _TagProto(name, [_ParamProto(NOT_OPTIONAL, t, c)])


def _multi(name: str, *params: tuple[VarType, ParamClass, int]) -> _TagProto:
    return _TagProto(name, [_ParamProto(o, t, c) for t, c, o in params])


_I, _F, _T, _B, _BL = (
    VarType.INT,
    VarType.FLOAT,
    VarType.TEXT,
    VarType.BOOL,
    VarType.BLOCK,
)
_PC = ParamClass
_N = NOT_OPTIONAL

# load_protos(): longer tag names must appear before shorter tag names
PROTOS: list[_TagProto] = [
    _single("\\alpha", _T, _PC.ALPHA),
    _single("\\bord", _F, _PC.ABSOLUTE_SIZE_Y),
    _single("\\xbord", _F, _PC.ABSOLUTE_SIZE_X),
    _single("\\ybord", _F, _PC.ABSOLUTE_SIZE_Y),
    _single("\\shad", _F, _PC.ABSOLUTE_SIZE_Y),
    _single("\\xshad", _F, _PC.ABSOLUTE_SIZE_X),
    _single("\\yshad", _F, _PC.ABSOLUTE_SIZE_Y),
    _multi(
        "\\fade",
        (_I, _PC.NORMAL, _N),
        (_I, _PC.NORMAL, _N),
        (_I, _PC.NORMAL, _N),
        (_I, _PC.RELATIVE_TIME_START, _N),
        (_I, _PC.RELATIVE_TIME_START, _N),
        (_I, _PC.RELATIVE_TIME_START, _N),
        (_I, _PC.RELATIVE_TIME_START, _N),
    ),
    _multi(
        "\\move",
        (_F, _PC.ABSOLUTE_POS_X, _N),
        (_F, _PC.ABSOLUTE_POS_Y, _N),
        (_F, _PC.ABSOLUTE_POS_X, _N),
        (_F, _PC.ABSOLUTE_POS_Y, _N),
        (_I, _PC.RELATIVE_TIME_START, _N),
        (_I, _PC.RELATIVE_TIME_START, _N),
    ),
    _multi(
        "\\clip",
        (_I, _PC.ABSOLUTE_POS_X, _N),
        (_I, _PC.ABSOLUTE_POS_Y, _N),
        (_I, _PC.ABSOLUTE_POS_X, _N),
        (_I, _PC.ABSOLUTE_POS_Y, _N),
    ),
    _multi("\\clip", (_I, _PC.NORMAL, OPT_2), (_T, _PC.DRAWING, _N)),
    _multi(
        "\\iclip",
        (_I, _PC.ABSOLUTE_POS_X, _N),
        (_I, _PC.ABSOLUTE_POS_Y, _N),
        (_I, _PC.ABSOLUTE_POS_X, _N),
        (_I, _PC.ABSOLUTE_POS_Y, _N),
    ),
    _multi("\\iclip", (_I, _PC.NORMAL, OPT_2), (_T, _PC.DRAWING, _N)),
    _single("\\fscx", _F, _PC.RELATIVE_SIZE_X),
    _single("\\fscy", _F, _PC.RELATIVE_SIZE_Y),
    _multi("\\pos", (_F, _PC.ABSOLUTE_POS_X, _N), (_F, _PC.ABSOLUTE_POS_Y, _N)),
    _multi("\\org", (_F, _PC.ABSOLUTE_POS_X, _N), (_F, _PC.ABSOLUTE_POS_Y, _N)),
    _single("\\pbo", _I, _PC.ABSOLUTE_SIZE_Y),
    _multi("\\fad", (_I, _PC.RELATIVE_TIME_START, _N), (_I, _PC.RELATIVE_TIME_END, _N)),
    _single("\\fsp", _F, _PC.ABSOLUTE_SIZE_Y),
    _single("\\frx", _F),
    _single("\\fry", _F),
    _single("\\frz", _F),
    _single("\\fr", _F),
    _single("\\fax", _F),
    _single("\\fay", _F),
    _single("\\1c", _T, _PC.COLOR),
    _single("\\2c", _T, _PC.COLOR),
    _single("\\3c", _T, _PC.COLOR),
    _single("\\4c", _T, _PC.COLOR),
    _single("\\1a", _T, _PC.ALPHA),
    _single("\\2a", _T, _PC.ALPHA),
    _single("\\3a", _T, _PC.ALPHA),
    _single("\\4a", _T, _PC.ALPHA),
    _single("\\fe", _T),
    _single("\\ko", _I, _PC.KARAOKE),
    _single("\\kf", _I, _PC.KARAOKE),
    _single("\\be", _I),
    _single("\\blur", _F),
    _single("\\fn", _T),
    _single("\\fs+", _F),
    _single("\\fs-", _F),
    _single("\\fs", _F, _PC.ABSOLUTE_SIZE_Y),
    _single("\\an", _I),
    _single("\\c", _T, _PC.COLOR),
    _single("\\b", _I),
    _single("\\i", _B),
    _single("\\u", _B),
    _single("\\s", _B),
    _single("\\a", _I),
    _single("\\k", _I, _PC.KARAOKE),
    _single("\\K", _I, _PC.KARAOKE),
    _single("\\q", _I),
    _single("\\p", _I),
    _single("\\r", _T),
    _multi(
        "\\t",
        (_I, _PC.RELATIVE_TIME_START, OPT_3 | OPT_4),
        (_I, _PC.RELATIVE_TIME_START, OPT_3 | OPT_4),
        (_F, _PC.NORMAL, OPT_2 | OPT_4),
        (_BL, _PC.NORMAL, _N),
    ),
]
assert len(PROTOS) == 56


@dataclass(slots=True)
class OverrideParam:
    type: VarType
    classification: ParamClass
    value: str = ""
    omitted: bool = True
    # AssOverrideParameter::block: a VarType.BLOCK parameter (`\t`'s last one) keeps the tags it
    # holds as a block of its own once something asks for them, and the text then comes from it.
    block: Block | None = None

    def get_block(self) -> Block:
        """`Get<AssDialogueBlockOverride*>()`: this parameter's tags, parsed on first use."""
        if self.block is None:
            text = self.value
            self.block = Block(BlockType.OVERRIDE, text, parse_override_tags(text))
        return self.block

    def get_str(self) -> str:
        """`Get<std::string>()`: the block's text (without its braces) once there is a block."""
        if self.block is not None:
            text = self.block.get_text()
            return text.removeprefix("{").removesuffix("}")
        return self.value

    def set_str(self, new_value: str) -> None:
        """`Set<std::string>`: the parameter is present from now on, and has no block."""
        self.omitted = False
        self.value = new_value
        self.block = None

    def set_int(self, new_value: int) -> None:
        """`Set<int>`, including the `&HXX&` form an alpha parameter is written in."""
        if self.classification is ParamClass.ALPHA:
            self.set_str("&H%02X&" % mid(0, new_value, 255))
        else:
            self.set_str(str(new_value))

    def set_double(self, new_value: float) -> None:
        """`Set<double>`: utils.cpp's float_to_string (three decimals, trailing zeros trimmed)."""
        self.set_str(float_to_string(new_value))

    def set_bool(self, new_value: bool) -> None:
        """`Set<bool>`, which is `Set<int>` of 0 or 1."""
        self.set_int(int(new_value))

    def get_double(self) -> float:
        """`Get<double>()`: atof of the string form."""
        if self.omitted:
            raise ValueError("AssOverrideParameter::Get() called on omitted parameter")
        return atof(self.get_str())

    def get_int(self, default: int | None = None) -> int:
        if self.omitted:
            if default is None:
                raise ValueError("AssOverrideParameter::Get() called on omitted parameter")
            return default
        if self.classification is ParamClass.ALPHA:
            pos = next(
                (i for i, c in enumerate(self.value) if c in "0123456789abcdefABCDEF"),
                len(self.value),
            )
            return mid(0, strtol_hex(self.value[pos:]), 255)
        return atoi(self.get_str())


def _tokenize(text: str) -> list[str]:
    params: list[str] = []
    if not text:
        return params
    if text[0] != "(":
        params.append(trim(text))
        return params
    i, n = 0, len(text)
    depth = 1
    while i < n and depth > 0:
        i += 1
        start = i
        while i < n and depth > 0:
            c = text[i]
            if c == "," and depth == 1:
                break
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        params.append(trim(text[start:i]))
    if i + 1 < n:
        params.append(text[i + 1 :])
    return params


class OverrideTag:
    __slots__ = ("name", "params", "valid")

    def __init__(self, text: str) -> None:
        self.params: list[OverrideParam] = []
        self.valid = False
        self.name = text
        for idx, proto in enumerate(PROTOS):
            if text.startswith(proto.name):
                self.name = proto.name
                self._parse_parameters(text[len(proto.name) :], idx)
                self.valid = True
                return

    def _parse_parameters(self, text: str, proto_idx: int) -> None:
        self.params = []
        plist = _tokenize(text)
        total = len(plist)
        flag = 1 << (total - 1) if 0 < total <= 8 else 0
        if self.name in ("\\clip", "\\iclip") and total != 4:
            proto_idx += 1
        cur = 0
        for proto in PROTOS[proto_idx].params:
            param = OverrideParam(proto.type, proto.classification)
            self.params.append(param)
            if not (proto.optional & flag) or cur >= total:
                continue
            param.value = plist[cur]
            param.omitted = False
            cur += 1

    @override
    def __str__(self) -> str:
        result = self.name
        parens = len(self.params) > 1
        if parens:
            result += "("
        result += ",".join(p.get_str() for p in self.params if not p.omitted)
        if parens:
            result += ")"
        return result


_strict_tags = False


def strict_tag_parsing() -> bool:
    """Whether parse_override_tags / parse_dialogue_blocks count brackets by default."""
    return _strict_tags


def set_strict_tag_parsing(strict: bool) -> bool:
    r"""Switch the default splitting of `{...}` blocks for this process; returns the old value.

    False (the default) is Aegisub's: `depth` only rises on a `(` seen at depth 0, so a `\t`
    with tags in its parameters is split in the middle (see parse_override_tags). True counts
    brackets properly, which keeps such a tag in one piece and makes `str(tag)` join back into
    the input text.

    This changes what every caller in the process gets, including `parse_karaoke_syllables` and
    `AssDialogue.get_stripped_text`, so it is for a program that has decided it wants correct
    splitting everywhere -- not for one call (pass `strict=True` for that). The byte-identical
    output this tool is built for is only tested with the default, so leave it alone while
    rendering; a caller that does flip it should do so once, at startup.
    """
    global _strict_tags  # noqa: PLW0603 - a process-wide switch is the point
    previous = _strict_tags
    _strict_tags = strict
    return previous


def parse_override_tags(text: str, strict: bool | None = None) -> list[OverrideTag]:
    r"""AssDialogueBlockOverride::ParseTags: split a `{...}` block's contents into tags.

    With `strict=False` (the default, unless set_strict_tag_parsing changed it) this is faithful
    to the C++, including this: `depth` only goes up on a `(` seen at depth 0, so the first `)`
    ends the parameter list however many `(` are nested inside it. A `\t` with a tag inside its
    parameters is therefore split in the middle::

        \t(0,248,\clip(690,0,1230,1080)\t(586,922,\clip(1230,0,1230,1080)))
        -> \t(0,248,\clip(690,0,1230,1080)        and \t(586,922,\clip(1230,0,1230,1080)))

    and joining `str(tag)` back together does not give the input text back: each tag closes its
    own parenthesis (AssOverrideTag::operator std::string()), so the second half comes out as
    `\t(586,922,\clip(1230,0,1230,1080),))`. kara-templater writes this shape a lot. The
    parameters are shifted as well: the first half gets an empty accel, the second a stray `)`.
    Aegisub only ever writes `Text` back verbatim unless a line was actually edited, which is why
    this does not corrupt files there -- and why `AssFile.serialize()` is byte-identical.

    What is lost is the bytes, not the picture: libass treats the trailing `)` as junk inside
    `\t`, and a real assrender comparison of the nested, the flattened and this `,))` form gave
    pixel-identical masks. So rebuilding a line this way is a fidelity problem (the file no
    longer matches what Aegisub would write), not a rendering bug.

    `strict=True` counts brackets, so the example above stays one `\t` whose last parameter is
    the nested animation, and `"".join(str(t) for t in tags)` gives the input text back. Use it
    to read or rebuild lines of your own; `strict=False` is the split Aegisub itself sees.
    `None` follows the process-wide default.
    """
    if strict is None:
        strict = _strict_tags
    tags: list[OverrideTag] = []
    depth = 0
    start = 0
    for i in range(1, len(text)):
        c = text[i]
        if depth > 0:
            if c == ")":
                depth -= 1
            elif strict and c == "(":
                depth += 1  # Aegisub does not do this: for it, the first ')' closes the tag
        elif c == "\\":
            tags.append(OverrideTag(text[start:i]))
            start = i
        elif c == "(":
            depth += 1
    if text:
        tags.append(OverrideTag(text[start:]))
    return tags


class BlockType(Enum):
    PLAIN = auto()
    COMMENT = auto()
    OVERRIDE = auto()
    DRAWING = auto()


@dataclass(slots=True)
class Block:
    type: BlockType
    text: str  # GetText(): comment blocks include their braces
    tags: list[OverrideTag] = field(default_factory=list[OverrideTag])

    def get_text(self) -> str:
        """AssDialogueBlock::GetText; an override block is rebuilt from its tags, as in Aegisub.

        So a block whose tags were changed (see process_parameters) comes back out changed. The
        rebuild is only as faithful as the splitting was: with the default, Aegisub-compatible
        tag parsing a nested `\\t(...)` does not survive the round trip byte for byte (see
        parse_override_tags). That is Aegisub's behaviour too -- it is why it keeps `Text` as it
        is unless a line was really edited -- and `set_strict_tag_parsing(True)` avoids it.
        """
        if self.type is BlockType.OVERRIDE:
            self.text = "{" + "".join(str(tag) for tag in self.tags) + "}"
        return self.text

    def process_parameters(self, callback: Callable[[str, OverrideParam], None]) -> None:
        """AssDialogueBlockOverride::ProcessParameters: callback(tag name, parameter).

        Omitted parameters are skipped and a parameter that holds a block of its own (`\\t`'s
        last one) is walked into, both as in Aegisub. The callback changes a parameter with its
        set_* methods; AssDialogue.update_text then writes the blocks back into the line.
        """
        for tag in self.tags:
            for par in tag.params:
                if par.omitted:
                    continue
                callback(tag.name, par)
                if par.type is VarType.BLOCK:
                    par.get_block().process_parameters(callback)


def parse_dialogue_blocks(text: str, strict: bool | None = None) -> list[Block]:
    """AssDialogue::ParseTags. `strict` is handed to parse_override_tags for each `{...}` block."""
    blocks: list[Block] = []
    if not text:
        blocks.append(Block(BlockType.PLAIN, ""))
        return blocks
    drawing_level = 0
    cur, n = 0, len(text)
    while cur < n:
        if text[cur] == "{":
            end = text.find("}", cur)
            if end >= 0:
                work = text[cur + 1 : end]
                cur = end + 1
                if work and "\\" not in work:
                    blocks.append(Block(BlockType.COMMENT, "{" + work + "}"))
                else:
                    tags = parse_override_tags(work, strict)
                    for tag in tags:
                        if tag.name == "\\p":
                            drawing_level = tag.params[0].get_int(0)
                    blocks.append(Block(BlockType.OVERRIDE, work, tags))
                continue
        # plain text / drawing block (also an unclosed '{')
        end = text.find("{", cur + 1)
        if end < 0:
            work = text[cur:]
            cur = n
        else:
            work = text[cur:end]
            cur = end
        blocks.append(Block(BlockType.PLAIN if drawing_level == 0 else BlockType.DRAWING, work))
    return blocks
