# Installation alternatives

The supported researcher distributions are the self-contained executables
described in the [project overview](../README.md#quick-start) (Windows,
macOS, and Linux). These alternatives are intended for developers and
technical maintainers.

## Linux: install.sh

```console
curl -sSL https://raw.githubusercontent.com/selby-botany/jost-finite-island-model/main/install.sh | bash
```

The installer downloads and checksum-verifies the
`fim-linux-x64` release asset, installs `fim` and a `fim-gui` wrapper to
`~/.local/bin` (no root required), and writes a `.desktop` application-menu
entry for `fim-gui`. Set `FIM_INSTALL_VERSION` to
install a specific tag instead of the latest release, or
`FIM_INSTALL_DIR` to install somewhere other than `~/.local/bin`.

**WebKitGTK prerequisite (this installer only):** the GUI (`fim-gui`, or
the CLI's `fim --graphical`) needs WebKitGTK, a system package this
script does not install. Most desktop Linux distributions
already have it as a dependency of another installed application, but a
minimal server-style install may not. If `fim-gui` reports
`WebViewException: You must have either QT or GTK with Python
extensions installed` instead of opening a window, install your
distribution's WebKitGTK package (for example, `libwebkit2gtk-4.1-0`
and `gir1.2-webkit2-4.1` on a Debian/Ubuntu-family system) and try
again. The CLI itself (`fim run`, `fim stats`, `fim init`) needs no such
prerequisite when using this self-contained binary. Installing the Python
package, including an editable installation from a clone, also requires
build dependencies as described below.

## Python package

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
fim --version
```

Python 3.12 or newer is required. On Linux this also installs `pywebview`'s
GTK bindings through the platform-specific `pywebview[gtk]` dependency in
`pyproject.toml`. WebKitGTK must be installed as a system package.

**On a minimal Debian/Ubuntu server, install build dependencies before
running `pip install`.**
`pywebview[gtk]` pulls in `PyGObject` and `pycairo`, both of which `pip`
compiles from source unless a matching prebuilt wheel exists; that build
needs a C compiler and development packages even for CLI-only use.
Without them, installation can fail with errors such as
`Unknown compiler(s)` or `Run-time dependency python found: NO`.
The following package set covers the GTK dependencies used in CI and the
compiler and Python headers required on a minimal Ubuntu 24.04 system:

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
arguments, or `fim --graphical [--detach]`, opens the GUI.
`bin/fim-gui` launches the GUI directly, matching the entry point provided
by the Homebrew formula and `install.sh`. The
[Python package](#python-package) build prerequisites also apply to
`pip install -e .`, which installs the same `pywebview[gtk]` dependencies.

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
