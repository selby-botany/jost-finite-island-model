"""Static checks that every declared console script resolves to a callable.

No subprocess or installed-package round trip: this parses
`[project.scripts]` directly out of `pyproject.toml` and imports each
target, so a typo in the `module:attribute` string (the class of mistake
an installed-package smoke test would also catch, only much later and
only in CI) is caught in milliseconds against the source tree itself.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _console_scripts() -> dict[str, str]:
    """Return `[project.scripts]` from the repository's `pyproject.toml`."""
    payload = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return payload["project"]["scripts"]  # type: ignore[no-any-return]


def test_every_console_script_target_is_a_real_callable() -> None:
    """Each `module:attribute` entry imports and resolves to a callable."""
    scripts = _console_scripts()
    assert scripts, "pyproject.toml declares no [project.scripts] entries"
    for name, target in scripts.items():
        module_name, _, attribute = target.partition(":")
        module = importlib.import_module(module_name)
        entry_point = getattr(module, attribute)
        assert callable(entry_point), f"{name} -> {target} is not callable"


def test_fim_gui_console_script_points_at_the_gui_app_main() -> None:
    """`fim-gui` launches `fim.gui.app.main`, independent of `fim`'s dispatch."""
    assert _console_scripts()["fim-gui"] == "fim.gui.app:main"
