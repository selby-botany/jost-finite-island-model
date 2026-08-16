"""Tests for deterministic generated API documentation."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "dev" / "bin" / "generate-api-docs"


def test_generator_documents_every_source_module(tmp_path: Path) -> None:
    """Every committed Python module receives an API section."""
    output = tmp_path / "API.md"

    result = subprocess.run(
        [str(GENERATOR), str(output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    source_root = PROJECT_ROOT / "src"
    for path in sorted((source_root / "fim").rglob("*.py")):
        module = ".".join(path.relative_to(source_root).with_suffix("").parts)
        module = module.removesuffix(".__init__")
        assert f'<a id="{module}"></a>' in rendered, module
