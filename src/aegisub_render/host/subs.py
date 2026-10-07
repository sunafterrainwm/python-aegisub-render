"""Python half of LuaAssFile (src/auto4_lua_assfile.cpp): the line list a script edits."""

from aegisub_render.ass.color import Color
from aegisub_render.ass.entries import GROUP_HEADERS, AssDialogue, AssEntry, AssInfo, AssStyle, Group
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.karaoke import parse_karaoke_syllables
from aegisub_render.ass.time import clamp_time, time_int
from aegisub_render.cutil import lua_tolong
from aegisub_render.lua.state import ScriptError
from aegisub_render.textenc import from_bytes

type Flat = list[object]


def _s(v: object) -> str:
    if isinstance(v, bytes):
        return from_bytes(v)
    raise ScriptError(f"aegisub-render internal error: expected a string, got {type(v).__name__}")


def _f(v: object) -> float:
    if isinstance(v, float):
        return v
    raise ScriptError(f"aegisub-render internal error: expected a number, got {type(v).__name__}")


def _i(v: object) -> int:
    return lua_tolong(_f(v))


def _b(v: object) -> bool:
    if isinstance(v, bool):
        return v
    raise ScriptError(f"aegisub-render internal error: expected a boolean, got {type(v).__name__}")


def as_flat(v: object) -> Flat:
    if not isinstance(v, dict):
        raise ScriptError("aegisub-render internal error: entry is not a table")
    table: dict[object, object] = v  # pyright: ignore[reportUnknownVariableType]
    return [table[float(i)] for i in range(1, len(table) + 1) if float(i) in table]


def entry_from_flat(flat: Flat, ass: AssFile) -> AssEntry:
    """LuaAssFile::LuaToAssEntry, after the Lua side has read and validated the fields."""
    cls = _s(flat[0])
    if cls == "info":
        return AssInfo(_s(flat[1]), _s(flat[2]))
    if cls == "style":
        s = AssStyle()
        s.name = _s(flat[1])
        s.font = _s(flat[2])
        s.fontsize = _f(flat[3])
        s.primary = Color.parse(_s(flat[4]))
        s.secondary = Color.parse(_s(flat[5]))
        s.outline = Color.parse(_s(flat[6]))
        s.shadow = Color.parse(_s(flat[7]))
        s.bold = _b(flat[8])
        s.italic = _b(flat[9])
        s.underline = _b(flat[10])
        s.strikeout = _b(flat[11])
        s.scalex = _f(flat[12])
        s.scaley = _f(flat[13])
        s.spacing = _f(flat[14])
        s.angle = _f(flat[15])
        s.borderstyle = _i(flat[16])
        s.outline_w = _f(flat[17])
        s.shadow_w = _f(flat[18])
        s.alignment = _i(flat[19])
        s.margin = [_i(flat[20]), _i(flat[21]), _i(flat[22])]
        s.encoding = _i(flat[23])
        s.update_data()
        return s
    if cls == "dialogue":
        d = AssDialogue()
        d.comment = _b(flat[1])
        d.layer = _i(flat[2])
        d.start = clamp_time(_i(flat[3]))
        d.end = clamp_time(_i(flat[4]))
        d.style = _s(flat[5])
        d.actor = _s(flat[6])
        d.margin = [_i(flat[7]), _i(flat[8]), _i(flat[9])]
        d.effect = _s(flat[10])
        d.text = _s(flat[11])
        extra = flat[12] if len(flat) > 12 else False
        if isinstance(extra, dict):
            kv = as_flat(extra)
            ids = [ass.add_extradata(_s(kv[i]), _s(kv[i + 1])) for i in range(0, len(kv) - 1, 2)]
            d.extradata_ids = sorted(ids)
        return d
    raise ScriptError(f"Found line with unknown class: {cls}")


class OutputLimitError(ScriptError):
    pass


class SubsSession:
    """The C++ object behind one Lua subtitles userdata."""

    def __init__(self, ass: AssFile, max_output_lines: int) -> None:
        self.ass = ass
        self.lines: list[AssEntry | None] = [None] * len(ass.info)
        self.lines.extend(ass.styles)
        self.lines.extend(ass.events)
        self.script_info_copied = False
        self.modified = False
        self.pending_commits = 0
        self.max_output_lines = max_output_lines
        self.produced = 0

    # -- helpers mirroring LuaAssFile

    def _init_script_info_if_needed(self) -> None:
        if self.script_info_copied:
            return
        i = 0
        for info in self.ass.info:
            while self.lines[i] is not None:
                i += 1
            self.lines[i] = info.copy()
            i += 1
        self.script_info_copied = True

    def _queue_for_deletion(self, idx: int) -> None:
        line = self.lines[idx]
        if line is None or line.group == Group.INFO:
            self._init_script_info_if_needed()

    def _prepare_new(self, e: AssEntry) -> None:
        if e.group == Group.INFO:
            self._init_script_info_if_needed()

    def _count_produced(self, n: int) -> None:
        self.produced += n
        if self.produced > self.max_output_lines:
            raise OutputLimitError(
                f"aegisub-render: output line limit exceeded ({self.max_output_lines} lines produced by the script)"
            )

    def entry_at(self, idx0: int) -> AssEntry:
        line = self.lines[idx0]
        return line if line is not None else self.ass.info[idx0]

    # -- operations called from Lua (indices are 1-based where Lua passes them)

    def count(self) -> int:
        return len(self.lines)

    def read(self, idx: int) -> tuple[object, ...]:
        """AssEntryToLua: (class, section, raw, fields...)."""
        e = self.entry_at(idx - 1)
        section = GROUP_HEADERS[e.group]
        if isinstance(e, AssInfo):
            return ("info", section, e.entry_data(), e.key, e.value)
        if isinstance(e, AssDialogue):
            extra: list[str] = []
            for ed in self.ass.get_extradata(e.extradata_ids):
                extra += (ed.key, ed.value)
            return ("dialogue", section, e.entry_data(), e.comment, e.layer, time_int(e.start), time_int(e.end),
                    e.style, e.actor, e.effect, e.margin[0], e.margin[1], e.margin[2], e.text, extra)
        s = e
        return ("style", section, s.entry_data(), s.name, s.font, s.fontsize,
                s.primary.ass_style() + "&", s.secondary.ass_style() + "&",
                s.outline.ass_style() + "&", s.shadow.ass_style() + "&",
                s.bold, s.italic, s.underline, s.strikeout, s.scalex, s.scaley, s.spacing, s.angle,
                s.borderstyle, s.outline_w, s.shadow_w, s.alignment,
                s.margin[0], s.margin[1], s.margin[2], s.encoding)

    def assign(self, idx: int, flat: Flat) -> None:
        e = entry_from_flat(flat, self.ass)
        self.modified = True
        self._queue_for_deletion(idx - 1)
        self._prepare_new(e)
        self.lines[idx - 1] = e

    def append(self, flat: Flat) -> None:
        e = entry_from_flat(flat, self.ass)
        self.modified = True
        self._count_produced(1)
        self._prepare_new(e)
        if not self.lines:
            self.lines.insert(0, e)
            return
        for i in range(len(self.lines), 0, -1):
            cur = self.lines[i - 1]
            cur_group = cur.group if cur is not None else Group.INFO
            if cur_group == e.group:
                self.lines.insert(i, e)
                return
        self.lines.append(e)

    def insert(self, before: int, flats: list[Flat]) -> None:
        new_entries: list[AssEntry] = []
        for flat in flats:
            e = entry_from_flat(flat, self.ass)
            self.modified = True
            self._prepare_new(e)
            new_entries.append(e)
        self._count_produced(len(new_entries))
        self.lines[before - 1 : before - 1] = new_entries

    def delete(self, ids: list[int]) -> None:
        zero_based = sorted(i - 1 for i in ids)
        out: list[AssEntry | None] = []
        id_idx = 0
        for i in range(len(self.lines)):
            if id_idx < len(zero_based) and zero_based[id_idx] == i:
                self.modified = True
                self._queue_for_deletion(i)
                id_idx += 1
            else:
                out.append(self.lines[i])
        self.lines = out

    def deleterange(self, a: int, b: int) -> None:
        start = max(a, 1) - 1
        end = min(b, len(self.lines))
        if start >= end:
            return
        for i in range(start, end):
            self.modified = True
            self._queue_for_deletion(i)
        del self.lines[start:end]

    def resolution(self) -> tuple[int, int]:
        return self.ass.get_resolution()

    def karaoke(self, flat: Flat) -> list[list[object]]:
        """LuaParseKaraokeData (the [0] syllable is added on the Lua side)."""
        e = entry_from_flat(flat, self.ass)
        if not isinstance(e, AssDialogue):
            raise ScriptError("aegisub-render internal error: karaoke entry is not dialogue")
        start = time_int(e.start)
        result: list[list[object]] = []
        for syl in parse_karaoke_syllables(e):
            result.append([syl.duration, syl.start_time - start, syl.start_time + syl.duration - start,
                           syl.tag_type, syl.get_text(False), syl.text])
        return result

    def undo_point(self) -> None:
        self.pending_commits += 1
        self.modified = False

    def apply(self) -> None:
        """ProcessingComplete: write the edited line list back into the file."""
        if not (self.modified or self.pending_commits):
            return
        if self.script_info_copied:
            self.ass.info = []
        self.ass.styles = []
        self.ass.events = []
        for line in self.lines:
            if line is None:
                continue
            match line:
                case AssInfo():
                    self.ass.info.append(line)
                case AssStyle():
                    self.ass.styles.append(line)
                case AssDialogue():
                    self.ass.events.append(line)
