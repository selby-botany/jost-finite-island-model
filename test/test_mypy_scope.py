"""Static checks that the mypy gate's config and invocations stay in sync.

No container or subprocess mypy run: these assert directly against
`pyproject.toml`, `build`, and `dev/git-hooks/pre-push` source text, so a
scope regression is caught in milliseconds rather than by noticing the
type gate quietly stopped checking `test/`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Matches a real mypy invocation ("mypy", "mypy --strict", "python -m mypy",
# …) followed by a positional path argument, the specific mistake this
# module guards against. Deliberately does not match bare `mypy` (with only
# flags, or nothing, after it) or unrelated prose mentioning the word.
_MYPY_WITH_POSITIONAL_ARGUMENT = re.compile(
    r"\bmypy\b(?:\s+--[\w-]+(?:=\S+)?)*\s+\S*(?:src|test)\b"
)


def _mypy_config() -> dict[str, object]:
    """Return `[tool.mypy]` from the repository's `pyproject.toml`."""
    payload = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return payload["tool"]["mypy"]  # type: ignore[no-any-return]


def test_mypy_config_declares_files_without_a_conflicting_packages_key() -> None:
    """`files` and `packages` together make every bare `mypy` invocation fail.

    Regression test for R6: mypy accepts at most one of `files`,
    `packages`, `-m`/`-p`, or positional file arguments. `[tool.mypy]`
    once declared both `files = ["src", "test"]` and `packages =
    ["fim"]`, so a bare `mypy` (no CLI arguments) failed immediately with
    "May only specify one of: module/package, files, or command" —
    before checking a single file. `build` and `dev/git-hooks/pre-push`
    both sidestepped the crash by always passing an explicit `src`
    argument, which happens to override a conflicting config instead of
    erroring — masking the conflict rather than fixing it, and silently
    narrowing the checked scope away from `test/` in the process (see
    `test_build_and_pre_push_never_narrow_mypys_scope` below).
    """
    config = _mypy_config()
    assert config.get("files") == ["src", "test"]
    assert "packages" not in config


def test_build_and_pre_push_never_narrow_mypys_scope() -> None:
    """Neither script passes mypy a positional path that overrides the config.

    Regression test for R6: `build` and `dev/git-hooks/pre-push` used to
    invoke `mypy --strict src`, an explicit scope argument that silently
    overrides `[tool.mypy]`'s own `files` setting — so the type gate
    actually run in CI and at push time never covered `test/` at all,
    regardless of what the config said. Both must invoke mypy with no
    positional argument, so `[tool.mypy]` stays the single source of
    truth for what gets checked and the two cannot drift apart again.
    """
    for relative_path in ("build", "dev/git-hooks/pre-push"):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        match = _MYPY_WITH_POSITIONAL_ARGUMENT.search(text)
        assert match is None, (
            f"{relative_path} passes mypy an explicit scope argument, "
            f"narrowing it away from [tool.mypy]'s files: {match!r}"
        )
