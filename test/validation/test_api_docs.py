"""Tests for deterministic generated API documentation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest
from latex2mathml.converter import convert
from markdown_it import MarkdownIt

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = PROJECT_ROOT / "dev" / "bin" / "generate-api-docs"

# A minimal, `.venv*`-free `PATH` -- no activated virtualenv, and none of
# this repository's own `bin/` wrappers pre-added by a caller (`pytest`
# itself always runs from inside an activated virtualenv, so this is the
# only way to actually exercise the "plain, unactivated shell" case a
# background agent hits when it forgets, or targets the wrong worktree's
# own `.venv-312`, entirely from within the test suite).
_UNACTIVATED_PATH = "/usr/bin:/bin"


def _repository_managed_venv_exists() -> bool:
    """Whether `bin/python3`'s own "prefer repository-managed
    environments" glob (`.venv`, `.venv-*`) has anything to find here.

    True on every local development checkout (`.venv-312`, by this
    project's own convention). False in CI (confirmed live,
    `34718735434`): CI installs directly into the runner's own
    already-provisioned Python rather than creating a project-local
    virtualenv at all, so there is no "unactivated shell forgot to
    activate a venv" scenario to reproduce there in the first place --
    every ambient Python on a CI runner already has every package this
    project needs, on every `PATH`, which is exactly what makes
    `test_generator_runs_without_an_activated_virtualenv` unable to
    observe the real, local-only failure mode it is named for: with
    `_UNACTIVATED_PATH` and no `.venv*` for `bin/python3` to prefer
    instead, the fallback loop finds a genuinely bare `/usr/bin/python3`
    with none of this project's own dependencies installed (confirmed
    directly -- the same `ModuleNotFoundError: No module named
    'pydoc_markdown'` CI hit, reproduced locally against a bare
    `/usr/bin/python3` under this exact stripped `PATH`), which is a
    fact about the CI runner's own install strategy, not a regression
    in `bin/python3` or either generator.
    """
    return (PROJECT_ROOT / ".venv").exists() or any(PROJECT_ROOT.glob(".venv-*"))


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


def test_gui_settings_results_do_not_parse_json_as_return_fields(
    tmp_path: Path,
) -> None:
    """Dictionary examples in bridge returns stay readable in generated Markdown."""
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
    methods = (
        "set_dark_mode_override",
        "set_default_run_settings",
        "get_results_location",
        "set_results_location",
        "browse_for_results_location",
    )
    for method in methods:
        start = rendered.index(f'<a id="fim.gui.app.Api.{method}"></a>')
        end = rendered.find("\n<a id=", start + 1)
        section = rendered[start:] if end < 0 else rendered[start:end]
        assert '- ``{"' not in section, method
        assert '- `"message"` -' not in section, method


def test_generator_preserves_tex_equations(tmp_path: Path) -> None:
    """Equations remain math fences, not code blocks, in the generated reference."""
    output = tmp_path / "API.md"
    result = subprocess.run(
        [str(GENERATOR), str(output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    tokens = MarkdownIt().parse(output.read_text(encoding="utf-8"))
    equations = [
        token.content
        for token in tokens
        if token.type == "fence" and token.info == "math"
    ]
    for expected in (
        r"k = \max",
        r"\tau = \frac{1}{2\mu + 1/T}",
        r"\mathrm{within}^\prime",
        r"x_t = x^* + V \operatorname{diag}",
        r"\nu &= \frac{\mathrm{SE}^4}",
        r"P_{ia} &= \frac{G_a - N_i p_{ia}}{W_i}",
    ):
        assert any(expected in equation for equation in equations), expected
    for equation in equations:
        root = ElementTree.fromstring(convert(equation, display="block"))
        assert root.tag == "{http://www.w3.org/1998/Math/MathML}math"
        assert not any("\\" in text for text in root.itertext()), equation
    for token in tokens:
        if token.type == "code_block":
            assert "within'  =" not in token.content
            assert "standard error = sqrt" not in token.content


def test_generator_runs_without_an_activated_virtualenv(tmp_path: Path) -> None:
    """The generator resolves its own virtualenv from the repository's own
    path, with no dependency on the caller's shell having activated one
    first -- the exact recurring failure mode (a background agent
    forgetting, or activating the wrong worktree's own `.venv-312`) this
    generator's `bin/pydoc-markdown` indirection exists to eliminate.

    Compared against a normal, ordinary-environment run rather than the
    committed `src/fim/API.md` -- freshness of the committed file against
    the real source tree is `pre-push`'s own job (and
    `test_generator_documents_every_source_module`, above, already covers
    "every module gets documented"); this test's own job is narrower:
    confirm the two environments produce identical output.
    """
    if not _repository_managed_venv_exists():
        pytest.skip(
            "no repository-managed virtualenv (.venv/.venv-*) present -- "
            "this environment installs packages directly into its own "
            "already-provisioned Python (see _repository_managed_venv_exists's "
            "own docstring), so bin/python3's venv-preference fallback has "
            "nothing to find here regardless of PATH; the invariant this "
            "test checks does not apply"
        )
    unactivated_output = tmp_path / "unactivated" / "API.md"
    ordinary_output = tmp_path / "ordinary" / "API.md"

    unactivated = subprocess.run(
        [str(GENERATOR), str(unactivated_output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": _UNACTIVATED_PATH},
    )
    ordinary = subprocess.run(
        [str(GENERATOR), str(ordinary_output)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert unactivated.returncode == 0, unactivated.stderr
    assert ordinary.returncode == 0, ordinary.stderr
    assert unactivated_output.read_text(encoding="utf-8") == ordinary_output.read_text(
        encoding="utf-8"
    )
