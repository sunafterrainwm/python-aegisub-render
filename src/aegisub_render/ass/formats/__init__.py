"""Subtitle formats other than .ass: SubRip, WebVTT, MPEG-4 Timed Text, plain text, TranStation,
Matroska -- and .ass itself, so that one dispatcher covers everything.

Each format is a `SubtitleFormat` subclass (see `base`), so the usual way in is the class:

    from aegisub_render.ass.formats import SRTSubtitleFormat, TagMode

    ass = SRTSubtitleFormat().read(Path("in.srt").read_bytes())
    data = SRTSubtitleFormat(tags=TagMode.HTML).write(ass)

and the usual way in when the format follows from a file name is the dispatcher:

    ass = read_subtitles(Path("in.srt"))
    write_subtitles(ass, Path("out.vtt"))

`register_format` adds a format of your own, or puts a subclass of one of these in front of it.

Which formats exist, and in which direction:

======================= ======= ======= ================================================
Extension               read    write   notes
======================= ======= ======= ================================================
``.ass`` / ``.ssa``     yes     .ass    ass.parser / ass.writer; .ssa is read, not written
``.srt``                yes     yes     ported from Aegisub
``.vtt``                yes     yes     not an Aegisub format; see `vtt`
``.ttxt``               no      yes     ported from Aegisub (its reader is not)
``.txt``                no      yes     ported from Aegisub (its reader needs a dialog)
``.transtation.txt``    no      yes     ported from Aegisub; needs a frame rate
``.mkv`` / .mka / .mks  yes     no      PyAV in place of Aegisub's bundled demuxer
======================= ======= ======= ================================================

No writer here writes a style table: a .srt, .vtt, .ttxt or .txt from this package carries the
text and the timing, and by default (see `base.TagMode`) the `{...}` override tags along with it,
which the players built on libass understand.
"""

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import cast

from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.ass_format import AssSubtitleFormat
from aegisub_render.ass.formats.base import (
    SubtitleFormat,
    TagMode,
    TextWriter,
    UnknownSubtitleFormatError,
    UnsupportedOperationError,
    decode_text,
    set_script_info,
    split_lines,
)
from aegisub_render.ass.formats.mkv import MatroskaError, MKVSubtitleFormat
from aegisub_render.ass.formats.srt import SRTParseError, SRTSubtitleFormat, SrtTagParser, ToggleTag
from aegisub_render.ass.formats.transtation import TranStationSubtitleFormat
from aegisub_render.ass.formats.ttxt import TTXTSubtitleFormat
from aegisub_render.ass.formats.txt import TXTSubtitleFormat
from aegisub_render.ass.formats.vtt import VTTParseError, VTTSubtitleFormat
from aegisub_render.ass.formats.vtt_style import CueRules, CueStyle, TagStack

# An option handed to a format's constructor: they take a TagMode, a parser class, a Framerate,
# a resolution, a track number or a flag, and make_format sorts out which goes where.
type FormatOption = object

# SubtitleFormat::LoadFormats, in its order and with the formats this project has. A later
# registration never shadows an earlier one, so the order decides who reads a .txt.
_FORMATS: list[type[SubtitleFormat]] = []


def register_format(fmt: type[SubtitleFormat], first: bool = False) -> None:
    """Add a format to the registry; `first` puts it ahead of the built-in ones.

    AssExportFilterChain::Register's counterpart for formats: Aegisub registers its own in
    LoadFormats and GetReader/GetWriter take the first that accepts the file name.
    """
    if fmt in _FORMATS:
        _FORMATS.remove(fmt)
    if first:
        _FORMATS.insert(0, fmt)
    else:
        _FORMATS.append(fmt)


def unregister_format(fmt: type[SubtitleFormat]) -> None:
    """Remove a format from the registry (a no-op if it is not in it)."""
    if fmt in _FORMATS:
        _FORMATS.remove(fmt)


def formats() -> tuple[type[SubtitleFormat], ...]:
    """The registered formats, in the order get_reader / get_writer try them."""
    return tuple(_FORMATS)


for _fmt in (
    AssSubtitleFormat,
    MKVSubtitleFormat,
    SRTSubtitleFormat,
    TTXTSubtitleFormat,
    TXTSubtitleFormat,
    TranStationSubtitleFormat,
    VTTSubtitleFormat,
):
    register_format(_fmt)
del _fmt


def get_reader(filename: Path | str) -> type[SubtitleFormat]:
    """SubtitleFormat::GetReader: the first registered format that reads this file name."""
    for fmt in _FORMATS:
        if fmt.can_read_file(filename):
            return fmt
    raise UnknownSubtitleFormatError(f"no subtitle format reads {Path(filename).name}")


def get_writer(filename: Path | str) -> type[SubtitleFormat]:
    """SubtitleFormat::GetWriter: the first registered format that writes this file name.

    `.transtation.txt` is matched before plain `.txt` because TXTSubtitleFormat refuses it, the
    way Aegisub's CanWriteFile override does.
    """
    for fmt in _FORMATS:
        if fmt.can_write_file(filename):
            return fmt
    raise UnknownSubtitleFormatError(f"no subtitle format writes {Path(filename).name}")


def make_format(fmt: type[SubtitleFormat], **options: FormatOption) -> SubtitleFormat:
    """Build a format, passing on only the options its constructor takes.

    The constructors share `tags` and `parser_cls` and add their own (`fps` for TranStation,
    `play_res` for WebVTT, `track` for Matroska, ...), so a dispatcher that does not know which
    format it is about to build would otherwise have to guess. Instantiate the class yourself to
    be told about an option that does not belong.
    """
    accepted = set(inspect.signature(fmt.__init__).parameters) - {"self"}
    build = cast("Callable[..., SubtitleFormat]", fmt)
    return build(**{k: v for k, v in options.items() if k in accepted})


def read_subtitles(path: Path | str, **options: FormatOption) -> AssFile:
    """Read a subtitle file, picking the format from its name (see make_format for `options`)."""
    path = Path(path)
    return make_format(get_reader(path), **options).read_path(path)


def serialize_subtitles(file: AssFile, name: Path | str, export: bool = False, **options: FormatOption) -> bytes:
    """`file` in the format `name`'s extension names (see make_format for `options`).

    `export` picks ExportFile over WriteFile, which only .ass distinguishes.
    """
    fmt = make_format(get_writer(name), **options)
    return fmt.export(file) if export else fmt.write(file)


def write_subtitles(file: AssFile, path: Path | str, export: bool = False, **options: FormatOption) -> None:
    """serialize_subtitles, written to `path` atomically."""
    from aegisub_render.ass.writer import write_ass_file

    path = Path(path)
    write_ass_file(serialize_subtitles(file, path, export, **options), path)


__all__ = [
    "AssFile",
    "AssSubtitleFormat",
    "CueRules",
    "CueStyle",
    "FormatOption",
    "MKVSubtitleFormat",
    "MatroskaError",
    "SRTParseError",
    "SRTSubtitleFormat",
    "SrtTagParser",
    "SubtitleFormat",
    "TTXTSubtitleFormat",
    "TXTSubtitleFormat",
    "TagMode",
    "TagStack",
    "TextWriter",
    "ToggleTag",
    "TranStationSubtitleFormat",
    "UnknownSubtitleFormatError",
    "UnsupportedOperationError",
    "VTTParseError",
    "VTTSubtitleFormat",
    "decode_text",
    "formats",
    "get_reader",
    "get_writer",
    "make_format",
    "read_subtitles",
    "register_format",
    "serialize_subtitles",
    "set_script_info",
    "split_lines",
    "unregister_format",
    "write_subtitles",
]
