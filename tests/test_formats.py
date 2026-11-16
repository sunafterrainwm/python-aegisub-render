"""The subtitle formats other than .ass (aegisub_render.ass.formats)."""

import shutil
import subprocess
from pathlib import Path
from typing import ClassVar, override

import pytest

from aegisub_render.ass.entries import AssDialogue, AssInfo, AssStyle
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.formats import (
    AssSubtitleFormat,
    MKVSubtitleFormat,
    SRTParseError,
    SRTSubtitleFormat,
    SrtTagParser,
    SubtitleFormat,
    TagMode,
    TranStationSubtitleFormat,
    TTXTSubtitleFormat,
    TXTSubtitleFormat,
    UnknownSubtitleFormatError,
    UnsupportedOperationError,
    VTTParseError,
    VTTSubtitleFormat,
    formats,
    get_reader,
    get_writer,
    read_subtitles,
    register_format,
    serialize_subtitles,
    unregister_format,
    write_subtitles,
)
from aegisub_render.ass.parser import AssParser
from aegisub_render.cli import main
from aegisub_render.video.vfr import Framerate

CASES = Path(__file__).parent / "cases"


def file_with(*events: tuple[int, int, str], styles: list[AssStyle] | None = None) -> AssFile:
    ass = AssFile()
    ass.info.append(AssInfo("ScriptType", "v4.00+"))
    ass.styles.extend(styles or [AssStyle()])
    for start, end, text in events:
        ass.events.append(AssDialogue(start=start, end=end, text=text))
    return ass


def lines(data: bytes) -> list[str]:
    return data.decode("utf-8-sig").split("\r\n")


# -- the helpers SubtitleFormat offers (subtitle_format.cpp) ------------------------------------


def test_convert_newlines_merges_and_handles_hard_spaces():
    ass = file_with((0, 1000, "a\\Nb\\nc\\N\\N\\Nd\\he"))
    SubtitleFormat.convert_newlines(ass, "|")
    assert ass.events[0].text == "a|b|c|d e"


def test_convert_newlines_without_merging():
    ass = file_with((0, 1000, "a\\N\\Nb"))
    SubtitleFormat.convert_newlines(ass, "|", merge_line_breaks=False)
    assert ass.events[0].text == "a||b"


def test_strip_comments_drops_comments_and_empty_lines():
    ass = file_with((0, 1000, "kept"), (0, 1000, ""), (0, 1000, "commented"))
    ass.events[2].comment = True
    SubtitleFormat.strip_comments(ass)
    assert [e.text for e in ass.events] == ["kept"]


def test_merge_identical_merges_touching_equal_lines():
    ass = file_with((0, 1000, "same"), (1000, 2000, "same"), (2000, 3000, "other"))
    SubtitleFormat.merge_identical(ass)
    assert [(e.start, e.end, e.text) for e in ass.events] == [(0, 2000, "same"), (2000, 3000, "other")]


def test_recombine_overlaps_splits_into_three_parts():
    # B starts inside A and ends after it: A alone, both, B alone
    ass = file_with((0, 2000, "A"), (1000, 3000, "B"))
    SubtitleFormat.recombine_overlaps(ass)
    assert [(e.start, e.end, e.text) for e in ass.events] == [
        (0, 1000, "A"),
        (1000, 2000, "B\\NA"),
        (2000, 3000, "B"),
    ]


def test_recombine_overlaps_contained_line():
    # B is inside A: A, both, A again
    ass = file_with((0, 3000, "A"), (1000, 2000, "B"))
    SubtitleFormat.recombine_overlaps(ass)
    assert [(e.start, e.end, e.text) for e in ass.events] == [
        (0, 1000, "A"),
        (1000, 2000, "B\\NA"),
        (2000, 3000, "A"),
    ]


def test_recombine_overlaps_leaves_separate_lines_alone():
    ass = file_with((0, 1000, "A"), (1000, 2000, "B"))
    SubtitleFormat.recombine_overlaps(ass)
    assert [(e.start, e.end, e.text) for e in ass.events] == [(0, 1000, "A"), (1000, 2000, "B")]


def test_can_save_rejects_overrides_and_other_styles():
    assert SRTSubtitleFormat().can_save(file_with((0, 1000, "plain")))
    assert not AssSubtitleFormat().can_save(file_with((0, 1000, "{\\pos(1,2)}moved")))
    assert SRTSubtitleFormat().can_save(file_with((0, 1000, "{\\b1}bold")))  # SRT knows \b
    assert not SRTSubtitleFormat().can_save(file_with((0, 1000, "{\\pos(1,2)}moved")))


# -- SubRip ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("srt", "ass"),
    [
        ("<b>bold</b>", "{\\b1}bold{\\b}"),
        ("<B>upper case</b>", "{\\b1}upper case{\\b}"),
        ("<b>a<b>b</b>c</b>", "{\\b1}abc{\\b}"),  # nested: ToggleTag opens once
        ("<b>bo<i>th</b> i</i>", "{\\b1}bo{\\i1}th{\\b} i{\\i}"),
        ("<u>u</u><s>s</s>", "{\\u1}u{\\u\\s1}s{\\s}"),  # the }{ join runs them together
        ("</b>unopened", "unopened"),  # closing an unopened tag writes nothing
        ("<xyz a>kept", "<xyz a>kept"),  # an unknown tag is replicated
        ("<unknown x>kept", "{\\u1}kept"),  # but <u of "<unknown" matches first, as upstream
        ('<font color="#ff8800">c</font>', "{\\c&H0088FF&}c{\\c}"),
        ('<font face="Arial" size="40">x</font>', "{\\fnArial\\fs40}x{\\fn\\fs}"),
        # the inner font only writes what it changes, and restores it on the way out
        (
            # agi::Color has no CSS colour keywords, so both of these parse as black and the
            # inner <font> has nothing of its own to write
            '<font color="red"><font color="lime">x</font>y</font>',
            "{\\c&H000000&}xy{\\c}",
        ),
        ("</font>", ""),  # no font on the stack
    ],
)
def test_srt_tag_parser(srt: str, ass: str):
    assert SrtTagParser().to_ass(srt) == ass


def test_srt_read_blank_line_inside_a_cue():
    data = (
        b"1\r\n00:00:01,000 --> 00:00:02,500\r\nfirst\r\n\r\nstill the same cue\r\n"
        b"\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nsecond\r\n"
    )
    ass = SRTSubtitleFormat().read(data)
    assert [(e.start, e.end, e.text) for e in ass.events] == [
        (1000, 2500, "first\\N\\Nstill the same cue"),
        (3000, 4000, "second"),
    ]


def test_srt_read_loads_the_default_file():
    ass = SRTSubtitleFormat().read(b"1\r\n00:00:01,000 --> 00:00:02,000\r\nx\r\n")
    assert ass.get_script_info("ScriptType") == "v4.00+"
    assert [s.name for s in ass.styles] == ["Default"]


def test_srt_read_without_an_index_line():
    ass = SRTSubtitleFormat().read(b"00:00:01,000 --> 00:00:02,000\r\nx\r\n")
    assert [e.text for e in ass.events] == ["x"]


def test_srt_read_rejects_junk_and_truncation():
    with pytest.raises(SRTParseError, match="Expected subtitle index"):
        SRTSubtitleFormat().read(b"not a subtitle file\r\n")
    with pytest.raises(SRTParseError, match="Expected timestamp pair"):
        SRTSubtitleFormat().read(b"1\r\nnonsense\r\n")
    with pytest.raises(SRTParseError, match="Incomplete file"):
        SRTSubtitleFormat().read(b"1\r\n00:00:01,000 --> 00:00:02,000\r\n")


def test_srt_write_is_aegisubs_shape():
    ass = file_with((1230, 4567, "line"), (0, 1000, "earlier"))
    assert lines(SRTSubtitleFormat().write(ass)) == [
        "1",
        "00:00:00,000 --> 00:00:01,000",
        "earlier",
        "",
        "2",
        "00:00:01,230 --> 00:00:04,567",
        "line",
        "",
        "",
    ]


def test_srt_write_keeps_override_tags_by_default_and_converts_them_on_request():
    ass = file_with((0, 1000, "{\\b1}bold{\\b0} {\\pos(1,2)}moved"))
    assert "{\\b1}bold{\\b0} {\\pos(1,2)}moved" in lines(SRTSubtitleFormat().write(ass))
    assert "<b>bold</b> moved" in lines(SRTSubtitleFormat(tags=TagMode.HTML).write(ass))
    assert "bold moved" in lines(SRTSubtitleFormat(tags="strip").write(ass))


def test_srt_html_mode_closes_tags_left_open():
    ass = file_with((0, 1000, "{\\i1}unclosed"))
    assert "<i>unclosed</i>" in lines(SRTSubtitleFormat(tags=TagMode.HTML).write(ass))


def test_srt_write_newlines_are_not_merged():
    ass = file_with((0, 1000, "a\\N\\Nb"))
    assert lines(SRTSubtitleFormat().write(ass))[2:4] == ["a", ""]


def test_srt_round_trip_keeps_milliseconds():
    ass = file_with((1234, 5678, "text"))
    back = SRTSubtitleFormat().read(SRTSubtitleFormat().write(ass))
    assert (back.events[0].start, back.events[0].end) == (1234, 5678)


# -- WebVTT ------------------------------------------------------------------------------------

SAMPLE_VTT = """WEBVTT - a styled sample

NOTE
dropped

STYLE
::cue {
  color: #ffff00;
  font-family: "Noto Sans", sans-serif;
  font-size: 54px;
}
::cue(.loud) { font-weight: bold; color: red }
::cue(v[voice="Narrator"]) { font-style: italic; color: rgba(0, 255, 255, 0.5) }

cue-1
00:00:01.000 --> 00:00:03.500 align:left line:10%
<v Narrator>Up at <c.loud>the top</c>, left.

00:00:04.000 --> 00:00:06.000 align:center position:50% size:60%
A &lt;boxed&gt; line with a&nbsp;hard space
and a second row.

00:00:07.000 --> 00:00:09.000
<b>Bold</b> and <i>italic <u>underlined</u></i>

00:00:10.000 --> 00:00:12.000
<00:00:10.000>Ka<00:00:10.500>ra<00:00:11.000>o<00:00:11.500>ke

00:00:13.000 --> 00:00:15.000
<ruby>base<rt>annotation</rt></ruby> kept
"""


@pytest.fixture
def sample_vtt() -> AssFile:
    return VTTSubtitleFormat().read(SAMPLE_VTT.encode())


def test_vtt_rejects_a_file_without_the_signature():
    with pytest.raises(VTTParseError, match="WEBVTT"):
        VTTSubtitleFormat().read(b"1\n00:00:01,000 --> 00:00:02,000\nx\n")


def test_vtt_reads_timings_and_drops_notes_and_identifiers(sample_vtt: AssFile):
    assert [(e.start, e.end) for e in sample_vtt.events] == [
        (1000, 3500),
        (4000, 6000),
        (7000, 9000),
        (10000, 12000),
        (13000, 15000),
    ]


def test_vtt_cue_style_becomes_the_default_style(sample_vtt: AssFile):
    default = sample_vtt.styles[0]
    assert (default.font, default.fontsize) == ("Noto Sans", 54.0)
    assert default.primary.ass_style() == "&H0000FFFF"  # yellow


def test_vtt_voice_becomes_a_style_and_the_name_field(sample_vtt: AssFile):
    narrator = sample_vtt.get_style("Narrator")
    assert narrator is not None
    assert narrator.italic
    assert narrator.primary.ass_style() == "&H80FFFF00"  # cyan at half opacity
    assert (sample_vtt.events[0].actor, sample_vtt.events[0].style) == ("Narrator", "Narrator")


def test_vtt_class_becomes_inline_tags(sample_vtt: AssFile):
    assert sample_vtt.events[0].text.endswith("Up at {\\c&H0000FF&\\b1}the top{\\c\\b}, left.")


def test_vtt_alignment_and_margins(sample_vtt: AssFile):
    assert sample_vtt.events[0].text.startswith("{\\an7}")  # align:left line:10%
    assert sample_vtt.events[0].margin == [0, 0, 72]  # 10% of 720
    assert sample_vtt.events[1].margin == [256, 256, 0]  # size:60% centred on 1280
    assert (sample_vtt.get_script_info("PlayResX"), sample_vtt.get_script_info("PlayResY")) == ("1280", "720")


def test_vtt_without_positioning_stays_resolution_free():
    ass = VTTSubtitleFormat().read(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nplain\n")
    assert ass.get_script_info("PlayResX") == ""
    assert ass.events[0].text == "plain"


def test_vtt_entities_newlines_and_span_tags(sample_vtt: AssFile):
    assert sample_vtt.events[1].text == "A <boxed> line with a\\hhard space\\Nand a second row."
    assert sample_vtt.events[2].text == "{\\b1}Bold{\\b} and {\\i1}italic {\\u1}underlined{\\u\\i}"


def test_vtt_timestamp_tags_become_karaoke(sample_vtt: AssFile):
    assert sample_vtt.events[3].text == "{\\k50}Ka{\\k50}ra{\\k50}o{\\k50}ke"


def test_vtt_ruby_annotation_is_dropped(sample_vtt: AssFile):
    assert sample_vtt.events[4].text == "base kept"


def test_vtt_play_res_is_configurable():
    data = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000 line:50% size:50%\nx\n"
    ass = VTTSubtitleFormat(play_res=(640, 480)).read(data)
    assert ass.events[0].margin == [160, 160, 0]
    assert ass.get_script_info("PlayResX") == "640"


def test_vtt_write_has_a_signature_and_blank_separated_cues():
    ass = file_with((0, 1500, "one"), (2000, 3000, "two"))
    assert lines(VTTSubtitleFormat().write(ass)) == [
        "WEBVTT",
        "",
        "00:00:00.000 --> 00:00:01.500",
        "one",
        "",
        "00:00:02.000 --> 00:00:03.000",
        "two",
        "",
        "",
    ]


def test_vtt_write_escapes_angle_brackets_but_keeps_tag_ampersands():
    ass = file_with((0, 1000, "{\\c&H00FF00&}a < b --> c"))
    body = lines(VTTSubtitleFormat().write(ass))[3]
    assert body == "{\\c&H00FF00&}a &lt; b --&gt; c"


def test_vtt_write_merges_blank_lines_so_a_cue_is_not_cut_short():
    ass = file_with((0, 1000, "a\\N\\Nb"))
    assert lines(VTTSubtitleFormat().write(ass))[3:5] == ["a", "b"]


def test_vtt_round_trip():
    ass = file_with((1234, 5678, "{\\b1}text{\\b}"))
    back = VTTSubtitleFormat().read(VTTSubtitleFormat().write(ass))
    assert (back.events[0].start, back.events[0].end) == (1234, 5678)
    assert back.events[0].text == "{\\b1}text{\\b}"


# -- MPEG-4 Timed Text -------------------------------------------------------------------------


def test_ttxt_structure_and_samples():
    ass = file_with((1000, 2000, "first"), (2000, 3000, "second"))
    text = TTXTSubtitleFormat().write(ass).decode("utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<TextStream version="1.1">\n')
    assert '<FontTableEntry fontName="Sans" fontID="1"/>' in text
    assert '<TextSample sampleTime="00:00:01.000" xml:space="preserve">first</TextSample>' in text
    assert '<TextSample sampleTime="00:00:02.000" xml:space="preserve">second</TextSample>' in text
    # the blank sample ConvertToTTXT appends, two seconds after the last line
    assert '<TextSample sampleTime="00:00:03.000" xml:space="preserve"></TextSample>' in text
    assert text.endswith("</TextStream>\n")


def test_ttxt_fills_a_gap_with_a_blank_sample():
    ass = file_with((0, 1000, "a"), (2000, 3000, "b"))
    text = TTXTSubtitleFormat().write(ass).decode("utf-8")
    assert '<TextSample sampleTime="00:00:01.000" xml:space="preserve"></TextSample>' in text


def test_ttxt_escapes_xml_and_keeps_tags_by_default():
    ass = file_with((0, 1000, "{\\b1}a < b & c"))
    text = TTXTSubtitleFormat().write(ass).decode("utf-8")
    assert "{\\b1}a &lt; b &amp; c" in text
    stripped = TTXTSubtitleFormat(tags=TagMode.STRIP).write(ass).decode("utf-8")
    assert "{\\b1}" not in stripped


def test_ttxt_trailing_sample_starts_where_the_last_line_ends():
    # ConvertToTTXT gives the blank line an end of its own (Timing/Default Duration), but
    # WriteLine only ever writes sampleTime, so the duration never reaches the file
    ass = file_with((0, 1000, "a"))
    for duration in (500, 2000):
        text = TTXTSubtitleFormat(default_duration=duration).write(ass).decode("utf-8")
        assert '<TextSample sampleTime="00:00:01.000" xml:space="preserve"></TextSample>' in text


# -- plain text --------------------------------------------------------------------------------


def test_txt_writes_a_header_and_strips_tags():
    ass = file_with((0, 1000, "{\\b1}bold{\\b0} text"))
    out = lines(TXTSubtitleFormat().write(ass))
    assert out[0].startswith("# Exported by Aegisub 3.5.1 with aegisub-render ")
    assert out[1] == "bold text"


def test_txt_keep_brand_leaves_aegisubs_line_alone():
    out = lines(TXTSubtitleFormat(keep_brand=True).write(file_with((0, 1000, "x"))))
    assert out[0] == "# Exported by Aegisub 3.5.1"


def test_txt_writes_actors_only_when_most_lines_have_one():
    ass = file_with((0, 1000, "a"), (1000, 2000, "b"))
    ass.events[0].actor = "Alice"
    assert lines(TXTSubtitleFormat().write(ass))[1:3] == ["a", "b"]  # one of two is not enough
    ass.events[1].actor = "Bob"
    assert lines(TXTSubtitleFormat().write(ass))[1:3] == ["Alice: a", "Bob: b"]


def test_txt_comments_and_empty_lines():
    ass = file_with((0, 1000, "kept"), (0, 1000, "noted"), (0, 1000, "{\\pos(1,2)}"))
    ass.events[1].comment = True
    assert lines(TXTSubtitleFormat().write(ass))[1:3] == ["kept", "# noted"]  # the third is empty


def test_txt_refuses_the_other_formats_that_end_in_txt():
    assert TXTSubtitleFormat.can_write_file("a.txt")
    assert not TXTSubtitleFormat.can_write_file("a.transtation.txt")
    assert not TXTSubtitleFormat.can_write_file("a.encore.txt")


# -- TranStation -------------------------------------------------------------------------------


def test_transtation_needs_a_frame_rate():
    with pytest.raises(UnsupportedOperationError, match="frame rate"):
        TranStationSubtitleFormat().write(file_with((0, 1000, "x")))


def test_transtation_header_and_trailer():
    fps = Framerate.from_ratio(25, 1)
    ass = file_with((1000, 2000, "one"), (3000, 4000, "two"))
    out = lines(TranStationSubtitleFormat(fps=fps).write(ass))
    assert out == [
        "SUB[0 N 00:00:01:00>00:00:02:00]",
        "one",
        "",
        "SUB[0 N 00:00:03:00>00:00:04:00]",
        "two",
        "SUB[",
        "",
    ]


def test_transtation_alignment_and_italics_come_from_the_style():
    fps = Framerate.from_ratio(25, 1)
    style = AssStyle(name="Default", alignment=7, italic=True)
    ass = file_with((0, 1000, "x"), styles=[style])
    assert lines(TranStationSubtitleFormat(fps=fps).write(ass))[0].startswith("SUB[9LI ")


def test_transtation_italics_tag_anywhere_makes_the_line_italic():
    fps = Framerate.from_ratio(25, 1)
    ass = file_with((0, 1000, "plain {\\i1}and italic"))
    out = lines(TranStationSubtitleFormat(fps=fps, tags=TagMode.KEEP).write(ass))
    assert out[0].startswith("SUB[0 I ")


def test_transtation_pulls_the_end_back_a_frame_when_lines_touch():
    fps = Framerate.from_ratio(25, 1)  # 40 ms per frame
    ass = file_with((0, 1000, "a"), (1000, 2000, "b"))
    out = lines(TranStationSubtitleFormat(fps=fps).write(ass))
    assert out[0] == "SUB[0 N 00:00:00:00>00:00:00:23]"  # not ...:01:00
    assert out[3] == "SUB[0 N 00:00:01:00>00:00:02:00]"  # the last line keeps its end


# -- Matroska ----------------------------------------------------------------------------------


def mkv_fixture(tmp_path: Path, codec: str) -> Path:
    """Mux tests/cases/01-basic-syl.ass into a Matroska file with ffmpeg."""
    pytest.importorskip("av", reason="reading Matroska needs PyAV (the 'video' extra)")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is needed to build the Matroska fixture")
    out = tmp_path / f"{codec}.mkv"
    subprocess.run(
        [
            *[ffmpeg, "-y", "-loglevel", "error", "-f", "ass", "-i", str(CASES / "01-basic-syl.ass")],
            *["-c:s", codec, "-f", "matroska", str(out)],
        ],
        check=True,
    )
    return out


def test_mkv_reads_an_ass_track_with_its_styles(tmp_path: Path):
    ass = MKVSubtitleFormat().read_path(mkv_fixture(tmp_path, "copy"))
    # the track's CodecPrivate carried the script info and the styles over
    assert ass.get_script_info("PlayResX") == "1920"
    assert [s.name for s in ass.styles] == ["Default"]
    dialogue = [e for e in ass.events if not e.comment]
    assert len(dialogue) == 4
    assert dialogue[0].start == 2820
    assert dialogue[0].text.startswith("{\\k10}")


def test_mkv_reads_an_srt_track_through_the_srt_tag_parser(tmp_path: Path):
    ass = MKVSubtitleFormat().read_path(mkv_fixture(tmp_path, "srt"))
    assert ass.get_script_info("ScriptType") == "v4.00+"  # the default file, as Aegisub loads it
    assert ass.events[0].start == 2820
    assert "{\\fs42" in ass.events[0].text  # <font size="42" ...> became override tags


def test_mkv_bytes_and_track_selection(tmp_path: Path):
    path = mkv_fixture(tmp_path, "copy")
    from_bytes = MKVSubtitleFormat().read(path.read_bytes())
    assert len(from_bytes.events) == len(MKVSubtitleFormat().read_path(path).events)
    with pytest.raises(Exception, match="No subtitle track with index 7"):
        MKVSubtitleFormat(track=7).read_path(path)


def test_mkv_cannot_be_written():
    with pytest.raises(UnsupportedOperationError, match="cannot be written"):
        MKVSubtitleFormat().write(file_with((0, 1000, "x")))


# -- the registry and the dispatcher -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("x.ass", "Advanced Substation Alpha"),
        ("x.SSA", "Advanced Substation Alpha"),
        ("x.srt", "SubRip"),
        ("x.vtt", "WebVTT"),
        ("x.mkv", "Matroska"),
        ("x.mks", "Matroska"),
    ],
)
def test_get_reader(name: str, fmt: str):
    assert get_reader(name).name == fmt


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("x.ass", "Advanced Substation Alpha"),
        ("x.srt", "SubRip"),
        ("x.vtt", "WebVTT"),
        ("x.ttxt", "MPEG-4 Streaming Text"),
        ("x.txt", "Plain-Text"),
        ("x.transtation.txt", "TranStation"),
    ],
)
def test_get_writer(name: str, fmt: str):
    assert get_writer(name).name == fmt


def test_unknown_extensions():
    with pytest.raises(UnknownSubtitleFormatError, match=r"reads x\.sub"):
        get_reader("x.sub")
    with pytest.raises(UnknownSubtitleFormatError, match=r"writes x\.mkv"):
        get_writer("x.mkv")
    with pytest.raises(UnknownSubtitleFormatError, match=r"reads x\.ttxt"):
        get_reader("x.ttxt")


def test_register_format_can_put_a_subclass_in_front():
    class LoudSrt(SRTSubtitleFormat):
        @override
        def write(self, file: AssFile) -> bytes:
            return b"loud"

    register_format(LoudSrt, first=True)
    try:
        assert get_writer("x.srt") is LoudSrt
        assert serialize_subtitles(file_with((0, 1, "x")), "x.srt") == b"loud"
    finally:
        unregister_format(LoudSrt)
    assert get_writer("x.srt") is SRTSubtitleFormat
    assert LoudSrt not in formats()


def test_read_and_write_subtitles_round_trip(tmp_path: Path):
    srt = tmp_path / "in.srt"
    srt.write_bytes(b"1\r\n00:00:01,500 --> 00:00:02,000\r\nhello\r\n")
    ass = read_subtitles(srt)
    out = tmp_path / "out.vtt"
    write_subtitles(ass, out)
    assert "00:00:01.500 --> 00:00:02.000" in out.read_bytes().decode("utf-8-sig")


def test_serialize_subtitles_passes_only_the_options_a_format_takes():
    ass = file_with((0, 1000, "x"))
    # fps is TranStation's, play_res is WebVTT's: neither reaches SubRip's constructor
    assert serialize_subtitles(ass, "x.srt", fps=Framerate.from_ratio(25, 1), play_res=(1, 1))


def test_parser_cls_reaches_the_entries_a_reader_builds():
    class MyDialogue(AssDialogue):
        pass

    class MyParser(AssParser):
        @override
        def make_dialogue(self, data: str) -> AssDialogue:
            return MyDialogue.parse(data)

    ass = SRTSubtitleFormat(parser_cls=MyParser).read(b"1\r\n00:00:01,000 --> 00:00:02,000\r\nx\r\n")
    assert isinstance(ass.events[0], MyDialogue)


def test_a_format_of_your_own_only_needs_the_class_attributes():
    class Upper(SubtitleFormat):
        name: ClassVar[str] = "Upper"
        write_wildcards: ClassVar[tuple[str, ...]] = ("upper",)

        @override
        def write(self, file: AssFile) -> bytes:
            return "\n".join(e.text.upper() for e in self.prepare(file).events).encode()

    register_format(Upper)
    try:
        assert serialize_subtitles(file_with((0, 1000, "quiet")), "x.upper") == b"QUIET"
    finally:
        unregister_format(Upper)


# -- the command line --------------------------------------------------------------------------


def test_convert_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "out.srt"
    assert main(["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out)]) == 0
    text = out.read_bytes().decode("utf-8-sig")
    assert text.startswith("1\r\n00:00:02,820 --> 00:00:04,670\r\n")
    capsys.readouterr()


def test_convert_command_to_stdout_needs_the_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["convert", str(CASES / "01-basic-syl.ass"), "-o", "-"]) == 1
    assert "needs --to" in capsys.readouterr().err


def test_convert_command_transtation_reports_a_missing_frame_rate(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "out.transtation.txt"
    assert main(["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out)]) == 1
    assert "frame rate" in capsys.readouterr().err
    assert main(["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "--fps", "25"]) == 0
    assert out.read_bytes().decode("utf-8-sig").startswith("SUB[")
    capsys.readouterr()


def test_convert_command_refuses_to_replace_without_y(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "out.srt"
    out.write_bytes(b"old")
    assert main(["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-n"]) == 1
    assert out.read_bytes() == b"old"
    assert main(["convert", str(CASES / "01-basic-syl.ass"), "-o", str(out), "-y"]) == 0
    assert out.read_bytes() != b"old"
    capsys.readouterr()
