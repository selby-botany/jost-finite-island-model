"""Behavioral tests for repository-managed Git hooks."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOOKS = PROJECT_ROOT / "dev" / "git-hooks"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one Git command in a fixture repository."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _initialize_repo(path: Path, *, pyproject: bool = True) -> None:
    """Create a configured fixture Git repository."""
    _git(path, "init", "--quiet")
    _git(path, "config", "user.email", "tests@example.invalid")
    _git(path, "config", "user.name", "FIM tests")
    if pyproject:
        (path / "pyproject.toml").write_text(
            '[tool.ruff]\ntarget-version = "py312"\n',
            encoding="utf-8",
        )


def _run_hook(
    repo: Path,
    name: str,
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one project hook against a fixture repository."""
    return subprocess.run(
        [str(HOOKS / name), *args],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _tool_path(directory: Path, *names: str) -> str:
    """Create an isolated PATH containing symlinks to selected host tools."""
    directory.mkdir()
    for name in names:
        executable = (
            str(Path(sys.executable).resolve())
            if name == "python3"
            else shutil.which(name)
        )
        assert executable is not None
        (directory / name).symlink_to(executable)
    return str(directory)


@pytest.mark.parametrize(
    "subject",
    [
        "feat(model): add drift",
        "feat!: change the public API",
        "Merge branch 'dev'",
        'Revert "feat(model): add drift"',
        "fixup! feat(model): add drift",
        "squash! docs: update usage",
    ],
)
def test_commit_msg_accepts_supported_subjects(
    tmp_path: Path,
    subject: str,
) -> None:
    """Documented Conventional Commit and Git-generated forms pass."""
    message = tmp_path / "message"
    message.write_text(f"# comment\n{subject}\n", encoding="utf-8")

    result = _run_hook(tmp_path, "commit-msg", str(message))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "subject",
    ["", "Update docs", "Feat: uppercase type", "feat:no separator space"],
)
def test_commit_msg_rejects_malformed_subjects(
    tmp_path: Path,
    subject: str,
) -> None:
    """Malformed commit subjects fail with the expected format."""
    message = tmp_path / "message"
    message.write_text(f"# comment\n{subject}\n", encoding="utf-8")

    result = _run_hook(tmp_path, "commit-msg", str(message))

    assert result.returncode == 1
    assert "Expected: type(scope): summary" in result.stderr


def test_pre_commit_formats_and_restages_python_with_spaces(
    tmp_path: Path,
) -> None:
    """Staged Python is formatted and re-staged without splitting paths."""
    _initialize_repo(tmp_path)
    source = tmp_path / "messy file.py"
    source.write_text("def answer( ):\n return 42\n", encoding="utf-8")
    _git(tmp_path, "add", "pyproject.toml", source.name)
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
    }

    result = _run_hook(tmp_path, "pre-commit", env=env)

    assert result.returncode == 0, result.stderr
    staged = _git(tmp_path, "show", f":{source.name}").stdout
    assert staged == "def answer():\n    return 42\n"


def test_pre_commit_refreshes_api_only_for_staged_python(
    tmp_path: Path,
) -> None:
    """API generation runs for Python changes but not docs-only changes."""
    _initialize_repo(tmp_path)
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("# Initial\n", encoding="utf-8")
    api = tmp_path / "src" / "fim" / "API.md"
    api.parent.mkdir(parents=True)
    api.write_text("old\n", encoding="utf-8")
    generator = tmp_path / "dev" / "bin" / "generate-api-docs"
    generator.parent.mkdir(parents=True)
    generator.write_text(
        "#!/usr/bin/env bash\nprintf 'generated\\n' > src/fim/API.md\n",
        encoding="utf-8",
    )
    generator.chmod(0o755)
    tools = tmp_path / "tools"
    path = _tool_path(tools, "bash", "git", "grep", "python3")
    for name in ("pydoc-markdown", "ruff"):
        tool = tools / name
        tool.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
    env = {**os.environ, "PATH": path}

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "--quiet", "-m", "test: create fixture")
    source.write_text("VALUE = 2\n", encoding="utf-8")
    _git(tmp_path, "add", source.name)

    python_result = _run_hook(tmp_path, "pre-commit", env=env)

    assert python_result.returncode == 0, python_result.stderr
    assert _git(tmp_path, "show", ":src/fim/API.md").stdout == "generated\n"

    _git(tmp_path, "commit", "--quiet", "-m", "test: stage generated API")
    readme.write_text("# Documentation only\n", encoding="utf-8")
    _git(tmp_path, "add", readme.name)
    docs_result = _run_hook(tmp_path, "pre-commit", env=env)

    assert docs_result.returncode == 0, docs_result.stderr
    staged_names = _git(tmp_path, "diff", "--cached", "--name-only").stdout
    assert staged_names == "README.md\n"


def test_pre_commit_rejects_new_non_ascii_filename(tmp_path: Path) -> None:
    """A newly added filename outside ASCII fails before commit."""
    _initialize_repo(tmp_path)
    filename = "caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt"
    (tmp_path / filename).write_text("content\n", encoding="utf-8")
    _git(tmp_path, "add", "pyproject.toml", filename)

    result = _run_hook(tmp_path, "pre-commit")

    assert result.returncode == 1
    assert "newly added filenames must be ASCII" in result.stderr


def test_pre_commit_skips_unavailable_python_tools(tmp_path: Path) -> None:
    """Missing developer tools produce diagnostics without blocking a commit."""
    _initialize_repo(tmp_path)
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "pyproject.toml", source.name)
    tools = tmp_path / "tools"
    path = _tool_path(tools, "bash", "git", "python3")

    result = _run_hook(
        tmp_path,
        "pre-commit",
        env={**os.environ, "PATH": path},
    )

    assert result.returncode == 0, result.stderr
    assert "ruff absent; formatting skipped" in result.stdout
    assert "API generator unavailable; refresh skipped" in result.stdout


@pytest.mark.parametrize("name", ["pre-commit", "pre-push"])
def test_python_hooks_skip_when_pyproject_is_absent(
    tmp_path: Path,
    name: str,
) -> None:
    """A pre-Python fixture repository is not blocked by either hook."""
    _initialize_repo(tmp_path, pyproject=False)

    result = _run_hook(tmp_path, name)

    assert result.returncode == 0, result.stderr
    assert "pyproject.toml absent" in result.stdout


def test_pre_push_skips_unavailable_python_tools(tmp_path: Path) -> None:
    """Missing local gates are reported without blocking a push."""
    _initialize_repo(tmp_path)
    tools = tmp_path / "tools"
    # "mkdir"/"rm"/"cat": the lock's own always-on prerequisites, not one
    # of the optional project tools this test means to leave absent.
    path = _tool_path(tools, "bash", "git", "mkdir", "rm", "cat")

    result = _run_hook(
        tmp_path,
        "pre-push",
        env={**os.environ, "PATH": path},
    )

    assert result.returncode == 0, result.stderr
    assert "ruff absent; lint skipped" in result.stdout
    assert "mypy absent; types skipped" in result.stdout
    assert "pytest absent; tests skipped" in result.stdout
    assert "API generator unavailable; docs skipped" in result.stdout


def test_pre_push_detects_stale_generated_api(tmp_path: Path) -> None:
    """The pre-push docs gate fails until generated API content is current."""
    _initialize_repo(tmp_path)
    api = tmp_path / "src" / "fim" / "API.md"
    api.parent.mkdir(parents=True)
    api.write_text("stale\n", encoding="utf-8")
    generator = tmp_path / "dev" / "bin" / "generate-api-docs"
    generator.parent.mkdir(parents=True)
    generator.write_text(
        "#!/usr/bin/env bash\nprintf 'current\\n' > \"$1\"\n",
        encoding="utf-8",
    )
    generator.chmod(0o755)
    tools = tmp_path / "tools"
    path = _tool_path(tools, "bash", "diff", "git", "mktemp", "rm", "mkdir", "cat")
    pydoc = tools / "pydoc-markdown"
    pydoc.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    pydoc.chmod(0o755)
    env = {
        **os.environ,
        "PATH": path,
        "PRE_PUSH_SKIP_LINT": "true",
        "PRE_PUSH_SKIP_TEST": "true",
        "PRE_PUSH_SKIP_TYPE": "true",
    }

    stale = _run_hook(tmp_path, "pre-push", env=env)

    assert stale.returncode == 1
    api.write_text("current\n", encoding="utf-8")

    current = _run_hook(tmp_path, "pre-push", env=env)

    assert current.returncode == 0, current.stderr


def _lock_dir_for(repo: Path) -> Path:
    """Return the same per-repository lock directory `pre-push` computes.

    Mirrors the hook's own `"${TMPDIR:-/tmp}/fim-pre-push-${root//\\//_}.lock"`
    exactly, keyed off `git rev-parse --show-toplevel` (not `repo` itself,
    which can differ by a resolved symlink component on macOS) so a test
    that pre-seeds or inspects the lock always agrees with the hook on
    where it lives.
    """
    root = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    tmpdir = os.environ.get("TMPDIR") or "/tmp"
    return Path(tmpdir) / f"fim-pre-push-{root.replace('/', '_')}.lock"


def test_pre_push_refuses_a_second_concurrent_run(tmp_path: Path) -> None:
    """A lock held by a still-running process blocks a second push outright.

    Real, reported behavior, not a hypothetical one: three concurrent
    full `pytest` runs against this same checkout reproduced two genuine
    test failures neither run has alone (`assert settled is True` and a
    real batch `"error"` in place of `"done"`) -- two processes both
    opening real, hidden `pywebview` GUI windows race for the same
    exclusive display resources and can starve each other past a test's
    own fixed settle timeout. The lock turns that silent, sometimes-
    flaky contention into an immediate, clear refusal instead.
    """
    _initialize_repo(tmp_path)
    lock_dir = _lock_dir_for(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        lock_dir.mkdir(parents=True)
        (lock_dir / "pid").write_text(str(holder.pid), encoding="utf-8")

        result = _run_hook(tmp_path, "pre-push")

        assert result.returncode == 1
        assert "already in progress" in result.stderr
        assert str(holder.pid) in result.stderr
        # Refused before running anything -- never got as far as reporting
        # a missing tool, let alone actually invoking ruff/mypy/pytest.
        assert "skipped" not in result.stdout
    finally:
        holder.terminate()
        holder.wait(timeout=5)
        shutil.rmtree(lock_dir, ignore_errors=True)


def test_pre_push_reclaims_a_stale_lock(tmp_path: Path) -> None:
    """A lock left by a process that is no longer running does not block a push."""
    _initialize_repo(tmp_path)
    lock_dir = _lock_dir_for(tmp_path)
    dead = subprocess.Popen(["true"])
    dead.wait(timeout=5)
    tools = tmp_path / "tools"
    path = _tool_path(tools, "bash", "git", "mkdir", "rm", "cat")
    lock_dir.mkdir(parents=True)
    (lock_dir / "pid").write_text(str(dead.pid), encoding="utf-8")

    result = _run_hook(tmp_path, "pre-push", env={**os.environ, "PATH": path})

    assert result.returncode == 0, result.stderr
    assert "reclaiming a stale lock" in result.stdout
    assert "pytest absent; tests skipped" in result.stdout
    # The hook's own `trap ... EXIT` releases the reclaimed lock too --
    # nothing left behind for the next push to trip over.
    assert not lock_dir.exists()


def test_installer_links_all_repository_hooks(tmp_path: Path) -> None:
    """The installer links each documented hook into a fixture repository."""
    _initialize_repo(tmp_path, pyproject=False)
    fixture_hooks = tmp_path / "dev" / "git-hooks"
    shutil.copytree(HOOKS, fixture_hooks)

    result = subprocess.run(
        [str(fixture_hooks / "install")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    for name in ("commit-msg", "pre-commit", "pre-push"):
        installed = tmp_path / ".git" / "hooks" / name
        assert installed.is_symlink()
        assert installed.readlink() == Path("../../dev/git-hooks") / name
