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
| `pre-push` | Ruff, strict mypy, fast tests, API freshness |

Hooks print an informational message and exit successfully when the required
tool or `pyproject.toml` is absent. They are convenience checks; continuous
integration remains authoritative.

See the [maintainer runbook](../../CONTRIBUTING.md), the
[developer guide](../../doc/developer.md), and the
[project overview](../../README.md).
