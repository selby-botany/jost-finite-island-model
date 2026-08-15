# Installation alternatives

The supported researcher distribution is the self-contained Windows
executable described in the [project overview](../README.md#quick-start).
These alternatives are intended for developers and technical maintainers.

## Python package

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
fim --version
```

Python 3.12 or newer is required.

## Run from a clone

Install runtime dependencies, then put `bin/` on `PATH`:

```console
python -m pip install -e .
export PATH="$PWD/bin:$PATH"
fim --help
```

The wrapper sets `PYTHONPATH` to the clone's `src/` directory and invokes the
active `python3`.

## Homebrew formula

The formula under `homebrew/Formula/fim.rb` is a developer convenience for
macOS or Linux. Update its URL and SHA-256 to the release source archive before
publishing it in a tap. Validate changes with `homebrew/test-formula`; the
script runs Homebrew in its maintained container image and does not require a
host Homebrew installation. Until a release archive exists, the audit permits
only Homebrew's expected `HEAD-only (no stable download)` warning and fails on
every other finding.

Return to the [project overview](../README.md) or the
[command reference](../doc/usage.md).
