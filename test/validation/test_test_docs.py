"""Tests for deterministic generated test-suite documentation."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "dev" / "bin" / "generate-test-docs"

# Mirrors `generate-test-docs`'s own `_GROUPS` tuple -- kept as a
# separate, independently-written list (not imported from the
# generator) so this test can actually catch the generator's own
# `_GROUPS` silently drifting out of sync with the real directories
# under `test/`, the same purpose `test_api_docs.py`'s sibling test
# serves for `src/fim/`.
_GROUPS: tuple[str, ...] = (
    "",
    "cli",
    "convergence",
    "engine",
    "gui",
    "model",
    "persistence",
    "statistics",
    "validation",
    "viz",
)


def test_generator_documents_every_test_module(tmp_path: Path) -> None:
    """Every committed test module receives its own anchor in `TESTS.md`."""
    output = tmp_path / "TESTS.md"

    result = subprocess.run(
        [str(GENERATOR), str(output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    test_root = PROJECT_ROOT / "test"
    for group in _GROUPS:
        label = group or "test"
        directory = test_root / group if group else test_root
        for path in sorted(directory.glob("*.py")):
            if path.name == "__init__.py":
                continue
            qualified = f"{label}.{path.stem}"
            assert f'<a id="{qualified}"></a>' in rendered, qualified


def test_every_test_directory_is_a_documented_group() -> None:
    """`_GROUPS` here (and the generator's own) covers every real
    subdirectory of `test/` that actually holds `.py` files, other than
    `test/data/` (fixture JSON, not code)."""
    test_root = PROJECT_ROOT / "test"
    actual = {
        path.name
        for path in test_root.iterdir()
        if path.is_dir()
        and path.name not in {"data", "__pycache__"}
        and any(path.glob("*.py"))
    }
    documented = {group for group in _GROUPS if group}
    assert actual == documented
