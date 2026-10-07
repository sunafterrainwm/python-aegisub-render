"""Reading .ass files: TextFileReader + AssParser (src/text_file_reader.cpp, src/ass_parser.cpp)."""

import re
from collections.abc import Callable
from pathlib import Path

from aegisub_render.ass.codec import inline_string_decode, uudecode
from aegisub_render.ass.entries import (
    AssAttachment,
    AssDialogue,
    AssInfo,
    AssStyle,
    ExtradataEntry,
    Group,
    ProjectProperties,
    SubtitleFormatParseError,
)
from aegisub_render.ass.file import AssFile
from aegisub_render.cutil import trim, trim_left, try_parse_double, try_parse_int
from aegisub_render.textenc import from_bytes


class UnsupportedEncodingError(Exception):
    pass


_STRING_PROPS: dict[str, str] = {
    "Automation Scripts": "automation_scripts",
    "Export Filters": "export_filters",
    "Export Encoding": "export_encoding",
    "Last Style Storage": "style_storage",
    "Audio URI": "audio_file",
    "Audio File": "audio_file",
    "Video File": "video_file",
    "Timecodes File": "timecodes_file",
    "Keyframes File": "keyframes_file",
}
_INT_PROPS: dict[str, str] = {
    "Scroll Position": "scroll_position",
    "Active Line": "active_row",
    "Video Position": "video_position",
    "Video AR Mode": "ar_mode",
    "Aegisub Scroll Position": "scroll_position",
    "Aegisub Active Line": "active_row",
    "Aegisub Video Position": "video_position",
}
_DOUBLE_PROPS: dict[str, str] = {
    "Video Zoom Percent": "video_zoom",
    "Video AR Value": "ar_value",
    "Aegisub Video Zoom Percent": "video_zoom",
}

_EXTRADATA_RE = re.compile(r"Data:[ \t\n\v\f\r]*(\d+),([^,]+),(.)(.*)", re.DOTALL)


def _process_property(props: ProjectProperties, key: str, value: str) -> bool:
    if key in _STRING_PROPS:
        setattr(props, _STRING_PROPS[key], value)
        return True
    if key in _INT_PROPS:
        v = try_parse_int(value)
        if v is not None:
            setattr(props, _INT_PROPS[key], v)
        return True
    if key in _DOUBLE_PROPS:
        d = try_parse_double(value)
        if d is not None:
            setattr(props, _DOUBLE_PROPS[key], d)
        return True
    if key.startswith("Automation Settings "):
        props.automation_settings[key[len("Automation Settings") :]] = value
        return True
    return False


class AssParser:
    def __init__(self, target: AssFile, version: int) -> None:
        self.target = target
        self.version = version
        self.state: Callable[[str], None] = self.parse_script_info_line
        self.attachment_active = False
        self.attachment_group = Group.FONT
        self.attachment_data = ""

    def finish_attachment(self) -> None:
        self.target.attachments.append(AssAttachment(self.attachment_data, self.attachment_group))
        self.attachment_active = False

    def parse_attachment_line(self, data: str) -> None:
        is_filename = data.startswith(("fontname: ", "filename: "))
        valid_data = 0 < len(data) <= 80 and all(33 <= ord(c) < 97 for c in data)
        if not valid_data or is_filename:
            self.finish_attachment()
            self.add_line(data)
        else:
            self.attachment_data += data + "\r\n"
            if len(data) < 80:
                self.finish_attachment()

    def parse_script_info_line(self, data: str) -> None:
        if data.startswith(";"):
            return
        if data.startswith("ScriptType:"):
            version_str = trim(data[11:]).lower()
            if version_str == "v4.00":
                self.version = 0
            elif version_str == "v4.00+":
                self.version = 1
            else:
                raise SubtitleFormatParseError("Unknown SSA file format version")
        if data.startswith("Collisions:"):
            return
        pos = data.find(":")
        if pos < 0:
            return
        key = data[:pos]
        value = trim_left(data[pos + 1 :])
        if not _process_property(self.target.properties, key, value):
            self.target.info.append(AssInfo(key, value))

    def parse_metadata_line(self, raw: str) -> None:
        data = raw.replace("\0", "�")
        pos = data.find(":")
        if pos < 0:
            return
        _process_property(self.target.properties, data[:pos], trim_left(data[pos + 1 :]))

    def parse_event_line(self, data: str) -> None:
        if data.startswith(("Dialogue:", "Comment:")):
            self.target.events.append(AssDialogue.parse(data))

    def parse_style_line(self, data: str) -> None:
        if data.startswith("Style:"):
            self.target.styles.append(AssStyle.parse(data, self.version))

    def parse_font_line(self, data: str) -> None:
        if data.startswith("fontname: "):
            self.attachment_active = True
            self.attachment_group = Group.FONT
            self.attachment_data = data + "\r\n"

    def parse_graphics_line(self, data: str) -> None:
        if data.startswith("filename: "):
            self.attachment_active = True
            self.attachment_group = Group.GRAPHIC
            self.attachment_data = data + "\r\n"

    def parse_extradata_line(self, raw: str) -> None:
        data = raw.replace("\0", "�")
        m = _EXTRADATA_RE.fullmatch(data)
        if not m:
            return
        ident = int(m.group(1))
        if ident > 0xFFFFFFFF:
            raise SubtitleFormatParseError(f"bad extradata id: {m.group(1)}")
        key = inline_string_decode(m.group(2))
        valuetype = m.group(3)
        value = m.group(4)
        if valuetype == "e":
            value = inline_string_decode(value)
        elif valuetype == "u":
            value = from_bytes(uudecode(value))
        else:
            value = ""
        self.target.next_extradata_id = max((ident + 1) & 0xFFFFFFFF, self.target.next_extradata_id)
        self.target.extradata.append(ExtradataEntry(ident, key, value))

    def unknown_line(self, data: str) -> None:
        pass

    def add_line(self, data: str) -> None:
        if self.attachment_active:
            self.parse_attachment_line(data)
            return
        if not data:
            return
        if data[0] == "[" and data[-1] == "]":
            low = data.lower()
            if low == "[v4 styles]":
                self.version = 0
                self.state = self.parse_style_line
            elif low == "[v4+ styles]":
                self.version = 1
                self.state = self.parse_style_line
            elif low == "[events]":
                self.state = self.parse_event_line
            elif low == "[script info]":
                self.state = self.parse_script_info_line
            elif low == "[aegisub project garbage]":
                self.state = self.parse_metadata_line
            elif low == "[aegisub extradata]":
                self.state = self.parse_extradata_line
            elif low == "[graphics]":
                self.state = self.parse_graphics_line
            elif low == "[fonts]":
                self.state = self.parse_font_line
            else:
                self.state = self.unknown_line
            return
        self.state(data)


def decode_subtitle_bytes(raw: bytes) -> list[str]:
    """Decode and split into lines the way TextFileReader + agi::line_iterator do.

    Only UTF-8 (with or without BOM) and UTF-16 with a BOM are accepted; Aegisub would run
    charset detection here, which this tool deliberately does not reproduce.
    """
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        codec = "utf-16-le" if raw.startswith(b"\xff\xfe") else "utf-16-be"
        text = raw[2:].decode(codec, "surrogatepass")
        text = from_bytes(text.encode("utf-8", "surrogatepass"))
        lines = text.replace("\r", "").split("\n")
    else:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UnsupportedEncodingError(
                f"input is not valid UTF-8 (byte offset {e.start}); only UTF-8 and UTF-16 with BOM are supported"
            ) from None
        text = from_bytes(raw)
        lines = [line.removesuffix("\r") for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        line = trim(line)
        if line.startswith("﻿"):
            line = line[1:]
        out.append(line)
    return out


def parse_ass_lines(lines: list[str], version: int = 1) -> AssFile:
    target = AssFile()
    parser = AssParser(target, version)
    for line in lines:
        parser.add_line(line)
    return target


def read_ass_file(path: Path) -> AssFile:
    version = 0 if path.suffix.lower() == ".ssa" else 1
    return parse_ass_lines(decode_subtitle_bytes(path.read_bytes()), version)
