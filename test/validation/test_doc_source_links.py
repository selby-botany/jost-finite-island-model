"""Exact source-line links for both generated references, without importing fixtures."""

from __future__ import annotations

import os
import re
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = '''"""Example module."""
VALUE: int = 7
"""Example constant."""

@decorate
class Example:
    """Example class."""
    field: int = 1
    """Example field."""

    @property
    def value(self):
        """Example property."""
        return self.field

    class Nested:
        """Nested class."""
        def method(self):
            """Nested method."""

@decorate
async def test_example():
    """Example test."""
    def local():
        """Local function."""
'''


@pytest.fixture
def linker() -> Callable[[str, Mapping[str, Path], Path], str]:
    """Load the shared linker without executing its CLI."""
    link: Callable[[str, Mapping[str, Path], Path], str] = runpy.run_path(
        str(ROOT / "dev/lib/docsource.py")
    )["link_document"]
    return link


def test_exact_declaration_links(
    tmp_path: Path, linker: Callable[[str, Mapping[str, Path], Path], str]
) -> None:
    """Decorated and nested symbols link to declarations, not decorators."""
    source = tmp_path / "source #1.py"
    source.write_text(SOURCE, encoding="utf-8")
    symbols = {
        "sample": 1,
        "sample.VALUE": 2,
        "sample.Example": 6,
        "sample.Example.field": 8,
        "sample.Example.value": 12,
        "sample.Example.Nested": 16,
        "sample.Example.Nested.method": 18,
        "sample.test_example": 22,
        "sample.test_example.local": 24,
    }
    document = "[Example](#sample.Example)\n\n" + "\n\n".join(
        f'<a id="{name}"></a>\n\n## {name}' for name in symbols
    )
    expected = "[Example](#sample.Example)\n\n" + "\n\n".join(
        f'<a id="{name}"></a>\n\n## [{name}](./source%20%231.py#L{line})'
        for name, line in symbols.items()
    )
    assert linker(document, {"sample": source}, tmp_path / "API.md") == expected
    assert linker(
        document, {"sample": source}, tmp_path / "review" / "API.md"
    ) == expected.replace("./source%20%231.py", "../source%20%231.py")


def test_missing_declaration_is_an_error(
    tmp_path: Path, linker: Callable[[str, Mapping[str, Path], Path], str]
) -> None:
    """A stale/mismatched documented symbol cannot silently lose its source link."""
    source = tmp_path / "sample.py"
    source.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match=r"sample\.missing"):
        linker(
            '<a id="sample.missing"></a>\n\n## missing',
            {"sample": source},
            tmp_path / "API.md",
        )


@pytest.mark.parametrize("generator", ["generate-api-docs", "generate-test-docs"])
@pytest.mark.parametrize("alternate", [False, True])
def test_generators_link_exact_source_lines(
    tmp_path: Path, generator: str, alternate: bool
) -> None:
    """Both real pipelines preserve anchors and rebase links to the actual output."""
    root = tmp_path / "repository"
    scripts = root / "dev/bin"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "dev/bin" / generator, scripts / generator)
    (root / "dev/lib").mkdir()
    shutil.copy2(ROOT / "dev/lib/docsource.py", root / "dev/lib/docsource.py")
    api = generator == "generate-api-docs"
    directory = root / ("src/fim" if api else "test/gui")
    directory.mkdir(parents=True)
    source = directory / ("sample.py" if api else "test_sample.py")
    source.write_text(SOURCE, encoding="utf-8")
    if api:
        (directory / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
    default = root / ("src/fim/API.md" if api else "test/TESTS.md")
    output = root / "review/deep/reference.md" if alternate else default
    command = (
        [str(scripts / generator)]
        if api
        else [sys.executable, str(scripts / generator)]
    )
    if alternate:
        command.append("review/deep/reference.md")
    env = dict(os.environ)
    env["PATH"] = f"{ROOT / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    rendered = output.read_text(encoding="utf-8")
    module = "fim.sample" if api else "gui.test_sample"
    target = (
        ("../../src/fim/sample.py" if api else "../../test/gui/test_sample.py")
        if alternate
        else ("./sample.py" if api else "./gui/test_sample.py")
    )
    for name, title, line in (
        (module, module.replace("_", r"\_"), 1),
        (f"{module}.VALUE", "VALUE", 2),
        (f"{module}.Example", "Example Objects", 6),
        (f"{module}.Example.field", "field", 8),
        (f"{module}.Example.value", "value", 12),
        (f"{module}.Example.Nested", "Nested Objects", 16),
        (f"{module}.Example.Nested.method", "method", 18),
        (f"{module}.test_example", r"test\_example", 22),
    ):
        assert re.search(
            rf'<a id="{re.escape(name)}"></a>\n\n'
            rf"#+ \[{re.escape(title)}\]\({re.escape(target)}#L{line}\)",
            rendered,
        ), name
    assert f"](#{module})" in rendered
    if api:
        package_target = "../../src/fim/__init__.py" if alternate else "./__init__.py"
        assert f"# [fim]({package_target}#L1)" in rendered
    if alternate:
        assert not default.exists()
    rerun = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, check=False
    )
    assert rerun.returncode == 0, rerun.stderr
    assert output.read_text(encoding="utf-8") == rendered
    sidecar = output.with_name(".freshness")
    freshness = subprocess.run(
        [*command[: 1 if api else 2], str(sidecar)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert freshness.returncode == 0, freshness.stderr
    assert sidecar.read_text(encoding="utf-8") == rendered
