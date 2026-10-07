"""Generate seeded, plausible karaoke-template subtitle files for the snapshot tests.

    uv run python scripts/gen_cases.py            # writes tests/cases/*.ass
    uv run python scripts/gen_cases.py --seed 7 --count 12 --out somewhere/

Every random choice comes from random.Random(seed * 1000 + case number), and every template
that uses math.random seeds it in a `code once` line, so the files and their rendered output
are reproducible. Each case exercises a different set of kara-templater features; open them
in Aegisub 3.5.0, run Automation > Apply karaoke template, save, and drop the result into
tests/aegisub/ to turn the snapshot test into a comparison against real Aegisub output.
"""

import argparse
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FONTS = ["Arial", "Times New Roman", "Segoe UI", "Microsoft JhengHei", "Yu Gothic", "Georgia"]
ROMAJI = ["ka", "ze", "no", "to", "ri", "mi", "chi", "sa", "ki", "yo", "na", "ra", "su", "wa", "hi", "ta", "ru", "ne"]
KANA = ["か", "ぜ", "の", "と", "り", "み", "ち", "さ", "き", "よ", "な", "ら", "す", "わ", "ひ", "た", "る", "ね"]
KANJI = [("空", "そら"), ("花", "はな"), ("夢", "ゆめ"), ("星", "ほし"), ("心", "こころ"), ("光", "ひかり")]
ENGLISH = ["sing", "with", "me", "to", "night", "the", "stars", "are", "shin", "ing", "for", "you", "a", "gain"]

FORMAT_STYLE = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
    "MarginR, MarginV, Encoding"
)
FORMAT_EVENTS = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"


def ass_time(ms: int) -> str:
    cs = ms // 10
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def color(rng: random.Random, alpha: int = 0) -> str:
    return f"&H{alpha:02X}{rng.randrange(256):02X}{rng.randrange(256):02X}{rng.randrange(256):02X}"


@dataclass
class Case:
    name: str
    rng: random.Random
    res: tuple[int, int] = (1920, 1080)
    styles: list[str] = field(default_factory=list[str])
    events: list[str] = field(default_factory=list[str])

    def style(
        self,
        name: str,
        *,
        font: str | None = None,
        size: int | None = None,
        align: int | None = None,
        spacing: float = 0,
        scale_x: float = 100,
        bold: bool = False,
        italic: bool = False,
    ) -> str:
        rng = self.rng
        font = font or rng.choice(FONTS)
        size = size or rng.choice([36, 42, 48, 54, 60, 72])
        align = align or rng.choice([1, 2, 3, 5, 7, 8, 9])
        margins = (rng.choice([10, 20, 40]), rng.choice([10, 20, 40]), rng.choice([10, 30, 50]))
        spacing_text = f"{spacing:g}"
        self.styles.append(
            f"Style: {name},{font},{size},{color(rng)},{color(rng)},{color(rng)},{color(rng, 0x80)},"
            f"{-1 if bold else 0},{-1 if italic else 0},0,0,{scale_x:g},100,{spacing_text},0,1,"
            f"{rng.choice([1, 2, 3])},{rng.choice([0, 1, 2])},{align},{margins[0]},{margins[1]},{margins[2]},1"
        )
        return name

    def comment(self, effect: str, text: str, style: str = "Default", layer: int = 0) -> None:
        self.events.append(f"Comment: {layer},0:00:00.00,0:00:00.00,{style},,0,0,0,{effect},{text}")

    def line(
        self,
        start: int,
        end: int,
        text: str,
        *,
        style: str = "Default",
        effect: str = "karaoke",
        actor: str = "",
        comment: bool = False,
    ) -> None:
        kind = "Comment" if comment else "Dialogue"
        self.events.append(f"{kind}: 0,{ass_time(start)},{ass_time(end)},{style},{actor},0,0,0,{effect},{text}")

    def render(self) -> str:
        w, h = self.res
        out = [
            "[Script Info]",
            f"Title: aegisub-render test case {self.name}",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            f"PlayResX: {w}",
            f"PlayResY: {h}",
            "",
            "[V4+ Styles]",
            FORMAT_STYLE,
            *self.styles,
            "",
            "[Events]",
            FORMAT_EVENTS,
            *self.events,
            "",
        ]
        return "\r\n".join(out)


def karaoke_text(
    rng: random.Random,
    words: list[str],
    count: int,
    *,
    tag: str = "\\k",
    inline_fx: list[str] | None = None,
    spaces: bool = True,
) -> tuple[str, int]:
    parts: list[str] = []
    total = 0
    for i in range(count):
        dur = rng.choice([10, 15, 20, 25, 30, 40, 50, 60])
        total += dur * 10
        fx = ""
        if inline_fx and rng.random() < 0.4:
            fx = f"\\-{rng.choice(inline_fx)}"
        syl = rng.choice(words)
        if spaces and i < count - 1 and rng.random() < 0.3:
            syl += " "
        parts.append(f"{{{tag}{dur}{fx}}}{syl}")
    return "".join(parts), total


def lyric_lines(case: Case, n: int, words: list[str], **kw: object) -> None:
    rng = case.rng
    t = rng.randrange(500, 3000, 10)
    for _ in range(n):
        text, dur = karaoke_text(rng, words, rng.randint(4, 9), **kw)  # type: ignore[arg-type]
        lead = rng.choice([0, 100, 200, 300])
        case.line(t, t + dur + lead, text if not lead else f"{{\\k{lead // 10}}}" + text)
        t += dur + lead + rng.randrange(200, 1500, 10)


# ---------------------------------------------------------------------------------- the cases


def case_basic_syl(case: Case) -> None:
    case.style("Default")
    case.comment("code once", "math.randomseed(1001)")
    case.comment(
        "template syl",
        "{\\an5\\pos($scenter,$smiddle)\\fad(!math.random(50,150)!,!math.random(100,300)!)\\t($start,$end,\\fscx120\\fscy120)}",
    )
    lyric_lines(case, 4, ROMAJI)


def case_line_and_preline(case: Case) -> None:
    case.style("Default")
    case.style("Sub", align=8)
    case.comment("template pre-line", "{\\an!line.styleref.align!\\fad(150,150)\\blur2}", style="Default")
    case.comment("template line", "{\\an7\\pos($lleft,$ltop)\\1c&H00FFFF&}", style="Sub")
    lyric_lines(case, 3, ENGLISH)
    t = case.rng.randrange(20000, 25000, 10)
    text, dur = karaoke_text(case.rng, ENGLISH, 5)
    case.line(t, t + dur, text, style="Sub")


def case_char_and_noblank(case: Case) -> None:
    case.style("Default", spacing=2)
    case.comment("template char noblank", "{\\an5\\pos($scenter,$smiddle)\\fscx$swidth}")
    case.comment(
        "template syl noblank notext", "{\\an5\\pos($scenter,!$sbottom+5!)\\p1}m 0 0 l $swidth 0 $swidth 3 0 3"
    )
    lyric_lines(case, 3, ROMAJI)


def case_retime_and_layers(case: Case) -> None:
    case.style("Default")
    case.comment("template syl", '!retime("syl", -200, 200)!{\\an5\\pos($scenter,$smiddle)\\fad(200,200)}', layer=1)
    case.comment("template syl", '!retime("presyl", -500, 0)!{\\an5\\pos($scenter,!$smiddle-20!)\\alpha&H80&}', layer=0)
    case.comment(
        "template syl", '!retime("postsyl", 0, 300)!{\\an5\\move($scenter,$smiddle,$scenter,!$smiddle+30!)}', layer=2
    )
    case.comment("template line", '!retime("line", -300, 300)!{\\an5\\pos($lcenter,$lmiddle)\\fad(300,300)}')
    lyric_lines(case, 3, KANA, spaces=False)


def case_code_lines(case: Case) -> None:
    case.style("Default")
    case.comment("code once", "math.randomseed(2002); palette = { '&H3030FF&', '&H30FF30&', '&HFF3030&', '&H30FFFF&' }")
    case.comment("code line", "linecolor = palette[(line.i % #palette) + 1]")
    case.comment("code syl", "sylrot = (syl.i % 2 == 0) and 10 or -10")
    case.comment(
        "template syl",
        "{\\an5\\pos($scenter,$smiddle)\\1c!linecolor!\\t($start,$mid,\\frz!sylrot!)\\t($mid,$end,\\frz0)}",
    )
    lyric_lines(case, 4, ENGLISH)


def case_loop(case: Case) -> None:
    case.style("Default")
    case.comment("code once", "math.randomseed(3003)")
    case.comment(
        "template syl loop 4",
        "{\\an5\\move($scenter,$smiddle,!$scenter+math.random(-40,40)!,!$smiddle+math.random(-40,40)!,$start,$end)\\alpha&H!string.format('%02X', 40*j)!&\\blur!j!}",
    )
    lyric_lines(case, 2, ROMAJI)


def case_inline_fx(case: Case) -> None:
    case.style("Default")
    case.comment("template syl fx big", "{\\an5\\pos($scenter,$smiddle)\\t($start,$end,\\fscx150\\fscy150)}")
    case.comment("template syl fx spin", "{\\an5\\pos($scenter,$smiddle)\\t($start,$end,\\frz360)}")
    case.comment("template syl", "{\\an5\\pos($scenter,!$smiddle+40!)\\alpha&HA0&}")
    lyric_lines(case, 3, ROMAJI, inline_fx=["big", "spin"])


def case_multi_highlight(case: Case) -> None:
    case.style("Default")
    case.comment("template syl multi", "{\\an5\\pos($scenter,$smiddle)\\t($start,$end,\\1c&H0000FF&)}")
    rng = case.rng
    t = 1000
    for _ in range(3):
        parts: list[str] = []
        total = 0
        for _ in range(rng.randint(3, 6)):
            durs = [rng.choice([10, 20, 30]) for _ in range(rng.randint(1, 3))]
            total += sum(durs) * 10
            parts.append(f"{{\\k{durs[0]}}}{rng.choice(ROMAJI)}" + "".join(f"{{\\k{d}}}#" for d in durs[1:]))
        case.line(t, t + total, "".join(parts))
        t += total + 500


def case_furigana(case: Case) -> None:
    case.style("Default", font="Yu Gothic", size=60)
    case.comment("template syl", "{\\an5\\pos($scenter,$smiddle)\\fad(100,100)}")
    case.comment("template furi", "{\\an5\\pos($scenter,$smiddle)\\fad(100,100)\\1c&HC0C0FF&}")
    rng = case.rng
    t = 1500
    for _ in range(3):
        parts: list[str] = []
        total = 0
        for _ in range(rng.randint(3, 5)):
            kanji, reading = rng.choice(KANJI)
            first = True
            for ch in reading:
                d = rng.choice([15, 20, 30])
                total += d * 10
                if first:
                    parts.append(f"{{\\k{d}}}{kanji}|{ch}")
                    first = False
                else:
                    parts.append(f"{{\\k{d}}}#|{ch}")
            if rng.random() < 0.5:
                d = rng.choice([20, 30])
                total += d * 10
                parts.append(f"{{\\k{d}}}{rng.choice(KANA)}")
        case.line(t, t + total, "".join(parts))
        t += total + 800


def case_keeptags_and_kf(case: Case) -> None:
    case.style("Default", italic=True, scale_x=90)
    case.comment("template syl keeptags", "{\\an5\\pos($scenter,$smiddle)}")
    case.comment("template line keeptags", "{\\an5\\pos($lcenter,!$lmiddle+60!)\\alpha&H60&}")
    rng = case.rng
    t = 800
    for tag in ("\\kf", "\\ko", "\\K"):
        parts: list[str] = []
        total = 0
        for _ in range(rng.randint(4, 7)):
            d = rng.choice([20, 30, 40])
            total += d * 10
            extra = rng.choice(["", "\\i0", "\\b1", "\\fs50"])
            parts.append(f"{{{tag}{d}{extra}}}{rng.choice(ENGLISH)}")
        case.line(t, t + total, "".join(parts))
        t += total + 700


def case_remember_recall(case: Case) -> None:
    case.style("Default")
    case.comment("code once", "math.randomseed(4004)")
    case.comment(
        "template syl",
        "{\\an5\\pos($scenter,$smiddle)\\1c!remember('c', _G.util.ass_color(math.random(0,255), math.random(0,255), math.random(0,255)))!}",
    )
    case.comment("template syl", "{\\an5\\pos($scenter,!$smiddle+50!)\\1c!recall.c!\\alpha&H80&}")
    lyric_lines(case, 3, ROMAJI)


def case_mixed_styles(case: Case) -> None:
    case.res = (1280, 720)
    case.style("Default", font="Arial", size=40, align=2)
    case.style("Top", font="Georgia", size=36, align=8, bold=True)
    case.style("Left", font="Segoe UI", size=30, align=7, spacing=1.5)
    case.comment("template syl", "{\\an5\\pos($scenter,$smiddle)\\bord!line.styleref.outline + 1!}", style="Default")
    case.comment("template syl", "{\\an5\\pos($scenter,$smiddle)\\fscy!line.styleref.scale_y * 1.1!}", style="Top")
    case.comment("template line", "{\\pos($lleft,$ltop)\\an7}", style="Left")
    rng = case.rng
    t = 1000
    for style in ("Default", "Top", "Left", "Default"):
        text, dur = karaoke_text(rng, ENGLISH, rng.randint(4, 7))
        case.line(t, t + dur, text, style=style)
        t += dur + 400
    case.line(t, t + 2000, "a plain line without karaoke", effect="")
    case.line(t, t + 2000, "a commented line", comment=True)
    case.line(t, t + 2000, "{\\pos(1,1)}an old effect line that must be removed", effect="fx")


CASES: list[tuple[str, Callable[[Case], None]]] = [
    ("basic-syl", case_basic_syl),
    ("line-preline", case_line_and_preline),
    ("char-noblank", case_char_and_noblank),
    ("retime-layers", case_retime_and_layers),
    ("code-lines", case_code_lines),
    ("loop", case_loop),
    ("inline-fx", case_inline_fx),
    ("multi-highlight", case_multi_highlight),
    ("furigana", case_furigana),
    ("keeptags-kf", case_keeptags_and_kf),
    ("remember-recall", case_remember_recall),
    ("mixed-styles", case_mixed_styles),
]


def generate(out: Path, seed: int, count: int | None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for n, (name, build) in enumerate(CASES[:count] if count else CASES, 1):
        case = Case(f"{n:02d}-{name}", random.Random(seed * 1000 + n))
        build(case)
        path = out / f"{case.name}.ass"
        path.write_bytes(b"\xef\xbb\xbf" + case.render().encode("utf-8"))
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "cases")
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument("--count", type=int)
    args = parser.parse_args()
    for path in generate(args.out, args.seed, args.count):
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


if __name__ == "__main__":
    main()
