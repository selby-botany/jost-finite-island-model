"""The constants scan: policy constants live in `fim.config`, nowhere else.

`fim.config` holds every named policy constant and numerical guard of the
convergence and statistics code (see its `README.md`). These tests read the
source, not the running program, so they cost milliseconds and need nothing
installed. They fail when a new module-level numeric constant appears in the
scanned modules outside `fim/config/`, when a constant in `fim/config/`
lacks a docstring with a `Kind:` line, and when the README's table drifts
from the modules.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "src" / "fim"
CONFIG = SOURCE / "config"
KINDS = ("derivable", "numerical guard", "policy")

SCANNED = (
    *sorted((SOURCE / "convergence").glob("*.py")),
    *sorted((SOURCE / "statistics").glob("*.py")),
    SOURCE / "model" / "params.py",
    SOURCE / "engine.py",
    SOURCE / "gui" / "animation.py",
)

ALLOWED: dict[str, str] = {
    "AUTO_CONVERGENCE": "a sentinel for 'derive it', not a tunable value",
    "_MINIMUM_SIGMA_BAND_WINDOW": "retired with the sigma band",
    "_SIGMA_BAND_FIELD_COUNT": "retired with the sigma band",
}
"""Module-level numeric constants allowed outside `fim/config/`, with why."""


def _is_numeric(node: ast.expr) -> bool:
    """Whether `node` is a number, or arithmetic on numbers only."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int | float) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return _is_numeric(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_numeric(node.left) and _is_numeric(node.right)
    return False


def _assigned(tree: ast.Module) -> list[tuple[str, ast.stmt, ast.expr]]:
    """Module-level `NAME = value` and `NAME: T = value` statements."""
    found: list[tuple[str, ast.stmt, ast.expr]] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            found.append((node.targets[0].id, node, node.value))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            found.append((node.target.id, node, node.value))
    return found


def _config_constants() -> dict[str, tuple[str, str | None]]:
    """Every constant in `fim/config/`: name -> (module, its docstring)."""
    found: dict[str, tuple[str, str | None]] = {}
    for path in sorted(CONFIG.glob("*.py")):
        if path.name == "__init__.py":
            continue
        body = ast.parse(path.read_text(encoding="utf-8")).body
        for index, node in enumerate(body):
            if not isinstance(node, ast.AnnAssign) or not isinstance(
                node.target, ast.Name
            ):
                continue
            following = body[index + 1] if index + 1 < len(body) else None
            doc = (
                following.value.value
                if isinstance(following, ast.Expr)
                and isinstance(following.value, ast.Constant)
                and isinstance(following.value.value, str)
                else None
            )
            found[node.target.id] = (path.stem, doc)
    return found


def test_no_numeric_constant_is_defined_outside_the_config_package() -> None:
    """A new magic number goes into `fim/config/`, not into the module using it."""
    stray = sorted(
        f"{path.relative_to(SOURCE)}: {name}"
        for path in SCANNED
        for name, _node, value in _assigned(ast.parse(path.read_text(encoding="utf-8")))
        if _is_numeric(value) and name not in ALLOWED
    )
    assert not stray, (
        "move these constants into fim/config/ (see its README.md), or add "
        f"them to ALLOWED with a reason: {stray}"
    )


def test_every_allow_list_entry_still_exists() -> None:
    """A retired constant leaves the allow list with it."""
    present = {
        name
        for path in SCANNED
        for name, _node, value in _assigned(ast.parse(path.read_text(encoding="utf-8")))
        if _is_numeric(value)
    }
    assert sorted(set(ALLOWED) - present) == []


def test_every_config_constant_says_what_it_is_and_which_kind() -> None:
    """Each constant has a docstring ending in a `Kind:` line with a known kind."""
    problems = []
    for name, (module, doc) in _config_constants().items():
        if not doc:
            problems.append(f"{module}.{name}: no docstring")
            continue
        kind = re.search(r"^Kind: (.*?)\.?$", doc, re.MULTILINE)
        if kind is None or not kind.group(1).lower().startswith(KINDS):
            problems.append(f"{module}.{name}: no valid Kind line")
    assert problems == []


def test_the_readme_table_lists_exactly_the_config_constants() -> None:
    """The README names every constant once, and nothing that is gone."""
    readme = (CONFIG / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \| `(\w+)\.py` \|", readme, re.M)
    names = [name for name, _module in listed]
    assert sorted(names) == sorted(_config_constants())
    assert len(names) == len(set(names))
    for name, module in listed:
        assert _config_constants()[name][0] == module


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(SOURCE)))
def test_scanned_modules_exist(path: Path) -> None:
    """A renamed module must not silently drop out of the scan."""
    assert path.is_file()
