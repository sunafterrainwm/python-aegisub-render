r"""The filtergraph syntax of `--export-filters`, borrowed from ffmpeg.

    name[=value[:value...][:key=value...]][,name...]

so `Fix Styles`, `Transform Framerate=input=23.976:output=25`, or the two of them separated by a
comma. The escaping is ffmpeg's: a backslash makes the next character literal, and `'...'`
quotes a run in which nothing but the closing quote is special. That is what a name containing a
comma, a colon, an equals sign, a backslash or a quote needs -- a space does not, so
`Fix Styles` can be written as it is.

Unlike ffmpeg the separators are read in one pass instead of one level of escaping per nesting
level, so `\:` means a colon wherever it appears and there is never a reason to write `\\\\:`.
"""

from dataclasses import dataclass, field

_SEPARATORS = ",:="


class FilterSpecError(Exception):
    """The filtergraph could not be read."""


@dataclass(slots=True)
class FilterArgs:
    """One filter of a chain: its name and the options written after `=`."""

    name: str
    positional: list[str] = field(default_factory=list[str])
    options: dict[str, str] = field(default_factory=dict[str, str])

    def unparse(self) -> str:
        """The spec that would parse back into this, with everything special escaped."""
        rendered = [escape(p) for p in self.positional]
        rendered += [f"{escape(k)}={escape(v)}" for k, v in self.options.items()]
        return escape(self.name) + ("=" + ":".join(rendered) if rendered else "")


def escape(text: str) -> str:
    """Backslash-escape what the parser would otherwise read as syntax."""
    out: list[str] = []
    for char in text:
        if char in _SEPARATORS or char in "\\'":
            out.append("\\")
        out.append(char)
    return "".join(out)


def _tokenize(spec: str) -> list[tuple[str, str]]:
    """(text, separator) pairs; the separator of the last token is "" (end of string)."""
    tokens: list[tuple[str, str]] = []
    buf: list[str] = []
    quoted = False
    i = 0
    while i < len(spec):
        char = spec[i]
        if quoted:
            if char == "'":
                quoted = False
            else:
                buf.append(char)
            i += 1
            continue
        if char == "\\":
            if i + 1 >= len(spec):
                raise FilterSpecError("filtergraph ends with a backslash")
            buf.append(spec[i + 1])
            i += 2
            continue
        if char == "'":
            quoted = True
            i += 1
            continue
        if char in _SEPARATORS:
            tokens.append(("".join(buf), char))
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    if quoted:
        raise FilterSpecError("filtergraph has an unterminated quote")
    tokens.append(("".join(buf), ""))
    return tokens


def parse_filter_chain(spec: str) -> list[FilterArgs]:
    """Read a filtergraph into one FilterArgs per filter, in the order they are written."""
    chain: list[FilterArgs] = []
    current: FilterArgs | None = None
    pending_key: str | None = None

    for text, sep in _tokenize(spec):
        value = text.strip()
        if current is None:
            if sep == ":":
                raise FilterSpecError(f"unexpected ':' before a filter name in {spec!r}")
            if not value:
                if sep == "=":
                    raise FilterSpecError(f"a filter with no name in {spec!r}")
                continue  # a stray or trailing comma, or an empty graph
            current = FilterArgs(value)
            if sep != "=":
                chain.append(current)
                current = None
            continue
        if sep == "=":
            if pending_key is not None:
                raise FilterSpecError(f"{current.name}: two '=' in the option {pending_key!r}")
            if not value:
                raise FilterSpecError(f"{current.name}: an option with no name in {spec!r}")
            pending_key = value
            continue
        if pending_key is not None:
            if pending_key in current.options:
                raise FilterSpecError(f"{current.name}: the option {pending_key!r} is given twice")
            current.options[pending_key] = text.strip()
            pending_key = None
        elif value or sep == ":":
            current.positional.append(text.strip())
        if sep != ":":
            chain.append(current)
            current = None

    return chain


__all__ = ["FilterArgs", "FilterSpecError", "escape", "parse_filter_chain"]
