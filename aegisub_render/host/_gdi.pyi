"""Type stubs for the _gdi extension module (see _gdi.pyx)."""

def measure(
    face: str,
    height: int,
    bold: bool,
    italic: bool,
    underline: bool,
    strikeout: bool,
    charset: int,
    text: str,
    spacing: float,
) -> tuple[float, float, float, float, str] | None: ...
def add_private_font(path: str) -> bool: ...
