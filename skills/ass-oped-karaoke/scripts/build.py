"""組裝交付檔：換掉 code／template 行，（可選）注入 `\\k`，再套用卡拉OK模板。

    python -X utf8 build.py x.ass --lua op.lua op_line.lua --render
    python -X utf8 build.py x.ass --lua op.lua --templates templates_op.txt --prune --render
    python -X utf8 build.py x.ass --render                 # 只重新套用模板（改過 Name／時間／樣式之後）
    python -X utf8 build.py x.ass --lua op_lite.lua --templates templates_lite.txt --prune -o x_lite.ass

**手調過的 `{\\k}`、樣式、表頭、專案設定一律原樣保留**：這支只動 code／template 行
（以及 `--ktiming` 指定時的 `\\k` 長度）。要從頭產生整個檔是另一回事，不要用這支做。

`--templates` 的格式是一行一條模板行，欄位以 `|` 分隔（前後空白會被去掉），`#` 開頭是註解：

    layer | style | effect                                   | text              | name(可省略)
    0     | OPJP  | code line all                            | fxgroup.motes=…   | 分組開關
    2     | OPJP  | template char noblank loop 2             | !opLayer("body")! | 歌詞本體

**只給 `--lua`（沒有 `--templates`）時只換 code 行**，既有的 template 行原封不動——模板清單不在
Lua 裡的專案（`--@`／`blank` 方言）就是這樣用。給了 `--templates` 才會把整組 code／template 行
重建，`--@@` 方言（模板行寫在 Lua 裡）則用 `--order lua` 連模板行一起從 Lua 來。

排序規則（`--order auto`，有 `--templates` 時的預設）：先 `code line`／`code syl` 開關，
再 `template` 行，最後 `code once` 函式庫。這是為了讓 template 行落在 `[Events]` 的前 50 行內
（kara-templater 的 validation 看這個）。`--order lua` 則完全照 Lua 檔裡的順序，
`--@@` 方言請用這個（它的 prepare／finish 位置有意義）。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ktime  # noqa: E402
import luasync  # noqa: E402
import oped  # noqa: E402
from oped import AssDialogue, AssFile  # noqa: E402


def read_templates(path: Path) -> list[AssDialogue]:
    out: list[AssDialogue] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            raise SystemExit(f"{path}:{n}: 需要至少 4 欄（layer|style|effect|text）：{raw}")
        layer, style, effect, text = parts[0], parts[1], parts[2], parts[3]
        name = parts[4] if len(parts) > 4 else ""
        out.append(oped.comment(text, effect, layer=int(layer), style=style, actor=name))
    return out


def _sort_key(ev: AssDialogue) -> int:
    if ev.effect.startswith("code once"):
        return 2
    if ev.effect.startswith("template"):
        return 1
    return 0


def rebuild(
    ass: AssFile,
    blocks: list[luasync.Block],
    templates: list[AssDialogue],
    *,
    order: str,
    style: str,
    labels: bool = False,
) -> int:
    """刪掉現有的 code／template 行，在原位插入新的一組。回傳插入幾行。"""
    new = list(templates) + [
        oped.comment(b.text, b.effect, style=style, actor=b.name if labels else "") for b in blocks
    ]
    if order == "auto":
        new.sort(key=_sort_key)
    idx = [i for i, e in enumerate(ass.events) if oped.is_code(e) or oped.is_template(e)]
    if idx:
        at = idx[0]
        for i in reversed(idx):
            del ass.events[i]
    else:
        at = 0
    ass.events[at:at] = new
    return len(new)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path, help="要改的 .ass（交付檔）")
    ap.add_argument("-o", "--out", type=Path, help="輸出到別的檔（預設就地改寫）")
    ap.add_argument("--lua", type=Path, nargs="+", default=[], help="Lua 原始碼")
    ap.add_argument("--templates", type=Path, help="模板行清單（見 docstring）")
    ap.add_argument("--dialect", choices=["blank", "at", "atat"], help="Lua 分段方言（預設自動偵測）")
    ap.add_argument("--effect", help="blank／at 方言的預設 Effect 欄")
    ap.add_argument("--style", default="Default", help="code 行要掛在哪個樣式上（預設 Default）")
    ap.add_argument("--order", choices=["auto", "lua"], help="新行的排序（預設：有 --templates 時 auto）")
    ap.add_argument("--labels", action="store_true", help="把 Lua 的區塊標籤寫進 code 行的 Name 欄")
    ap.add_argument("--prune", action="store_true", help="遞移裁剪：只留模板行用得到的 code once 區塊")
    ap.add_argument("--ktiming", type=Path, help="逐詞時間表（ktime.py extract --json 的格式），注入 \\k")
    ap.add_argument("--drop-fx", action="store_true", help="先丟掉舊的 fx 行（套模板時本來也會重建）")
    ap.add_argument("--render", action="store_true", help="最後套用 kara-templater")
    ap.add_argument("--font-dir", type=Path, action="append", default=[], help="額外字型目錄（可重複）")
    a = ap.parse_args()

    ass = oped.load(a.target)
    out_path = a.out or a.target

    if a.lua or a.templates:
        blocks = luasync.read_blocks(a.lua, a.dialect, a.effect) if a.lua else []
        templates = read_templates(a.templates) if a.templates else []
        order = a.order or ("auto" if a.templates else "lua")
        if a.prune:
            used = [e.text for e in templates if not e.effect.startswith("code once")]
            used += [b.text for b in blocks if not b.effect.startswith("code once")]
            once = [b for b in blocks if b.effect.startswith("code once")]
            keep = {id(b) for b in luasync.code_once(once, used)}
            blocks = [b for b in blocks if not b.effect.startswith("code once") or id(b) in keep]
        if templates:
            n = rebuild(ass, blocks, templates, order=order, style=a.style, labels=a.labels)
            print(f"{a.target.name}: 換上 {n} 條 code／template 行")
        else:
            # 沒給模板清單＝模板行不歸這次管：只換 code 行，其餘（含既有的 template 行）原封不動
            diffs = luasync.sync(ass, blocks, write=True, labels=a.labels)
            print(f"{a.target.name}: 同步 {len(blocks)} 塊 code 行" + (f"（{len(diffs)} 處不同）" if diffs else "（本來就一致）"))

    if a.ktiming:
        changed = ktime.apply_table(ass, ktime.load_table(a.ktiming))
        print(f"{a.target.name}: 注入 \\k，改了 {changed} 行")

    if a.drop_fx:
        before = len(ass.events)
        ass.events[:] = [e for e in ass.events if not oped.is_fx(e)]
        print(f"{a.target.name}: 丟掉 {before - len(ass.events)} 條舊的 fx 行")

    save_ui_state = True
    if a.render:
        settings = oped.settings_for(a.font_dir)
        save_ui_state = oped.apply_template(ass, out_path, settings=settings).save_ui_state

    oped.save(out_path, ass, save_ui_state=save_ui_state)
    print(f"{out_path.name}: {oped.stats(ass)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
