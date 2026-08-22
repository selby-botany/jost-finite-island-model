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

**WebKitGTK prerequisite:** the GUI (`fim-gui`, or the CLI's `fim
--graphical`) needs WebKitGTK, a system package this script does not
install for you — most desktop Linux distributions already have it as a
dependency of another installed application, but a minimal server-style
install may not. If `fim-gui` reports `WebViewException: You must have
either QT or GTK with Python extensions installed` instead of opening a
window, install your distribution's WebKitGTK package (for example,
`libwebkit2gtk-4.1-0` and `gir1.2-webkit2-4.1` on a Debian/Ubuntu-family
system) and try again. The CLI itself (`fim run`, `fim stats`, `fim
init`) needs no such prerequisite.

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
`install.sh` both provide.

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
