# Maintainer runbook

This is a single-maintainer project built to be inherited. This runbook covers
setup, validation, commits, and releases; it does not assume access to the
original author.

## Setup

Use a Unix-like development system with:

- Bash 3.2 or newer
- Git
- Python 3.12 or newer

The scripts use Bash arrays, `[[ ... ]]`, and `BASH_SOURCE`, so plain POSIX
`sh` is not sufficient. They do not use modern-only Bash features; the Bash
3.2 bundled with macOS works. Docker Engine is required for the complete
repository-file checks and Homebrew formula validation, but not for the Python
build itself. Native Windows shells are not supported. The Windows executable
is built and tested on the release workflow's Windows runner.

```console
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
. include/dot-bashrc
bash dev/git-hooks/install
```

Shell activation is optional. Repository-local wrappers automatically select a
Python 3.12+ `.venv` interpreter when you run the commands below.

Confirm the environment with `bash --version`, `docker version`,
`git --version`, and `python --version` before interpreting tool errors.

## Daily validation

```console
pytest
ruff check src test
ruff format --check src test
mypy
./build
```

`./build --ci` runs the authoritative local equivalent of continuous
integration, including branch coverage, deterministic statistical tests,
documentation freshness, link checking, and package smoke tests.

Repository-level shell, YAML, Markdown, and secret checks use pinned
Docker-backed wrappers stored in `bin/`; they do not depend on another
checkout:

```console
. include/dot-bashrc
dev/bin/validate-repository
```

## Commits

Use Conventional Commits: `type(scope): summary`. Every behavior change lands
with tests and an entry under `[Unreleased]` in `CHANGELOG.md`.

Public functions require typed signatures and docstrings. Regenerate
`src/fim/API.md` in the same commit as public API changes.

## Release

Work promotes through three branches, each with a distinct role — `dev`
itself never packages or publishes anything:

1. **`dev`** — normal development. Every push runs `ci.yml`'s `build`
   job (lint, type-check, the full test suite including the
   `gui`-marked suite under Xvfb). No packaging, no release artifact of
   any kind comes from `dev` directly.
2. **`staging`** — the beta stage. Pushing (or merging) verified `dev`
   work to `staging` triggers `.github/workflows/beta.yml`, a separate
   workflow from `ci.yml`. It builds and smoke-tests all five platform
   executables (`windows-beta-x64`, `windows-beta-arm64`,
   `macos-beta-arm64`, `macos-beta-x64`, `linux-beta-x64`), each
   stamped with a computed `beta-YYYYMMDD.NN` label rather than
   `version.txt`'s own value, and `publish-beta` ships them as a GitHub
   **prerelease** for testers — `workflow_dispatch` triggers the same
   pipeline manually, without a push. A beta build touches none of
   `version.txt`, `CHANGELOG.md`, or the `vX.Y.Z` tag namespace.
3. **`main`** — the real release, once a beta build (or `dev` directly,
   if skipping the beta stage for a given change) is verified:

   1. Run `./build --ci` on the verified commit.
   2. Move `[Unreleased]` changes into a dated `X.Y.Z` section in
      `CHANGELOG.md`.
   3. Set the same version in `version.txt`.
   4. Merge or fast-forward the verified commit to `main`.
   5. Tag `vX.Y.Z` from `main` with an **annotated** tag:
      `git tag -a vX.Y.Z -m "X.Y.Z"`. A bare `git tag vX.Y.Z`
      (lightweight, no message) is rejected by CI's `verify-tag` job
      before anything publishes — see below.
   6. Confirm `ci.yml`'s `windows`, `windows-arm64`, `macos-arm64`,
      `macos-x64`, and `linux-x64` jobs all run and `publish` ships
      every executable (checksummed), the wheel, and the source
      distribution. Every platform job is independent — PyInstaller
      never cross-compiles, so each OS/architecture combination needs
      its own runner — and none can start until every `build` matrix
      leg and `verify-tag` succeed for that exact commit
      (`needs: [build, verify-tag]`); `publish` cannot start until
      every one of them has. `verify-tag` itself rejects a tag that is
      not both annotated and an ancestor of `main`.
   7. Download at least one executable matching hardware you have
      available and independently verify `--version`, `--help`, and
      one offline tiny run.

`publish` rejects a tag that differs from `version.txt`.

### Repository settings (one-time, applied via GitHub, not this repo)

`ci.yml`'s `verify-tag` job (workflow-level, checked on every tag push) is
necessary but not sufficient on its own — it still runs *after* a tag has
already been pushed. Closing the remaining gap (who is *allowed* to push to
`main` or push a `v*` tag in the first place) requires GitHub repository
settings that live outside this repo's version control and must be applied
by hand, once, in the GitHub UI (Settings → Rules → Rulesets, or the legacy
Settings → Branches / Tags):

- **Branch protection on `main`**: require the `build` status check
  (all matrix legs) to pass before merging; disallow force-pushes and
  branch deletion.
- **Tag protection on `v*`**: restrict who may create or delete a
  matching tag to maintainers, so a release cannot be triggered by
  anyone with ordinary push access alone.

Neither setting is expressible in a workflow YAML file — a workflow only
ever runs *after* a push has already happened, so it can refuse to
*publish* a bad tag (which `verify-tag` now does) but cannot prevent the
push itself. These two settings are what closes that remaining gap.

## Related documents

- [Developer architecture](doc/developer.md)
- [Detailed design](doc/fim-simulator-detailed-design.md)
- [Detailed test plan](doc/fim-simulator-detailed-test-plan.md)
- [Externally accessible engine API](doc/fim-simulator-functional-api.md)
- [Desktop GUI test plan](doc/fim-gui-test-plan.md)
- [Git hooks](dev/git-hooks/README.md)
- [Maintainer scripts](dev/bin/README.md)
- [Project overview](README.md)
