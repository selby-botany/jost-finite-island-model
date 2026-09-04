# Installation alternatives

The supported researcher distributions are the self-contained executables
described in the [project overview](../README.md#quick-start) (Windows,
macOS, and Linux). These alternatives are intended for developers and
technical maintainers.

## Linux: install.sh

```console
curl -sSL https://raw.githubusercontent.com/selby-botany/jost-finite-island-model/main/install.sh | bash
```

A rustup/uv-style installer: downloads and checksum-verifies the
`fim-linux-x64` release asset, installs `fim` and a `fim-gui` wrapper to
`~/.local/bin` (no root required), and writes a `.desktop` entry so
`fim-gui` appears in a real application menu. Set `FIM_INSTALL_VERSION` to
install a specific tag instead of the latest release, or
`FIM_INSTALL_DIR` to install somewhere other than `~/.local/bin`.

**WebKitGTK prerequisite (this installer only):** the GUI (`fim-gui`, or
the CLI's `fim --graphical`) needs WebKitGTK, a system package this
script does not install for you — most desktop Linux distributions
already have it as a dependency of another installed application, but a
minimal server-style install may not. If `fim-gui` reports
`WebViewException: You must have either QT or GTK with Python
extensions installed` instead of opening a window, install your
distribution's WebKitGTK package (for example, `libwebkit2gtk-4.1-0`
and `gir1.2-webkit2-4.1` on a Debian/Ubuntu-family system) and try
again. The CLI itself (`fim run`, `fim stats`, `fim init`) needs no such
prerequisite — but this is specific to `install.sh`'s own self-contained
binary; see the note below for the two install paths under it, which
have a stricter requirement than this one.

## Python package

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
fim --version
```

Python 3.12 or newer is required. On Linux this also installs `pywebview`'s
GTK bindings (`pyproject.toml`'s own `pywebview[gtk]` extra for that
platform) — see the WebKitGTK prerequisite above for the one piece `pip`
cannot install on its own.

**On a genuinely minimal Debian/Ubuntu server, `pip install` itself can
fail outright here — not just the GUI misbehaving at runtime.**
`pywebview[gtk]` pulls in `PyGObject` and `pycairo`, both of which `pip`
compiles from source unless a matching prebuilt wheel exists; that build
needs a C compiler and several `-dev` packages that a minimal server
image does not ship, regardless of whether the CLI is ever used without
the GUI. Confirmed directly on a fresh Ubuntu 24.04 server install (not
assumed from the WebKitGTK note above, which alone is not enough — the
build fails before that runtime package is ever reached), first on a
missing compiler (`Unknown compiler(s): [['cc'], ['gcc'], ...]`), then,
once one was installed, on a missing Python pkg-config file
(`Run-time dependency python found: NO`). Install every one of the
following before `pip install` (matches the exact package set this
project's own CI installs on `ubuntu-latest` — `.github/workflows/
ci.yml`'s own `build` job — plus `build-essential` and `python3-dev`,
which GitHub Actions' runner image already has preinstalled and a bare
server usually does not):

```console
sudo apt-get install -y --no-install-recommends \
    build-essential pkg-config python3-dev \
    gir1.2-gtk-3.0 gir1.2-webkit2-4.1 gir1.2-soup-3.0 \
    libwebkit2gtk-4.1-0 libgirepository1.0-dev libcairo2-dev
```

## Run from a clone

Install runtime dependencies, then put `bin/` on `PATH`:

```console
python -m pip install -e .
export PATH="$PWD/bin:$PATH"
fim --help
```

Both wrappers set `PYTHONPATH` to the clone's `src/` directory and invoke
the active `python3`. `bin/fim` also provides the GUI: `fim` with no
arguments, or `fim --graphical [--detach]`, opens it exactly like the
packaged executables do. `bin/fim-gui` is a direct, argv-sniff-free path to
the same GUI, matching the entry point the Homebrew formula and
`install.sh` both provide. The same minimal-server build prerequisites
as [Python package](#python-package), above, apply here too — `pip
install -e .` pulls in the identical `pywebview[gtk]` dependency chain.

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
