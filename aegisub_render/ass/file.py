"""AssFile (src/ass_file.cpp)."""

import bisect
from dataclasses import dataclass, field

from aegisub_render._crt import atoi, cdiv, wrap_int32
from aegisub_render.ass.entries import (
    AssAttachment,
    AssDialogue,
    AssInfo,
    AssStyle,
    ExtradataEntry,
    ProjectProperties,
)


@dataclass(slots=True)
class AssFile:
    info: list[AssInfo] = field(default_factory=list[AssInfo])
    styles: list[AssStyle] = field(default_factory=list[AssStyle])
    events: list[AssDialogue] = field(default_factory=list[AssDialogue])
    attachments: list[AssAttachment] = field(default_factory=list[AssAttachment])
    extradata: list[ExtradataEntry] = field(default_factory=list[ExtradataEntry])
    properties: ProjectProperties = field(default_factory=ProjectProperties)
    next_extradata_id: int = 1
    # Whether the file these were read from had the sections (not part of Aegisub's AssFile).
    had_project_garbage: bool = False
    had_extradata: bool = False

    def line_count(self) -> int:
        return len(self.info) + len(self.styles) + len(self.events)

    def get_script_info(self, key: str) -> str:
        lk = key.lower()
        for info in self.info:
            if info.key.lower() == lk:
                return info.value
        return ""

    def get_script_info_int(self, key: str) -> int:
        return atoi(self.get_script_info(key))

    def get_resolution(self) -> tuple[int, int]:
        sw = self.get_script_info_int("PlayResX")
        sh = self.get_script_info_int("PlayResY")
        # Gabest logic: default is 384x288, assume 1280x1024 if either height or
        # width are that, otherwise assume 4:3 if only height or width are set.
        if sw == 0 and sh == 0:
            sw, sh = 384, 288
        elif sw == 0:
            sw = 1280 if sh == 1024 else wrap_int32(cdiv(sh * 4, 3))
        elif sh == 0:
            sh = 1024 if sw == 1280 else wrap_int32(cdiv(sw * 3, 4))
        return sw, sh

    def add_extradata(self, key: str, value: str) -> int:
        for data in self.extradata:
            if data.key == key and data.value == value:
                return data.id
        self.extradata.append(ExtradataEntry(self.next_extradata_id, key, value))
        self.next_extradata_id = (self.next_extradata_id + 1) & 0xFFFFFFFF
        return self.extradata[-1].id

    def _enumerate_extradata(self, id_list: list[int]) -> list[ExtradataEntry]:
        """enumerate_extradata: lower_bound over the (id-sorted) list, without an equality check."""
        result: list[ExtradataEntry] = []
        ids = [e.id for e in self.extradata]
        begin = 0
        for wanted in id_list:
            idx = bisect.bisect_left(ids, wanted, lo=begin)
            if idx != len(ids):
                result.append(self.extradata[idx])
                begin = idx
        return result

    def get_extradata(self, id_list: list[int]) -> list[ExtradataEntry]:
        return self._enumerate_extradata(id_list)

    def clean_extradata(self) -> None:
        if not self.extradata:
            return
        ids_used: set[int] = set()
        for line in self.events:
            if not line.extradata_ids:
                continue
            keys_used: dict[str, int] = {}
            for e in self._enumerate_extradata(line.extradata_ids):
                keys_used[e.key] = e.id
            ids_used.update(keys_used.values())
            if len(keys_used) != len(line.extradata_ids):
                line.extradata_ids = sorted(keys_used.values())
        if len(ids_used) != len(self.extradata):
            self.extradata = [e for e in self.extradata if e.id in ids_used]
