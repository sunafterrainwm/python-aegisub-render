"""Matroska (.mkv/.mka/.mks): reading the subtitle track, after src/subtitle_format_mkv.h.

Aegisub reads Matroska with its own bundled demuxer (src/MatroskaParser.c, ~3300 lines) wrapped by
mkv_wrap.cpp. Porting that is not worth it: this project already depends on PyAV for video, and
ffmpeg's Matroska demuxer hands over exactly what MatroskaWrapper::GetSubtitles works with -- the
track's CodecPrivate as the stream's extradata, and each block as the raw
`ReadOrder,Layer,Style,...` line. So what is ported is the function around it, which is the part
that decides what the AssFile ends up looking like:

* ASS/SSA tracks: the extradata is fed to AssParser line by line (so the track's own styles,
  script info and resolution come along), then `[Events]`, then one `Dialogue:` line per block
  built from the block's timestamps and everything after the block's second comma;
* S_TEXT/UTF8 tracks: a default ASS file, and each block's text through SrtTagParser, with its
  line breaks turned into `\\N` -- the same path Aegisub takes;
* the blocks are sorted by the ReadOrder field they start with, which is what it is for;
* the 64 MiB total cap on subtitle data is Aegisub's.

Differences: PyAV is needed (`pip install aegisub-render[video]`), block timestamps come from
ffmpeg rather than from a hand-rolled parser, and where Aegisub asks which track to read this
picks the first subtitle track it recognises unless `track` names one. Writing Matroska is not
something Aegisub does either.
"""

import io
import re
from pathlib import Path
from typing import ClassVar, override

from aegisub_render.ass.entries import SubtitleFormatParseError
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats.base import SubtitleFormat
from aegisub_render.ass.formats.srt import SrtTagParser
from aegisub_render.ass.parser import AssParser
from aegisub_render.ass.time import ass_formatted, clamp_time
from aegisub_render.textenc import from_bytes

MAX_TOTAL_SUBTITLE_BYTES = 64 * 1024 * 1024  # mkv_wrap.cpp's max_total_subtitle_bytes

_ASS_CODECS = frozenset({"ass", "ssa"})  # S_TEXT/ASS and S_TEXT/SSA
_SRT_CODECS = frozenset({"srt", "subrip", "text"})  # S_TEXT/UTF8
_SUBTITLE_CODECS = _ASS_CODECS | _SRT_CODECS
_HEADER_SPLIT_RE = re.compile(r"[\r\n]+")  # boost::char_separator<char>("\r\n"), empties dropped


class MatroskaError(SubtitleFormatParseError):
    pass


class MKVSubtitleFormat(SubtitleFormat):
    """MKVSubtitleFormat. `track` is the stream index to read (default: the first recognised)."""

    name: ClassVar[str] = "Matroska"
    read_wildcards: ClassVar[tuple[str, ...]] = ("mkv", "mka", "mks")

    def __init__(
        self,
        parser_cls: type[AssParser] | None = None,
        track: int | None = None,
    ) -> None:
        super().__init__(None, parser_cls)
        self.track = track

    @override
    def read(self, raw: bytes) -> AssFile:
        return self._read_container(io.BytesIO(raw))

    @override
    def read_path(self, path: Path) -> AssFile:
        return self._read_container(str(path))

    def tracks(self, path: Path) -> list[tuple[int, str, str, str]]:
        """The subtitle tracks of a file as (index, codec, language, title), for picking one."""
        import av  # imported lazily: PyAV is the optional "video" extra

        with av.open(str(path)) as container:
            return [
                (
                    stream.index,
                    (stream.codec_context.name or "").lower(),
                    str(stream.metadata.get("language", "")),
                    str(stream.metadata.get("title", "")),
                )
                for stream in container.streams
                if stream.type == "subtitle"
            ]

    def _read_container(self, source: str | io.BytesIO) -> AssFile:
        try:
            import av  # imported lazily: only needed to read a Matroska file
        except ImportError as e:
            raise MatroskaError(
                "reading Matroska files needs PyAV, which is the 'video' extra of this package "
                "(uv add 'aegisub-render[video]', or uv sync --extra video in its repository)"
            ) from e

        target, parser = self.new_file()
        tag_parser = SrtTagParser()

        with av.open(source, "r") as container:
            streams = [
                s
                for s in container.streams
                if s.type == "subtitle" and (s.codec_context.name or "").lower() in _SUBTITLE_CODECS
            ]
            if not streams:
                raise MatroskaError("File has no recognised subtitle tracks.")
            if self.track is None:
                stream = streams[0]
            else:
                found = [s for s in streams if s.index == self.track]
                if not found:
                    available = ", ".join(str(s.index) for s in streams)
                    raise MatroskaError(f"No subtitle track with index {self.track} (the file has {available})")
                stream = found[0]

            srt = (stream.codec_context.name or "").lower() in _SRT_CODECS

            if not srt:
                # Read private data if it's ASS/SSA: the track's header, line by line. The
                # version AssParser starts with does not matter, ScriptType / [V4 Styles] in
                # the header sets it (Aegisub passes !ssa for the same reason).
                private = from_bytes(bytes(stream.codec_context.extradata or b""))
                for line in _HEADER_SPLIT_RE.split(private):
                    if line:
                        parser.add_line(line)
            else:
                self.load_default(target, parser)

            parser.add_line("[Events]")
            total = 0
            sub_list: list[tuple[int, str]] = []
            tb = stream.time_base  # 1/1000 for Matroska; guard the denominator all the same
            tb_num, tb_den = (tb.numerator, tb.denominator) if tb.denominator else (1, 1000)

            for packet in container.demux(stream):
                data = bytes(packet)
                if not data:
                    continue  # the flush packet at the end of the stream
                total += len(data)
                if total > MAX_TOTAL_SUBTITLE_BYTES:
                    raise MatroskaError(
                        f"Matroska subtitle data exceeds the {MAX_TOTAL_SUBTITLE_BYTES // 1024 // 1024} MiB limit"
                    )
                start = _to_ms(packet.pts, tb_num, tb_den)
                end = _to_ms((packet.pts or 0) + (packet.duration or 0), tb_num, tb_den)
                text = from_bytes(data)

                if not srt:
                    first = text.find(",")
                    if first < 0:
                        continue
                    second = text.find(",", first + 1)
                    if second < 0:
                        continue
                    order = _read_int(text[:first])
                    layer = _read_int(text[first + 1 : second])
                    sub_list.append(
                        (
                            order,
                            f"Dialogue: {layer},{ass_formatted(start)},{ass_formatted(end)},{text[second + 1 :]}",
                        )
                    )
                else:
                    line = (
                        f"Dialogue: 0,{ass_formatted(start)},{ass_formatted(end)},Default,,0,0,0,,"
                        f"{tag_parser.to_ass(text)}"
                    )
                    line = line.replace("\r\n", "\\N").replace("\r", "\\N").replace("\n", "\\N")
                    sub_list.append((len(sub_list), line))

        sub_list.sort()
        for _, line in sub_list:
            parser.add_line(line)
        return target


def _to_ms(pts: int | None, tb_num: int, tb_den: int) -> int:
    """A packet timestamp as agi::Time does it: truncated to whole milliseconds."""
    if pts is None:
        return 0
    return clamp_time((int(pts) * tb_num * 1000) // tb_den)


def _read_int(text: str) -> int:
    """boost::lexical_cast<int> on a ReadOrder / Layer field; a bad one counts as zero."""
    try:
        return int(text.strip())
    except ValueError:
        return 0
