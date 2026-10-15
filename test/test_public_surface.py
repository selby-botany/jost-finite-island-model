"""Keep the public surface of `fim` explicit and the module boundaries clean.

The public API redesign (`20261014-claude-sonnet-5-5-public-api-redesign-
design.md`, `selby/restricted`) defines the public surface as the names
listed in each public module's `__all__`. These tests make that rule
mechanical: a public module must declare `__all__`, every name in it must
import, and no module may import another module's underscore-prefixed name.

The underscore rule starts with a baseline of the imports that already
existed. The baseline may only shrink: a new offender fails the first test,
and a baseline entry that no longer occurs fails the second, so removing an
offender forces its entry out of the list.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"

PUBLIC_MODULES = (
    "fim",
    "fim.convergence",
    "fim.engine",
    "fim.model",
    "fim.persistence",
    "fim.statistics",
    "fim.viz",
)
"""Modules whose `__all__` defines supported names; grows as the redesign adds
`fim.engine`, `fim.spec`, and the facade."""

PrivateImport = tuple[str, str, str]
"""(importing module, imported module, underscore-prefixed name)."""

PRIVATE_IMPORT_BASELINE: frozenset[PrivateImport] = frozenset()
"""Underscore imports across modules that are tolerated. The redesign started
with 26 and this is now empty; an entry may be added only with a written reason."""


def _module_name(path: Path) -> str:
    """Return the dotted module name of a source file under `src/`.

    Args:
        path: A `.py` file below `SOURCE_ROOT`.

    Returns:
        The importable name, with a package's `__init__` named as the package.
    """
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _private_imports() -> frozenset[PrivateImport]:
    """Scan `src/` for `from fim... import _name` statements.

    Dunder names (`__version__`) are public by convention and are skipped.

    Returns:
        Every (importing module, imported module, name) found.
    """
    found: set[PrivateImport] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        importer = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or node.level != 0:
                continue
            module = node.module or ""
            if module != "fim" and not module.startswith("fim."):
                continue
            for alias in node.names:
                name = alias.name
                if name.startswith("_") and not name.endswith("__"):
                    found.add((importer, module, name))
    return frozenset(found)


@pytest.mark.parametrize("module_name", PUBLIC_MODULES)
def test_public_module_declares_importable_all(module_name: str) -> None:
    """A public module lists its supported names, and each name imports."""
    module = importlib.import_module(module_name)
    names = getattr(module, "__all__", None)
    assert names is not None, f"{module_name} must declare __all__"
    assert len(names) == len(set(names)), f"{module_name}.__all__ repeats a name"
    missing = [name for name in names if not hasattr(module, name)]
    assert not missing, f"{module_name}.__all__ names missing attributes: {missing}"


def test_no_new_private_imports_across_modules() -> None:
    """A module must not import another module's underscore-prefixed name."""
    new = _private_imports() - PRIVATE_IMPORT_BASELINE
    assert not new, (
        "promote these names (drop the underscore) or stop importing them: "
        f"{sorted(new)}"
    )


def test_private_import_baseline_has_no_stale_entries() -> None:
    """A fixed offender must leave the baseline, so the list only shrinks."""
    stale = PRIVATE_IMPORT_BASELINE - _private_imports()
    assert not stale, f"remove these fixed entries from the baseline: {sorted(stale)}"


def test_package_ships_a_py_typed_marker() -> None:
    """A researcher's type checker must treat `fim` as typed (PEP 561).

    Hatch's wheel target packages every file under `src/fim`, so the marker
    ships whenever it exists in the source tree.
    """
    assert (SOURCE_ROOT / "fim" / "py.typed").is_file()
