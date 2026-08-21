"""Static checks on the Linux install script."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = PROJECT_ROOT / "install.sh"


def _script_text() -> str:
    """Return `install.sh`'s full source text."""
    return INSTALL_SCRIPT.read_text(encoding="utf-8")


def test_install_script_is_executable() -> None:
    """The script is usable directly, not only via `bash install.sh`.

    The documented invocation pipes it into `bash` explicitly
    (`curl ... | bash`), so this is not strictly required for that path
    -- but a downloaded, directly-run copy (`./install.sh`) should also
    work, matching every other rustup/uv-style installer.
    """
    mode = INSTALL_SCRIPT.stat().st_mode
    assert mode & 0o111, "install.sh is not executable"


def test_install_script_never_requires_root() -> None:
    """No `sudo` anywhere -- everything installs under the user's home.

    Design doc 20260821-claude-sonnet-5-macos-linux-packaging.md §3.5:
    matches how rustup/uv behave, and avoids the trust escalation a
    piped-to-a-privileged-shell install would ask a first-time academic
    user for.
    """
    script = _script_text()

    assert "sudo" not in script


def test_install_script_verifies_a_checksum_before_installing() -> None:
    """The downloaded binary is checksummed against its `.sha256` sidecar.

    Regression-shaped test: a script that installs an unverified binary
    from the network is exactly the class of supply-chain gap this
    project's own CI already avoids for its GitHub Actions dependencies
    (test_workflow_pins.py). `sha256sum` must run, and its result must
    gate whether `install` (the actual placement onto `PATH`) happens.
    """
    script = _script_text()
    verify_index = script.index("sha256sum")
    install_index = script.index('install -m 0755 "${binary_path}"')

    assert "checksum mismatch" in script
    assert verify_index < install_index


def test_install_script_rejects_non_linux_and_non_x86_64() -> None:
    """The script fails clearly on an unsupported OS or architecture.

    Only a Linux/x86_64 binary is built (design doc §3.6); running this
    on macOS or an arm64 Linux machine must not silently attempt (and
    fail) a download of an asset that does not exist.
    """
    script = _script_text()

    assert '"${os}" == "Linux"' in script
    assert "x86_64)" in script


def test_install_script_installs_both_entry_points() -> None:
    """`fim` and a `fim-gui` wrapper are both installed, matching Homebrew.

    `fim-gui` is a thin wrapper invoking the same binary's `--graphical`
    flag rather than a second downloaded artifact -- there is only ever
    one binary for Linux (design doc §3.5).
    """
    script = _script_text()

    assert 'fim_path="${install_dir}/fim"' in script
    assert 'fim_gui_path="${install_dir}/fim-gui"' in script
    assert "--graphical" in script


def test_install_script_writes_a_desktop_entry() -> None:
    """A `.desktop` file is installed, not just a PATH-only command.

    The piece CLI-only install-script templates (rustup, uv) skip and
    this GUI actually needs, so `fim-gui` shows up in a real application
    menu (design doc §3.5).
    """
    script = _script_text()

    assert 'applications_dir="${data_dir}/applications"' in script
    assert '"${applications_dir}/fim-gui.desktop"' in script
    assert "[Desktop Entry]" in script
    assert "Exec=" in script


def test_install_script_warns_when_the_install_dir_is_not_on_path() -> None:
    """A missing `PATH` entry produces a visible warning, not a silent gap.

    Otherwise a first-time user's `fim`/`fim-gui` install would appear to
    succeed and then be unrunnable with no explanation.
    """
    script = _script_text()

    assert ":${install_dir}:" in script
    assert "is not on your PATH yet" in script
