"""Lua 原始碼 ↔ .ass 的 code／template 行：壓縮、比對、寫回、還原。

    python -X utf8 luasync.py check   x.ass --lua op.lua op_line.lua     # 逐字一致嗎（exit 1 表示不同）
    python -X utf8 luasync.py apply   x.ass --lua op.lua op_line.lua     # 壓成一行寫回 .ass
    python -X utf8 luasync.py extract x.ass -o restored.lua              # 反向：從交付檔還原可讀版
    python -X utf8 luasync.py blocks  op.lua                             # 看會壓成什麼（除錯）

三種分段方言（預設自動偵測，可用 --dialect 指定）：

  blank 以**空行**分塊，一塊一行。`--@ 標籤` 寫進 Name 欄；區塊開頭的 `--` 說明轉成 `--[[ ]]`。
  at    以 **`--@` 標頭**分段，段內的空行無所謂，所有 `--` 整行註解一律丟掉。
  atat  `--@@ <Effect 欄> | <區段名>` 明寫 Effect。`code` 區段以空白接行，`template` 區段直接相接。

`blank`／`at` 的 Effect 預設 `code once`；檔名以 `_line.lua` 結尾、或檔首寫
`--@effect code line all` 時改用它（`--effect` 旗標優先）。

兩種方言都**不允許行尾 `--` 註解**：區塊會被接成一行，`--` 會把後面全部吃掉。

還有給別的工具用的 `code_once()`：從模板行用到的符號出發做遞移裁剪，lite 版因此可以和完整版
共用同一份 Lua（見 references/lua-layout.md）。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oped  # noqa: E402
from oped import AssDialogue, AssFile  # noqa: E402

DEFAULT_EFFECT = "code once"
IDENT = re.compile(r"[A-Za-z_]\w*")


class Block:
    """壓好的一個區塊：Effect 欄、Name 欄、要寫進 Text 欄的那一行。"""

    __slots__ = ("effect", "name", "text")

    def __init__(self, effect: str, name: str, text: str) -> None:
        self.effect = effect
        self.name = name
        self.text = text

    def __repr__(self) -> str:
        return f"Block({self.effect!r}, {self.name!r}, {self.text[:40]!r}…)"


# --- 壓縮 -------------------------------------------------------------------------------


def detect_dialect(src: str) -> str:
    """`--@@` → atat；段內出現「程式碼之後又有 `--` 註解」→ at；否則 blank。

    後面那個判斷就是兩種方言真正的差別：`blank` 的說明一律寫在區塊開頭（才能轉成 `--[[ ]]`），
    所以「註解在程式碼後面」只可能是 `at`（它把所有註解丟掉）。
    """
    if "--@@" in src:
        return "atat"
    for chunk in re.split(r"\n\s*\n", src.strip()):
        seen_code = False
        for raw in chunk.split("\n"):
            s = raw.strip()
            if not s or s.startswith("--@"):
                continue
            if s.startswith("--"):
                if seen_code:
                    return "at"
            else:
                seen_code = True
    return "blank"


def _default_effect(src: str, path: Path, effect: str | None) -> tuple[str, str]:
    """(Effect 欄, 去掉 `--@effect` 標頭之後的原始碼)"""
    m = re.match(r"--@effect\s+(.+)", src.lstrip())
    if m:
        rest = src.lstrip()
        src = rest.split("\n", 1)[1] if "\n" in rest else ""
        return effect or m.group(1).strip(), src
    if effect:
        return effect, src
    return ("code line all" if path.name.endswith("_line.lua") else DEFAULT_EFFECT), src


def blocks_split_at(src: str, path: Path, effect: str | None = None) -> list[Block]:
    """`--@` 方言：以 `--@ 標籤` 分段，段內所有 `--` 整行註解丟掉，其餘以空白接成一行。"""
    default, src = _default_effect(src, path, effect)
    out: list[Block] = []
    label = ""
    cur: list[str] = []
    for raw in src.splitlines():
        s = raw.strip()
        if s.startswith("--@"):
            if cur:
                out.append(Block(default, label, " ".join(cur)))
                cur = []
            label = s[3:].strip()
            continue
        if not s or s.startswith("--"):
            continue
        _check_line(s, path.name)
        cur.append(s)
    if cur:
        out.append(Block(default, label, " ".join(cur)))
    return out


def _check_line(line: str, where: str) -> None:
    if "--" in line and line.count('"') < 2 and "--[[" not in line:
        raise SystemExit(f"{where}: 區塊裡有行註解，接成一行會把後面吃掉：{line}")
    if line.startswith("("):
        raise SystemExit(f"{where}: 以 '(' 開頭的行接起來會變成函式呼叫：{line}")


def blocks_blank(src: str, path: Path, effect: str | None = None) -> list[Block]:
    """`blank` 方言：空行分塊，一塊一行；區塊開頭的 `--` 說明轉成 `--[[ ]]`。"""
    default, src = _default_effect(src, path, effect)
    out: list[Block] = []
    label = ""
    for chunk in re.split(r"\n\s*\n", src.strip()):
        notes: list[str] = []
        code: list[str] = []
        for raw in chunk.split("\n"):
            s = raw.strip()
            if not s:
                continue
            if s.startswith("--@ "):
                label = s[4:].strip()
            elif s.startswith("--") and not code:
                notes.append(s[2:].strip())
            else:
                _check_line(s, f"{path.name}")
                code.append(s)
        if not code:
            continue
        text = " ".join(code)
        if notes:
            note = " ".join(notes)
            if "]]" in note:
                raise SystemExit(f"{path.name}: 說明裡不能有 ']]'：{note}")
            text = f"--[[ {note} ]] {text}"
        out.append(Block(default, label, text))
    return out


def blocks_atat(src: str, path: Path, name: str = "Template") -> list[Block]:
    """`--@@ <Effect> | <區段名>` 方言：Effect 明寫在標頭上。"""
    out: list[Block] = []
    head: str | None = None
    body: list[str] = []

    def flush() -> None:
        if head is None:
            return
        effect, _, label = (x.strip() for x in head.partition("|"))
        kind = effect.split()[0] if effect else ""
        if kind == "code":
            for line in body:
                _check_line(line, f"{path.name} [{label or effect}]")
        text = " ".join(body) if kind == "code" else "".join(body)
        if text:
            out.append(Block(effect, name, text))

    for raw in src.splitlines():
        if raw.startswith("--@@"):
            flush()
            head, body = raw[4:].strip(), []
            continue
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        body.append(line)
    flush()
    return out


def read_blocks(paths: list[Path], dialect: str | None = None, effect: str | None = None) -> list[Block]:
    out: list[Block] = []
    for p in paths:
        src = p.read_text(encoding="utf-8")
        d = dialect or detect_dialect(src)
        if d == "atat":
            out += blocks_atat(src, p)
        elif d == "at":
            out += blocks_split_at(src, p, effect)
        else:
            out += blocks_blank(src, p, effect)
    return out


# --- 遞移裁剪 ---------------------------------------------------------------------------


def defined_symbols(text: str) -> list[str]:
    """這個區塊定義了哪些符號：`function f(...)`，或開頭的 `a, b = …` 多重指派。

    一個都找不到（例如只是呼叫了什麼）就回傳空的——那種區塊**一律保留**，裁剪不掉。
    """
    code = text.split("]] ", 1)[-1]
    m = re.match(r"(?:local\s+)?function\s+([\w.:]+)\s*\(", code)
    if m:
        return [m.group(1)]
    m = re.match(r"(?:local\s+)?([\w.]+(?:\s*,\s*[\w.]+)*)\s*=[^=]", code)
    if m:
        return [s.strip() for s in m.group(1).split(",")]
    return []


def code_once(blocks: list[Block], used_by: list[str]) -> list[Block]:
    """只留下 `used_by`（模板行與 code line 行的文字）遞移用得到的區塊，保持原順序。

    同一個符號在兩個檔都有定義時兩塊都留著（覆寫要蓋在預設之後）；
    沒有定義任何符號的區塊一律保留。
    """
    defs = [defined_symbols(b.text) for b in blocks]
    known = {s for d in defs for s in d}
    refs = [{s for s in IDENT.findall(b.text) if s in known} - set(d) for b, d in zip(blocks, defs)]
    want = {s for line in used_by for s in IDENT.findall(line) if s in known}
    while True:
        grow = {s for r, d in zip(refs, defs) if want & set(d) for s in r} - want
        if not grow:
            break
        want |= grow
    return [b for b, d in zip(blocks, defs) if not d or want & set(d)]


# --- 與 .ass 同步 -------------------------------------------------------------------------


def _targets(ass: AssFile, effect: str) -> list[int]:
    return [i for i, e in enumerate(ass.events) if (oped.is_code(e) or oped.is_template(e)) and e.effect == effect]


def sync(ass: AssFile, blocks: list[Block], write: bool, labels: bool = False) -> list[str]:
    """比對（write=False）或寫回（write=True）。回傳差異說明，空表示一致。

    `labels=True` 才會把 `--@ 標籤` 寫進 Name 欄（預設不動 Name，免得只是同步程式碼卻改了別的欄位）。
    """
    diffs: list[str] = []
    by_effect: dict[str, list[Block]] = {}
    for b in blocks:
        by_effect.setdefault(b.effect, []).append(b)
    for effect, group in by_effect.items():
        idx = _targets(ass, effect)
        if not idx:
            diffs.append(f"{effect}: .ass 裡沒有這個 Effect 的行（{len(group)} 塊）")
            if write:
                raise SystemExit(f"{effect}: 找不到可以取代的行，請先在 .ass 裡放一條佔位的 Comment")
            continue
        if len(idx) != len(group):
            diffs.append(f"{effect}: {len(idx)} 塊 -> {len(group)} 塊")
            if write:
                proto = ass.events[idx[0]]
                for i in reversed(idx):
                    del ass.events[i]
                for k, b in enumerate(group):
                    ass.events.insert(idx[0] + k, _as_event(proto, b, labels))
            continue
        for i, b in zip(idx, group):
            ev = ass.events[i]
            if ev.text != b.text:
                diffs.append(f"{effect}: 第 {i + 1} 行不同")
                if write:
                    ev.text = b.text
            if labels and b.name and ev.actor != b.name and write:
                ev.actor = b.name
    return diffs


def _as_event(proto: AssDialogue, b: Block, labels: bool = False) -> AssDialogue:
    ev = proto.copy()
    ev.text = b.text
    if labels and b.name:
        ev.actor = b.name
    return ev


# --- 還原 -------------------------------------------------------------------------------

_SPLIT = re.compile(r"(?<=[;)])\s+(?=(?:local\s+|function\s+|if\s+|for\s+|while\s+|return\b|end\b))")


def extract(ass: AssFile) -> str:
    """把 .ass 裡的 code／template 行還原成可讀的 `--@@` 方言。

    **不會和原本的 .lua 逐字相同**（註解與換行回不來），這是救援用的。
    """
    out: list[str] = [
        "-- 由 luasync.py extract 還原，註解與換行無法回復；正式來源仍是原本的 .lua",
    ]
    for e in ass.events:
        if not (oped.is_code(e) or oped.is_template(e)):
            continue
        text = e.text
        note = ""
        m = re.match(r"--\[\[(.*?)\]\]\s*(.*)$", text)
        if m:
            note, text = m.group(1).strip(), m.group(2)
        out.append("")
        out.append(f"--@@ {e.effect} | {e.actor or '-'}")
        if note:
            out.append(f"-- {note}")
        body = text.replace(" end ", "\nend\n") if e.effect.startswith("code") else text
        for line in _SPLIT.split(body):
            out.append(line.strip())
    return "\n".join(out) + "\n"


# --- CLI --------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["check", "apply", "extract", "blocks"])
    ap.add_argument("target", type=Path, help="check/apply/extract：.ass；blocks：.lua")
    ap.add_argument("--lua", type=Path, nargs="+", default=[], help="Lua 原始碼（可多個，順序就是輸出順序）")
    ap.add_argument("-o", "--out", type=Path, help="extract 的輸出（預設 stdout）")
    ap.add_argument("--dialect", choices=["blank", "at", "atat"], help="分段方言（預設自動偵測）")
    ap.add_argument("--effect", help="at 方言的預設 Effect 欄（預設 code once）")
    ap.add_argument("--labels", action="store_true", help="把 `--@ 標籤` 寫進 Name 欄（預設不動 Name）")
    ap.add_argument("--prune-with", type=Path, help="遞移裁剪：只保留這個檔（一行一條模板行文字）用得到的區塊")
    a = ap.parse_args()

    if a.mode == "blocks":
        for b in read_blocks([a.target], a.dialect, a.effect):
            print(f"[{b.effect}] ({b.name}) {b.text}")
        return 0

    ass = oped.load(a.target)

    if a.mode == "extract":
        text = extract(ass)
        if a.out:
            a.out.write_text(text, encoding="utf-8")
            print(f"寫出 {a.out}")
        else:
            sys.stdout.write(text)
        return 0

    if not a.lua:
        ap.error("check/apply 需要 --lua")
    blocks = read_blocks(a.lua, a.dialect, a.effect)
    if a.prune_with:
        used = a.prune_with.read_text(encoding="utf-8").splitlines()
        keep = {id(b) for b in code_once([b for b in blocks if b.effect.startswith("code once")], used)}
        blocks = [b for b in blocks if not b.effect.startswith("code once") or id(b) in keep]

    diffs = sync(ass, blocks, write=(a.mode == "apply"), labels=a.labels)
    if a.mode == "check":
        print(f"{a.target.name}: " + ("一致" if not diffs else "不同"))
        for d in diffs:
            print("  " + d)
        return 0 if not diffs else 1
    if diffs:
        oped.save(a.target, ass)
        print(f"{a.target.name}: 已寫回 {len(blocks)} 塊")
        for d in diffs:
            print("  " + d)
    else:
        print(f"{a.target.name}: 本來就一致，沒有寫檔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
