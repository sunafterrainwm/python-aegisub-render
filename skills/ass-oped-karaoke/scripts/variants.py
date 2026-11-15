"""衍生版：NCOP／NCED、lite、繁簡檢查。規則與理由見 references/variants.md。

    # NC 版：平移時間軸、平移拍點原點、關掉本篇專用的避讓、換片源、加 credit
    python -X utf8 variants.py nc op_kfx.ass -o op_ncop.ass --shift -1678 --fps 24000/1001 \\
        --video ncop.mp4 --title "<作品> - OP kfx (NCOP)" --const OP_B0 --set OP_FINAL_SHIFT=0 \\
        --credit credit_op.ass --drop-fx --render

`--const`／`--set` 後面接的是**你的 Lua 裡那個常數真正的名字**（上面的 `OP_B0`、`OP_FINAL_SHIFT`
只是舉例）。名字對不到任何 code 行時會直接報錯並列出檔裡有的常數，不會寫出檔案——
拍點原點沒跟著平移的 NC 版，特效會整首和拍子錯開。

    # lite 版：同一份 Lua，另一張模板清單（遞移裁剪會把用不到的定義留在外面）
    python -X utf8 variants.py lite op_kfx.ass -o op_kfx_lite.ass \\
        --lua lua_common.lua lua_op.lua lua_op_lite.lua --templates templates_op_lite.txt --render

    # 繁簡：檢查 +TC/+SC 後綴與日中行一一對應
    python -X utf8 variants.py lang op_kfx.ass --check

`--shift` 同 `aegisub-render macro offset` 的語法：`[+-]影格數`（要 `--fps` 或能讀時間碼的影片）
或 `[+-][[[hh:]mm:]ss].ms`。**NC 的對應用影格數**，毫秒換算會差一格。

有歌詞行會被平移到 0 秒之前時同樣直接報錯、不寫檔：那幾乎都是位移的方向或數值錯了，
或這份字幕根本不在你以為的那條時間軸上。真的要把它們夾到 `0:00:00.00` 才加 `--allow-clamp`。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as build_mod  # noqa: E402
import luasync  # noqa: E402
import oped  # noqa: E402
from oped import AssFile, ass_formatted  # noqa: E402

from aegisub_render import parse_offset, shift_time_by_frames  # noqa: E402
from aegisub_render.ass.time import clamp_time  # noqa: E402


# --- NC ---------------------------------------------------------------------------------


def frame_delta_ms(frames: int, fr) -> int:
    """`frames` 格的標稱長度（ms），正負號跟著 `frames`。

    code 行裡的常數（拍點原點）用這個數字平移。事件行的時間會被 `shift_time_by_frames` 吸附到
    影格的中點，所以每一行實際移動的毫秒數都差一點；拿其中任何一行的差值去移常數，
    來回平移一次（+N 再 -N）常數就回不到原值了。標稱長度是對稱的，回得去。
    """
    span = fr.time_at_frame(abs(frames)) - fr.time_at_frame(0)
    return span if frames >= 0 else -span


def shift_events(ass: AssFile, spec: str, fps: str | None, video: Path | None) -> tuple[int, list[tuple[int, str]]]:
    """平移每一行。回傳（常數要跟著移的毫秒數, 會被推到 0 秒之前的行〔原起點, 樣式〕）。"""
    off = parse_offset(spec)
    fr = oped.framerate_of(video, fps) if off.by_frames else None
    delta = frame_delta_ms(off.amount, fr) if fr is not None else off.amount
    # 影格位移會把時間吸附到影格中點，剛好落在第 0 格的行不算「被推到 0 之前」
    slack = abs(frame_delta_ms(1, fr)) if fr is not None else 0
    lost: list[tuple[int, str]] = []
    for ev in ass.events:
        # code／template 行的時間本來就是 0，沒有意義；只看真的有時間的行
        if not (oped.is_code(ev) or oped.is_template(ev)) and ev.end > ev.start and ev.start + delta < -slack:
            lost.append((ev.start, ev.style))
        if fr is not None:
            ev.start = shift_time_by_frames(ev.start, off.amount, fr)
            ev.end = shift_time_by_frames(ev.end, off.amount, fr)
        else:
            ev.start = clamp_time(ev.start + off.amount)
            ev.end = clamp_time(ev.end + off.amount)
    return delta, lost


CONST_ASSIGN = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*(-?\d+(?:\.\d+)?)\b(?!\s*=)")


def numeric_consts(ass: AssFile) -> dict[str, str]:
    """code 行裡所有「名字 = 數字」的指派（名字 → 值），給報錯與提醒用。"""
    found: dict[str, str] = {}
    for ev in ass.events:
        if oped.is_code(ev):
            for name, value in CONST_ASSIGN.findall(ev.text):
                found.setdefault(name, value)
    return found


def patch_consts(ass: AssFile, shift_ms: int, consts: list[str], sets: list[str]) -> tuple[int, list[str]]:
    """code 行裡的常數：`--const NAME` 跟著平移，`--set NAME=VALUE` 直接換值。

    回傳（改到幾條 code 行, 一次都沒對到的名字）。
    """
    n = 0
    hits: dict[str, int] = {name: 0 for name in [*consts, *(s.partition("=")[0] for s in sets)]}
    for ev in ass.events:
        if not oped.is_code(ev):
            continue
        text = ev.text
        for name in consts:
            text, k = re.subn(
                rf"\b({re.escape(name)}\s*=\s*)(-?\d+)\b(?!\s*=)",
                lambda m: f"{m.group(1)}{int(m.group(2)) + shift_ms}",
                text,
            )
            hits[name] += k
        for s in sets:
            name, _, value = s.partition("=")
            text, k = re.subn(
                rf"\b({re.escape(name)}\s*=\s*)-?[\d.]+", lambda m, v=value: f"{m.group(1)}{v}", text
            )
            hits[name] += k
        if text != ev.text:
            ev.text = text
            n += 1
    return n, [name for name, k in hits.items() if k == 0]


def set_links(ass: AssFile, video: Path | None, title: str | None) -> None:
    if video:
        ass.properties.video_file = video.name if video.parent == Path(".") else str(video)
        ass.properties.audio_file = ass.properties.video_file
    if title is not None:
        for info in ass.info:
            if info.key == "Title":
                info.value = title
                return


def append_credit(ass: AssFile, credit: Path) -> int:
    """把另一個 .ass 的事件行（通常是曲名 credit 與它的載體行）接到後面，樣式缺的補上。"""
    src = oped.load(credit)
    have = {s.name for s in ass.styles}
    for s in src.styles:
        if s.name not in have:
            ass.styles.append(s.copy())
    for ev in src.events:
        ass.events.append(ev.copy())
    return len(src.events)


def cmd_nc(a: argparse.Namespace) -> int:
    ass = oped.load(a.target)
    out = a.out or a.target
    if a.drop_fx:
        before = len(ass.events)
        ass.events[:] = [e for e in ass.events if not oped.is_fx(e)]
        print(f"丟掉 {before - len(ass.events)} 條 fx 行")
    known = numeric_consts(ass)
    delta, lost = shift_events(ass, a.shift, a.fps, a.video) if a.shift else (0, [])
    if lost and not a.allow_clamp:
        first = min(t for t, _ in lost)
        raise SystemExit(
            f"{a.target.name}: {len(lost)} 行會被平移到 0 秒之前"
            f"（最早的一行在 {ass_formatted(first)}，位移是 {delta} ms）。沒有寫出任何檔案。\n"
            "這幾乎都代表位移的方向或數值不對，或這份字幕不在你以為的那條時間軸上"
            "（例如它已經是 NC 時間軸、或歌詞還沒對到正片）。先確認字幕的時間再平移；\n"
            "真的要把這些行夾到 0:00:00.00 就加 --allow-clamp。"
        )
    if a.shift:
        print(f"平移 {a.shift} → 常數位移 {delta} ms" + (f"（{len(lost)} 行被夾到 0:00:00.00）" if lost else ""))
    if a.const or a.set:
        n, missing = patch_consts(ass, delta, a.const, a.set)
        if missing:
            have = "、".join(f"{k}={v}" for k, v in known.items()) or "（沒有任何「名字 = 數字」的指派）"
            raise SystemExit(
                f"{a.target.name}: code 行裡找不到常數 {'、'.join(missing)}。沒有寫出任何檔案。\n"
                f"檔裡有的常數：{have}\n"
                "--const／--set 要給 Lua 裡真正的名字（說明文件裡的 B0、OP_B0 只是舉例）。"
            )
        print(f"改了 {n} 條 code 行的常數（{'、'.join([*a.const, *a.set])}）")
    if a.shift and not a.const:
        origins = [k for k in known if re.search(r"B0|BEAT|ORIGIN", k, re.I)]
        if origins:
            print(
                f"提醒：code 行裡有 {'、'.join(f'{k}={known[k]}' for k in origins)}，看起來是拍點原點，"
                f"這次沒有跟著平移。要平移請加 --const {origins[0]}"
            )
    set_links(ass, a.video, a.title)
    if a.credit:
        print(f"加上 {append_credit(ass, a.credit)} 條 credit／載體行")
    save_ui_state = True
    if a.render:
        save_ui_state = oped.apply_template(ass, out, settings=oped.settings_for(a.font_dir)).save_ui_state
    oped.save(out, ass, save_ui_state=save_ui_state)
    print(f"{out.name}: {oped.stats(ass)}")
    return 0


# --- lite -------------------------------------------------------------------------------


def cmd_lite(a: argparse.Namespace) -> int:
    ass = oped.load(a.target)
    out = a.out or a.target
    blocks = luasync.read_blocks(a.lua, a.dialect, None) if a.lua else []
    templates = build_mod.read_templates(a.templates) if a.templates else []
    if not templates:
        raise SystemExit("lite 版需要 --templates：lite 的差別就是另一張模板清單")
    used = [e.text for e in templates if not e.effect.startswith("code once")]
    once = [b for b in blocks if b.effect.startswith("code once")]
    keep = {id(b) for b in luasync.code_once(once, used)}
    pruned = [b for b in blocks if not b.effect.startswith("code once") or id(b) in keep]
    print(f"遞移裁剪：{len(once)} 塊 code once → {len(once) - (len(blocks) - len(pruned))} 塊")
    ass.events[:] = [e for e in ass.events if not oped.is_fx(e)]
    n = build_mod.rebuild(ass, pruned, templates, order=a.order, style=a.style, labels=a.labels)
    print(f"換上 {n} 條 code／template 行")
    save_ui_state = True
    if a.render:
        save_ui_state = oped.apply_template(ass, out, settings=oped.settings_for(a.font_dir)).save_ui_state
    oped.save(out, ass, save_ui_state=save_ui_state)
    print(f"{out.name}: {oped.stats(ass)}")
    return 0


# --- 繁簡 -------------------------------------------------------------------------------

LANG = re.compile(r"^(.*)([+#])(TC|SC)$")


def cmd_lang(a: argparse.Namespace) -> int:
    ass = oped.load(a.target)
    rows = [e for e in oped.events_of(ass) if not oped.is_karaoke(e) or True]
    bad = 0
    marks: dict[str, int] = {}
    for e in rows:
        m = LANG.match(e.actor)
        if m:
            marks[m.group(2) + m.group(3)] = marks.get(m.group(2) + m.group(3), 0) + 1
    print("語言標記：" + ("、".join(f"{k} × {v}" for k, v in sorted(marks.items())) or "（沒有）"))

    # 日中行一一對應：同一句的 (Start, End, Name 去掉語言標記) 必須成對
    def key(e) -> tuple[int, int, str]:
        m = LANG.match(e.actor)
        return (e.start, e.end, m.group(1) if m else e.actor)

    jp = [e for e in rows if e.style.endswith("JP")]
    ch = [e for e in rows if e.style.endswith(("CHT", "CHS", "CH"))]
    jk, ck = {key(e) for e in jp}, {key(e) for e in ch}
    for k in sorted(jk - ck):
        print(f"  只有日文行：{ass_formatted(k[0])} {k[2]}")
        bad += 1
    for k in sorted(ck - jk):
        print(f"  只有中文行：{ass_formatted(k[0])} {k[2]}")
        bad += 1
    print(f"日文 {len(jp)} 行、中文 {len(ch)} 行，配對" + ("OK" if not bad else f"有 {bad} 處對不上"))
    if bad:
        print("對不上就是字幕的資料問題，不要放寬配對規則（例如改成比對時間重疊）。")
    return 1 if bad and a.check else 0


# --- CLI --------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("target", type=Path)
        p.add_argument("-o", "--out", type=Path)
        p.add_argument("--render", action="store_true")
        p.add_argument("--font-dir", type=Path, action="append", default=[])

    p_nc = sub.add_parser("nc", help="NCOP／NCED 版")
    common(p_nc)
    p_nc.add_argument("--shift", help="[+-]影格數 或 [+-][[[hh:]mm:]ss].ms")
    p_nc.add_argument("--fps", help="影格位移用的 fps（24000/1001）")
    p_nc.add_argument("--video", type=Path, help="NC 影片（同時寫進 Video File／Audio File）")
    p_nc.add_argument("--title", help="[Script Info] 的 Title")
    p_nc.add_argument("--const", action="append", default=[], help="跟著平移的常數名（Lua 裡真正的名字，例如 OP_B0），可重複")
    p_nc.add_argument("--allow-clamp", action="store_true", help="允許把平移到 0 秒之前的行夾到 0:00:00.00（預設報錯）")
    p_nc.add_argument("--set", action="append", default=[], help="直接換值的常數（OP_FINAL_SHIFT=0），可重複")
    p_nc.add_argument("--credit", type=Path, help="要接上去的 credit 行（另一個 .ass）")
    p_nc.add_argument("--drop-fx", action="store_true")

    p_lite = sub.add_parser("lite", help="lite 版")
    common(p_lite)
    p_lite.add_argument("--lua", type=Path, nargs="+", default=[])
    p_lite.add_argument("--templates", type=Path, required=True)
    p_lite.add_argument("--dialect", choices=["blank", "at", "atat"])
    p_lite.add_argument("--order", choices=["auto", "lua"], default="auto")
    p_lite.add_argument("--style", default="Default")
    p_lite.add_argument("--labels", action="store_true", help="把 Lua 的區塊標籤寫進 code 行的 Name 欄")

    p_lang = sub.add_parser("lang", help="繁簡標記與日中行配對檢查")
    p_lang.add_argument("target", type=Path)
    p_lang.add_argument("--check", action="store_true", help="有問題時以 exit 1 結束")

    a = ap.parse_args()
    return {"nc": cmd_nc, "lite": cmd_lite, "lang": cmd_lang}[a.mode](a)


if __name__ == "__main__":
    sys.exit(main())
