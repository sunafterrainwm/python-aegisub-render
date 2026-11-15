"""Font helpers shared by both builds of the _font module (and testable without either)."""

import struct
from pathlib import Path

FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".fon", ".fnt")
# Family, full name, typographic family: the names a LOGFONT face name can select a font by.
FACE_NAME_IDS = (1, 4, 16)


def font_files(directory: Path) -> list[Path]:
    """The font files in `directory` and its subdirectories."""
    return [f for f in sorted(directory.rglob("*")) if f.suffix.lower() in FONT_SUFFIXES and f.is_file()]


def font_names(table: bytes) -> set[str]:
    """The FACE_NAME_IDS names in an OpenType 'name' table, in every language it has them in."""
    names: set[str] = set()
    if len(table) < 6:
        return names
    _, count, storage = struct.unpack_from(">3H", table, 0)
    for i in range(count):
        record = 6 + 12 * i
        if record + 12 > len(table):
            break
        platform, encoding, _, name_id, length, offset = struct.unpack_from(">6H", table, record)
        if name_id not in FACE_NAME_IDS:
            continue
        raw = table[storage + offset : storage + offset + length]
        if platform in (0, 3):  # Unicode, Windows: UTF-16BE
            names.add(raw.decode("utf-16-be", "replace"))
        elif platform == 1 and encoding == 0:  # Macintosh Roman
            names.add(raw.decode("mac_roman", "replace"))
    return names
