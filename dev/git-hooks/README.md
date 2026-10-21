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
HTML, and scans outgoing commit history for leaked credentials. Existing
remote ancestry is excluded using Git's pre-push ref updates, not a cached
remote-tracking branch. New refs scan their full ancestry; deletions add no
commits. If the remote base is unavailable locally, the hook reports that
fact and scans the full local ancestry instead.

Running `dev/bin/validate-repository` directly still scans every branch and
tag. The dedicated Gitleaks workflow remains enabled in CI. Local checks report
problems before a push and reuse cached, pinned Docker images. CI repeats
repository validation on `staging` and `main` to cover pushes made without
local checks.

Set `PRE_PUSH_SKIP_VALIDATE=true` to skip it; it also skips automatically,
with a message, when no Docker daemon is running.

See the [maintainer runbook](../../CONTRIBUTING.md), the
[developer guide](../../doc/developer.md), and the
[project overview](../../README.md).
