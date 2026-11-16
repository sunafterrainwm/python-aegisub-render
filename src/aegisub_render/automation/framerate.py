"""Transform Framerate (src/export_framerate.cpp)."""

from typing import ClassVar, override

from aegisub_render._crt import cdiv
from aegisub_render.ass.entries import AssDialogue
from aegisub_render.ass.file import AssFile
from aegisub_render.ass.override import BlockType, OverrideParam, ParamClass, VarType, parse_dialogue_blocks
from aegisub_render.automation.base import AutomationContext, AutomationError, ExportFilter, parse_bool, parse_fps
from aegisub_render.video.vfr import Framerate


def trunc_cs(time: int) -> int:
    """Truncate a time to centisecond precision (C division, so toward zero)."""
    return cdiv(time, 10) * 10


class TransformFramerateFilter(ExportFilter):
    """AssTransformFramerateFilter: retime the lines, and the times inside override tags.

    Options (`Transform Framerate=input=23.976:output=25`):

    input, output
        a frame rate ("23.976", "24000/1001") or a v1/v2 timecodes file. Either one left out is
        the project's own frame rate -- the video, --fps or --timecodes -- which is what the
        dialog starts from; with neither the filter would have nothing to do, so that is an
        error rather than a silent no-op.
    reverse
        swap the two, like the dialog's "Reverse transformation" box.

    The dialog's "Variable" output is the project's timecodes, so `output` left out is it.
    """

    name: ClassVar[str] = "Transform Framerate"
    description: ClassVar[str] = (
        "Transform subtitle times, including those in override tags, from an input framerate to an output framerate."
    )
    priority: ClassVar[int] = 1000
    positional: ClassVar[tuple[str, ...]] = ("input", "output", "reverse")

    def __init__(self, input: str | None = None, output: str | None = None, reverse: str = "0") -> None:
        self._input_spec = input
        self._output_spec = output
        self._reverse = parse_bool(reverse, f"{self.name}: reverse")
        self.input = parse_fps(input, f"{self.name}: input") if input is not None else None
        self.output = parse_fps(output, f"{self.name}: output") if output is not None else None
        # the two rates process() is running with, named as the C++ names them
        self._fps_in = Framerate()
        self._fps_out = Framerate()
        # per-line state, as the C++ keeps it on the filter object
        self._line: AssDialogue | None = None
        self._new_start = 0
        self._new_end = 0
        self._old_k = 0
        self._new_k = 0

    def rates(self, ctx: AutomationContext) -> tuple[Framerate, Framerate]:
        """LoadSettings: the two frame rates, defaulting to the project's and swapped by reverse."""
        if self.input is None and self.output is None:
            raise AutomationError(
                f"{self.name}: needs input= or output= (a frame rate such as 23.976 or 24000/1001, "
                "or a timecodes file); the one left out is the project's own frame rate"
            )
        project = ctx.fps
        if (self.input is None or self.output is None) and not project.is_loaded():
            raise AutomationError(
                f"{self.name}: only one of input= and output= was given and there is no project "
                "frame rate to use for the other (pass --fps, --timecodes or a video)"
            )
        src = self.input if self.input is not None else project
        dst = self.output if self.output is not None else project
        return (dst, src) if self._reverse else (src, dst)

    @override
    def process_filter(self, file: AssFile, ctx: AutomationContext) -> None:
        """AssTransformFramerateFilter::TransformFrameRate."""
        self._fps_in, self._fps_out = self.rates(ctx)
        if not self._fps_in.is_loaded() or not self._fps_out.is_loaded():
            return
        for diag in file.events:
            self._line = diag
            self._new_k = 0
            self._old_k = 0
            self._new_start = trunc_cs(self.convert_time(diag.start))
            self._new_end = trunc_cs(self.convert_time(diag.end) + 9)

            blocks = parse_dialogue_blocks(diag.text)
            for block in blocks:
                if block.type is BlockType.OVERRIDE:
                    block.process_parameters(self._transform_time_tags)
            diag.start = self._new_start
            diag.end = self._new_end
            diag.update_text(blocks)

    def _transform_time_tags(self, name: str, param: OverrideParam) -> None:
        """AssTransformFramerateFilter::TransformTimeTags."""
        del name
        if param.type not in (VarType.INT, VarType.FLOAT):
            return
        line = self._line
        if line is None:
            return
        par_val = param.get_int(0)

        if param.classification is ParamClass.RELATIVE_TIME_START:
            value = self.convert_time(trunc_cs(line.start) + par_val) - self._new_start
            # An end time of 0 is actually the end time of the line, so ensure nonzero is never
            # converted to 0. Needed here rather than in the end case because start/end here
            # mean which end of the line the time is relative to, not whether it is the start or
            # the end time (compare \move and \fad).
            if value == 0 and par_val != 0:
                value = 1
            param.set_int(value)
        elif param.classification is ParamClass.RELATIVE_TIME_END:
            param.set_int(self._new_end - self.convert_time(trunc_cs(line.end) - par_val))
        elif param.classification is ParamClass.KARAOKE:
            start = cdiv(line.start, 10) + self._old_k + par_val
            value = cdiv(self.convert_time(start * 10) - self._new_start, 10) - self._new_k
            self._old_k += par_val
            self._new_k += value
            param.set_int(value)

    def convert_time(self, time: int) -> int:
        """AssTransformFramerateFilter::ConvertTime."""
        frame = self._fps_out.frame_at_time(time)
        frame_start = self._fps_out.time_at_frame(frame)
        frame_end = self._fps_out.time_at_frame(frame + 1)
        frame_dur = frame_end - frame_start
        dist = (time - frame_start) / frame_dur if frame_dur else 0.0

        new_start = self._fps_in.time_at_frame(frame)
        new_end = self._fps_in.time_at_frame(frame + 1)
        new_dur = new_end - new_start
        return int(new_start + new_dur * dist)
