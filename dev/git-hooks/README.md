# Repository-managed Git hooks

Install the version-controlled hooks from the repository root:

```console
bash dev/git-hooks/install
```

The installer creates symlinks in `.git/hooks`, so later hook updates take
effect without reinstalling.

| Hook | Fast local gate |
|---|---|
| `commit-msg` | Conventional Commit subject |
| `pre-commit` | Staged Python format/lint, API refresh, ASCII filenames |
| `pre-push` | Ruff, strict mypy, fast tests, API freshness, Docker-backed repository validation |

Hooks print an informational message and exit successfully when the required
tool or `pyproject.toml` is absent. They are convenience checks; continuous
integration remains authoritative.

The `pre-push` hook also runs [`dev/bin/validate-repository`](../bin/README.md),
which lints Markdown, YAML, shell, and the desktop GUI's JavaScript, CSS, and
HTML, and scans the commit history for leaked credentials. Local is the right
place for it: the pinned Docker images are already warm on your machine, so
the check costs seconds, while the same work on a CI runner starts cold and
only reports back after the push. CI re-runs it on `staging` and `main` as a
backstop, where passing should be a formality.

Set `PRE_PUSH_SKIP_VALIDATE=true` to skip it; it also skips automatically,
with a message, when no Docker daemon is running.

See the [maintainer runbook](../../CONTRIBUTING.md), the
[developer guide](../../doc/developer.md), and the
[project overview](../../README.md).
