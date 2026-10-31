"""Generate .pyi stubs for the Cython extension modules from the compiled modules themselves.

Cython has no stub generator of its own, but with `embedsignature=True,
embedsignature.format=python` it puts the annotated Python signature written in the .pyx at the
top of every docstring:

    def load(self, code: bytes) -> LuaRef        ->  "load(self, code: bytes) -> LuaRef"
    @property def memory_used(self) -> int       ->  "memory_used: int"
    def __init__(self, limit: int = 0) -> None   ->  "__init__(self, limit: int = 0) -> None"

This script imports the built module, turns those signatures back into a stub, imports every
name a signature uses from the module the object came from, and formats the result with ruff.
native/build.py runs it after compiling; `--check` verifies the committed stubs are current.

Conventions the .pyx files follow so that this works: every public def has full annotations,
properties are read-only (setters would be invisible here), and cdef classes give __init__ a
docstring (otherwise Cython has nowhere to put the constructor signature).
"""

import argparse
import builtins
import importlib
import inspect
import keyword
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
# the modules built on this platform (the text measurement one is _gdi on Windows, _fcft elsewhere)
MODULES = (
    "aegisub_render._crt",
    "aegisub_render.host._gdi" if sys.platform == "win32" else "aegisub_render.host._fcft",
    "aegisub_render.lua._luajit",
)

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SKIP = {"self", "None", "True", "False", "object"}


class StubError(Exception):
    pass


def _split_doc(obj: object) -> tuple[str, str]:
    doc = inspect.getdoc(obj) or ""
    first, _, rest = doc.partition("\n")
    return first.strip(), rest.strip()


def _docstring(text: str, indent: str) -> list[str]:
    if not text:
        return []
    body = text.replace('"""', r"\"\"\"")
    if "\n" not in body:
        return [f'{indent}"""{body}"""']
    lines = [f'{indent}"""{body.splitlines()[0]}']
    lines += [f"{indent}{ln}" if ln else "" for ln in body.splitlines()[1:]]
    lines.append(f'{indent}"""')
    return lines


def _signature(name: str, obj: object) -> tuple[str, str]:
    """('(params) -> ret', remaining docstring) from an embedded signature."""
    first, rest = _split_doc(obj)
    if not first.startswith(name + "("):
        raise StubError(f"{name}: no embedded signature (is embedsignature.format=python set?)")
    return first[len(name) :], rest


def _property(name: str, obj: object) -> tuple[str, str]:
    first, rest = _split_doc(obj)
    if not first.startswith(name + ": "):
        raise StubError(f"property {name}: no embedded type")
    return first[len(name) + 2 :], rest


class _Writer:
    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.lines: list[str] = []
        self.used: set[str] = set()

    def note(self, text: str) -> None:
        self.used.update(_IDENT.findall(text))

    def function(self, name: str, obj: object, indent: str = "") -> None:
        sig, doc = _signature(name, obj)
        self.note(sig)
        if doc:
            self.lines.append(f"{indent}def {name}{sig}:")
            self.lines += _docstring(doc, indent + "    ")
        else:
            self.lines.append(f"{indent}def {name}{sig}: ...")

    def cls(self, name: str, cls: type) -> None:
        self.lines.append(f"class {name}:")
        _, doc = _split_doc(cls)
        self.lines += _docstring(inspect.getdoc(cls) or "", "    ")
        members = 0
        for attr, value in vars(cls).items():
            if attr == "__init__":
                if not (inspect.getdoc(value) or "").startswith("__init__("):
                    raise StubError(f"{name}.__init__ has no embedded signature (give it a docstring)")
                self.function("__init__", value, "    ")
                members += 1
            elif attr.startswith("_"):
                continue
            elif inspect.isgetsetdescriptor(value) or isinstance(value, property):
                typ, pdoc = _property(attr, value)
                self.note(typ)
                self.lines.append("    @property")
                if pdoc:
                    self.lines.append(f"    def {attr}(self) -> {typ}:")
                    self.lines += _docstring(pdoc, "        ")
                else:
                    self.lines.append(f"    def {attr}(self) -> {typ}: ...")
                members += 1
            elif callable(value):
                self.function(attr, value, "    ")
                members += 1
        if not members and not doc:
            self.lines.append("    ...")

    def imports(self) -> list[str]:
        by_module: dict[str, set[str]] = {}
        for ident in sorted(self.used):
            if ident in _SKIP or keyword.iskeyword(ident) or hasattr(builtins, ident):
                continue
            obj = getattr(self.module, ident, None)
            if obj is None:
                continue
            origin = getattr(obj, "__module__", None)
            if origin is None or origin == self.module.__name__:
                continue
            by_module.setdefault(origin, set()).add(ident)
        return [f"from {mod} import {', '.join(sorted(names))}" for mod, names in sorted(by_module.items())]


def generate(module_name: str) -> str:
    module = importlib.import_module(module_name)
    w = _Writer(module)
    for name, obj in vars(module).items():
        if name.startswith("_") or getattr(obj, "__module__", None) != module_name:
            continue
        w.lines.append("")
        if isinstance(obj, type):
            w.cls(name, obj)
        elif callable(obj):
            w.function(name, obj)
    header = [f'"""Stub for the compiled module {module_name} (generated by native/stubgen.py; do not edit)."""', ""]
    imports = w.imports()
    text = "\n".join([*header, *imports, *([""] if imports else []), *w.lines]) + "\n"
    return _format(text)


def _format(text: str) -> str:
    ruff = shutil.which("ruff")
    cmd = [ruff] if ruff else [sys.executable, "-m", "ruff"]
    try:
        done = subprocess.run(
            [*cmd, "format", "--stdin-filename", "stub.pyi", "-"],
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            cwd=ROOT,
        )
    except OSError, subprocess.CalledProcessError:
        return text
    return done.stdout


def stub_path(module_name: str) -> Path:
    return ROOT.joinpath(*module_name.split(".")).with_suffix(".pyi")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if a committed stub is out of date")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    stale: list[str] = []
    for name in MODULES:
        text = generate(name)
        path = stub_path(name)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"{path.relative_to(ROOT)}")
    if stale:
        raise SystemExit("stubs out of date (run native/build.py or native/stubgen.py):\n  " + "\n  ".join(stale))


if __name__ == "__main__":
    main()
