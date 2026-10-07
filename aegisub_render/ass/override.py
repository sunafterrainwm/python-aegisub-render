"""Dialogue block / override tag parsing (src/ass_dialogue.cpp ParseTags, src/ass_override.cpp)."""

from dataclasses import dataclass, field
from enum import Enum, auto

from aegisub_render.cutil import atoi, mid, strtol_hex_prefix, trim


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


_I, _F, _T, _B, _BL = VarType.INT, VarType.FLOAT, VarType.TEXT, VarType.BOOL, VarType.BLOCK
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

    def get_str(self) -> str:
        return self.value

    def get_int(self, default: int | None = None) -> int:
        if self.omitted:
            if default is None:
                raise ValueError("AssOverrideParameter::Get() called on omitted parameter")
            return default
        if self.classification is ParamClass.ALPHA:
            pos = next((i for i, c in enumerate(self.value) if c in "0123456789abcdefABCDEF"), len(self.value))
            return mid(0, strtol_hex_prefix(self.value[pos:]), 255)
        return atoi(self.value)


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

    def __str__(self) -> str:
        result = self.name
        parens = len(self.params) > 1
        if parens:
            result += "("
        result += ",".join(p.get_str() for p in self.params if not p.omitted)
        if parens:
            result += ")"
        return result


def parse_override_tags(text: str) -> list[OverrideTag]:
    """AssDialogueBlockOverride::ParseTags."""
    tags: list[OverrideTag] = []
    depth = 0
    start = 0
    for i in range(1, len(text)):
        c = text[i]
        if depth > 0:
            if c == ")":
                depth -= 1
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


def parse_dialogue_blocks(text: str) -> list[Block]:
    """AssDialogue::ParseTags."""
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
                    tags = parse_override_tags(work)
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
