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
mypy --strict src
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

1. Run `./build --ci` on `dev`.
2. Move `[Unreleased]` changes into a dated `X.Y.Z` section.
3. Set the same version in `version.txt`.
4. Merge or fast-forward the verified commit to `main`.
5. Tag `vX.Y.Z` from `main`.
6. Confirm the release workflow publishes the Windows executable, checksum,
   wheel, and source distribution.
7. Download the executable and independently verify `--version`, `--help`, and
   one offline tiny run.

The release workflow rejects a tag that differs from `version.txt`.

## Related documents

- [Developer architecture](doc/developer.md)
- [Detailed design](doc/fim-simulator-detailed-design.md)
- [Test plan](doc/fim-simulator-test-plan.md)
- [Git hooks](dev/git-hooks/README.md)
- [Project overview](README.md)
