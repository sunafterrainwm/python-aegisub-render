"""Karaoke syllable parsing (src/ass_karaoke.cpp ParseKaraokeSyllables, libaegisub/ass/karaoke.cpp)."""

from dataclasses import dataclass, field

from aegisub_render.ass.entries import AssDialogue
from aegisub_render.ass.override import BlockType, parse_dialogue_blocks
from aegisub_render.ass.time import time_int


@dataclass(slots=True)
class KaraokeSyllable:
    start_time: int = 0
    duration: int = 0
    text: str = ""
    tag_type: str = "\\k"
    ovr_tags: dict[int, str] = field(default_factory=dict[int, str])

    def get_text(self, k_tag: bool) -> str:
        ret = ""
        if k_tag:
            ret = f"{{{self.tag_type}{(self.duration + 5) // 10}}}"
        idx = 0
        for pos in sorted(self.ovr_tags):
            ret += self.text[idx:pos]
            ret += self.ovr_tags[pos]
            idx = pos
        ret += self.text[idx:]
        return ret

    def add_ovr(self, text: str) -> None:
        pos = len(self.text)
        self.ovr_tags[pos] = self.ovr_tags.get(pos, "") + text


def parse_karaoke_syllables(line: AssDialogue) -> list[KaraokeSyllable]:
    syls: list[KaraokeSyllable] = []
    syl = KaraokeSyllable(start_time=time_int(line.start), duration=0, tag_type="\\k")

    for block in parse_dialogue_blocks(line.text):
        match block.type:
            case BlockType.PLAIN:
                syl.text += block.text
            case BlockType.COMMENT | BlockType.DRAWING:
                syl.add_ovr(block.text)
            case BlockType.OVERRIDE:
                in_tag = False
                for tag in block.tags:
                    if tag.valid and tag.name.lower().startswith("\\k"):
                        if in_tag:
                            syl.add_ovr("}")
                            in_tag = False
                        if tag.name == "\\K":
                            tag.name = "\\kf"
                        if syl.duration > 0 or syl.text:
                            syls.append(syl)
                            syl = KaraokeSyllable(
                                start_time=syl.start_time, duration=syl.duration, text="", tag_type=syl.tag_type
                            )
                        syl.tag_type = tag.name
                        syl.start_time += syl.duration
                        syl.duration = tag.params[0].get_int(0) * 10
                    else:
                        if not in_tag:
                            syl.add_ovr("{")
                        in_tag = True
                        syl.add_ovr(str(tag))
                if in_tag:
                    syl.add_ovr("}")
    syls.append(syl)
    return syls
