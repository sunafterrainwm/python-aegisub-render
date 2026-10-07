"""The committed .pyi stubs match what native/stubgen.py generates from the committed .pyd files."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _stubgen():
    spec = importlib.util.spec_from_file_location("stubgen", ROOT / "native" / "stubgen.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["stubgen"] = module
    spec.loader.exec_module(module)
    return module


STUBGEN = _stubgen()


@pytest.mark.parametrize("module", STUBGEN.MODULES)
def test_stub_is_current(module: str) -> None:
    path = STUBGEN.stub_path(module)
    assert path.read_text(encoding="utf-8") == STUBGEN.generate(module), (
        f"{path.relative_to(ROOT)} is out of date; run native/stubgen.py"
    )
