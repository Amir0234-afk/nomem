"""The README is the PyPI long description — its examples must actually work.

Twice now a hand-written expected output in a doc example has been wrong (the
`examples/quickstart.py` supersession bug, then the README's `# Kira, Lisbon`).
Both were the same defect: prose asserting behavior nobody executed. These tests
execute it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"


def _python_blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)


def test_readme_has_python_examples() -> None:
    assert _python_blocks(), "no ```python blocks found — did the README format change?"


def test_readme_examples_are_syntactically_valid() -> None:
    """Cheap, hermetic guard: every snippet must at least parse."""
    for i, block in enumerate(_python_blocks()):
        try:
            compile(block, f"<README block {i}>", "exec")
        except SyntaxError as exc:  # pragma: no cover - only on a broken README
            pytest.fail(f"README python block {i} is not valid Python: {exc}\n\n{block}")


@pytest.mark.live
def test_readme_quickstart_runs(tmp_path: Path) -> None:
    """Execute the README's main example end to end against a real Ollama.

    Asserts only that it *runs* — never its output, which depends on the
    extraction model. That is exactly the mistake this file exists to prevent.
    """
    block = _python_blocks()[0]
    assert "MemoryGraph" in block, "expected the first block to be the quickstart"
    script = tmp_path / "readme_example.py"
    script.write_text(block, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"README example failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
