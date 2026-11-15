"""逐詞 `\\k` 時間的讀出、寫回與微調。

    python -X utf8 ktime.py extract x.ass                       # 列出每行每個音節的起點（cs，距行首）
    python -X utf8 ktime.py extract x.ass --json t.json         # 存成表，可以直接改數字
    python -X utf8 ktime.py apply   x.ass --json t.json         # 寫回
    python -X utf8 ktime.py shift   x.ass --line 0:01:40.12 --style OPJP --word 2 +30
    python -X utf8 ktime.py copy    x.ass --from OPJP --to OPCHT  # 把日文行的分段時間複製到中文行
    python -X utf8 ktime.py segk    x.ass --fps 24000/1001        # 純切段的 \\k：檢查值對不對（--write 改掉）

音節由 `aegisub_render` 的 `parse_karaoke_syllables()` 解出來（**不要自己寫正規式**），
寫回時只改 `\\k` 的數值，文字、inline fx（`\\-salt`）與其他標籤逐字保留。

表的格式（JSON，給人改的）：

    {"lines": [{"start": "0:01:40.12", "end": "0:01:43.00", "style": "OPJP",
                "actor": "OP market down", "words": ["ドル円ショート！", "ドル円ショート！"],
                "cs": [0, 74]}]}

`cs` 是**每一個 `\\k` 標籤的起點**（centisecond，距行首），所以長度等於該行 `\\k` 的個數，
最後一段的長度由行的結束時間決定。零長度的分隔音節（`{\\k0}　`）也占一格。
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oped  # noqa: E402
from oped import AssDialogue, AssFile, ass_formatted, parse_time  # noqa: E402

from aegisub_render.ass.karaoke import parse_karaoke_syllables  # noqa: E402

K_TAG = re.compile(r"(\\[kK][fo]?)(\d+)")


def syllables(ev: AssDialogue) -> list[tuple[int, str]]:
    """(起點 cs（距行首）, 文字) 一個 `\\k` 一項。"""
    base = ev.start
    out = [(round((syl.start_time - base) / 10), syl.text) for syl in parse_karaoke_syllables(ev)]
    # 行首若有 `\k` 之前的內容，解出來會多一個音節；每個 `\k` 標籤剛好對應一項，所以用標籤數對齊
    tags = len(K_TAG.findall(ev.text))
    if len(out) == tags + 1:
        out = out[1:]
    if len(out) != tags:
        # 解析器會把零長度的空音節併掉（行首的 `{\k0}{\k87}胸` 只回一個音節），
        # 但寫回時是一個 `\k` 標籤一格，少一格就整行對不上。這種行改成照標籤逐一走。
        out = _syllables_by_tags(ev)
    return out


def _syllables_by_tags(ev: AssDialogue) -> list[tuple[int, str]]:
    """一個 `\\k` 標籤一項（起點由前面各標籤的長度累加），文字是到下一個 `\\k` 之前的可見字。"""
    out: list[tuple[int, str]] = []
    cs = 0
    found = list(K_TAG.finditer(ev.text))
    for i, m in enumerate(found):
        seg = ev.text[m.end() : found[i + 1].start() if i + 1 < len(found) else len(ev.text)]
        seg = seg.split("}", 1)[1] if "}" in seg else ""  # 這個標籤所在區塊的剩餘部分
        out.append((cs, re.sub(r"\{[^}]*(\}|$)", "", seg)))
        cs += int(m.group(2))
    return out


def targets(ass: AssFile) -> list[AssDialogue]:
    return [e for e in oped.events_of(ass) if "\\k" in e.text or "\\K" in e.text]


def _key(ev: AssDialogue, seen: dict[tuple[str, str], int]) -> tuple[str, str, int]:
    """同一個開始時間＋樣式可能有好幾行（日文區與中文區的 credit），所以鍵要再加序號。"""
    k = (ass_formatted(ev.start), ev.style)
    seen[k] = seen.get(k, -1) + 1
    return (*k, seen[k])


def dump_table(ass: AssFile) -> dict[str, object]:
    lines: list[dict[str, object]] = []
    seen: dict[tuple[str, str], int] = {}
    for ev in targets(ass):
        syls = syllables(ev)
        start, style, i = _key(ev, seen)
        lines.append(
            {
                "start": start,
                "style": style,
                "i": i,
                "end": ass_formatted(ev.end),
                "actor": ev.actor,
                "words": [t for _, t in syls if t.strip(" 　")],
                "cs": [c for c, _ in syls],
                "cs_end": syls[-1][0] + _last_duration(ev) if syls else 0,
            }
        )
    return {"lines": lines}


def _last_duration(ev: AssDialogue) -> int:
    tags = K_TAG.findall(ev.text)
    return int(tags[-1][1]) if tags else 0


def load_table(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def set_cs(ev: AssDialogue, cs: list[int], cs_end: int | None = None) -> bool:
    """把這一行的 `\\k` 數值改成 `cs` 描述的分段。回傳有沒有真的改到。

    `cs_end` 是最後一段的結束（預設沿用該行現有的最後一段長度，所以「只改前面幾個詞」
    不會順手把尾巴拉到行尾——有些行的 `\\k` 總和本來就短於行長）。
    """
    tags = K_TAG.findall(ev.text)
    if len(tags) != len(cs):
        raise SystemExit(
            f"{ass_formatted(ev.start)} {ev.style}: 表裡有 {len(cs)} 段，行上有 {len(tags)} 個 \\k；"
            "音節數不同時要先改文字的分段"
        )
    if cs_end is None:
        cs_end = cs[-1] + _last_duration(ev) if cs else 0
    bounds = [*cs, cs_end]
    durs = [max(0, bounds[i + 1] - bounds[i]) for i in range(len(cs))]
    it = iter(durs)
    new = K_TAG.sub(lambda m: f"{m.group(1)}{next(it)}", ev.text)
    if new == ev.text:
        return False
    ev.text = new
    return True


def apply_table(ass: AssFile, table: dict[str, object]) -> int:
    by_key: dict[tuple[str, str, int], tuple[list[int], int | None]] = {}
    for row in table["lines"]:  # type: ignore[index]
        key = (str(row["start"]), str(row["style"]), int(row.get("i", 0)))  # type: ignore[union-attr]
        end = row.get("cs_end")  # type: ignore[union-attr]
        by_key[key] = ([int(c) for c in row["cs"]], int(end) if end is not None else None)  # type: ignore[index]
    changed = 0
    seen: dict[tuple[str, str], int] = {}
    for ev in targets(ass):
        hit = by_key.get(_key(ev, seen))
        if hit is not None and set_cs(ev, hit[0], hit[1]):
            changed += 1
    return changed


def copy_timing(ass: AssFile, src_style: str, dst_style: str) -> int:
    """把來源樣式的分段時間複製到目標樣式的同一句（以開始時間配對，音節數必須相同）。"""
    src = {
        ass_formatted(e.start): ([c for c, _ in syllables(e)], e.start, _last_duration(e))
        for e in targets(ass)
        if e.style == src_style
    }
    changed = 0
    for ev in targets(ass):
        if ev.style != dst_style:
            continue
        hit = src.get(ass_formatted(ev.start))
        if hit is None:
            print(f"  {ass_formatted(ev.start)} {dst_style}: 來源沒有對應的行，跳過", file=sys.stderr)
            continue
        cs, _, last = hit
        if set_cs(ev, cs, cs[-1] + last if cs else 0):
            changed += 1
    return changed


def seg_value(fps: str) -> int:
    """只分段、不切軸的 `\\k` 要用的數值＝一幀的毫秒數四捨五入（`round(1000/fps)`）。

    24000/1001 → 42、30000/1001 → 33、25 → 40、50 → 20、60000/1001 → 17。
    """
    if "/" in fps:
        num, _, den = fps.partition("/")
        rate = float(num) / float(den)
    else:
        rate = float(fps)
    return round(1000 / rate)


def seg_check(ass: AssFile, value: int, write: bool) -> tuple[int, dict[int, int]]:
    """檢查（或改寫）純切段的 `\\k` 值。回傳 (改了幾行, 看到哪些值各幾個)。"""
    seen: dict[int, int] = {}
    changed = 0
    for ev in targets(ass):
        for _tag, v in K_TAG.findall(ev.text):
            seen[int(v)] = seen.get(int(v), 0) + 1
        if write:
            new = K_TAG.sub(lambda m: f"{m.group(1)}{value}", ev.text)
            if new != ev.text:
                ev.text = new
                changed += 1
    return changed, seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["extract", "apply", "shift", "copy", "segk"])
    ap.add_argument("target", type=Path)
    ap.add_argument("--json", type=Path, help="extract 的輸出／apply 的輸入")
    ap.add_argument("--line", help="shift：行的開始時間（0:01:40.12）")
    ap.add_argument("--style", help="shift：行的樣式")
    ap.add_argument("--word", type=int, help="shift：第幾個音節（0 起算）")
    ap.add_argument("delta", nargs="?", help="shift：位移（cs，可帶正負號）")
    ap.add_argument("--from", dest="src", help="copy：來源樣式")
    ap.add_argument("--to", dest="dst", help="copy：目標樣式")
    ap.add_argument("--fps", help="segk：片源幀率（24000/1001、30000/1001、25…）")
    ap.add_argument("--write", action="store_true", help="segk：真的改掉（**只有純切段的檔才可以**）")
    a = ap.parse_args()

    ass = oped.load(a.target)

    if a.mode == "segk":
        if not a.fps:
            ap.error("segk 需要 --fps")
        value = seg_value(a.fps)
        changed, seen = seg_check(ass, value, a.write)
        print(f"{a.fps} 的切段 \\k 應該是 \\k{value}")
        print("  檔案裡看到：" + "、".join(f"\\k{v} × {n}" for v, n in sorted(seen.items())))
        if a.write:
            oped.save(a.target, ass)
            print(f"  已全部改成 \\k{value}（{changed} 行）")
        elif set(seen) - {value}:
            print("  有不是這個值的 \\k：如果這個檔是純切段（不靠 \\k 切軸）就用 --write 統一掉；")
            print("  如果 \\k 是真的時間軸，**不要** --write。")
        return 0

    if a.mode == "extract":
        table = dump_table(ass)
        if a.json:
            a.json.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"寫出 {a.json}（{len(table['lines'])} 行）")  # type: ignore[index]
        else:
            for row in table["lines"]:  # type: ignore[index]
                print(f"{row['start']} {row['style']:10} {row['cs']}  {' | '.join(row['words'])}")
        return 0

    if a.mode == "apply":
        if not a.json:
            ap.error("apply 需要 --json")
        n = apply_table(ass, load_table(a.json))
        oped.save(a.target, ass)
        print(f"{a.target.name}: 改了 {n} 行")
        return 0

    if a.mode == "copy":
        if not (a.src and a.dst):
            ap.error("copy 需要 --from 與 --to")
        n = copy_timing(ass, a.src, a.dst)
        oped.save(a.target, ass)
        print(f"{a.target.name}: {a.src} -> {a.dst}，改了 {n} 行")
        return 0

    if not (a.line and a.word is not None and a.delta):
        ap.error("shift 需要 --line、--word 與位移量")
    want = parse_time(a.line)
    delta = int(a.delta)
    for ev in targets(ass):
        if ev.start != want or (a.style and ev.style != a.style):
            continue
        cs = [c for c, _ in syllables(ev)]
        cs[a.word] += delta
        set_cs(ev, cs)
        print(f"{ass_formatted(ev.start)} {ev.style}: {cs}")
    oped.save(a.target, ass)
    return 0


if __name__ == "__main__":
    sys.exit(main())
