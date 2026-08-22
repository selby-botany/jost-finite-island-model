#!/usr/bin/env bash
#
# Install fim (and fim-gui) on Linux without a per-distro package.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/selby-botany/jost-finite-island-model/main/install.sh | bash
#
# Design doc 20260821-claude-sonnet-5-macos-linux-packaging.md §3.5: a
# self-contained binary plus this script, in place of maintaining
# separate .deb/.rpm/AUR/Flatpak/Snap packages. No root privileges are
# needed -- everything installs under the current user's home directory,
# matching how rustup/uv behave.

set -o errexit
set -o nounset
set -o pipefail

repo="selby-botany/jost-finite-island-model"
install_dir="${FIM_INSTALL_DIR:-${HOME}/.local/bin}"
data_dir="${XDG_DATA_HOME:-${HOME}/.local/share}"

# Print an error to stderr and exit 1.
# Args:
#   $1: message
# Returns:
#   Does not return -- always exits 1
die() {
    printf 'install.sh: %s\n' "$1" >&2
    exit 1
}

# Confirm this is a supported platform before downloading anything.
# Args:
#   None
# Returns:
#   0 if the platform is supported; calls die() otherwise
check_platform() {
    local os arch
    os=$(uname -s)
    arch=$(uname -m)

    [[ "${os}" == "Linux" ]] || die \
        "this script installs the Linux build only (detected: ${os})." \
        " macOS has its own .app/.dmg release; see the project's Releases page."
    case "${arch}" in
        x86_64) ;;
        *) die "no prebuilt fim binary exists for architecture '${arch}' yet" \
            " (only x86_64 is built today) -- install via pip instead:" \
            " python3 -m pip install fim" ;;
    esac
}

# Resolve the release tag to install: an explicit override, or GitHub's
# "latest release" API.
# Args:
#   None
# Returns:
#   0 and writes the tag name (e.g. "v1.2.0") to stdout
resolve_version() {
    if [[ -n "${FIM_INSTALL_VERSION:-}" ]]; then
        printf '%s\n' "${FIM_INSTALL_VERSION}"
        return 0
    fi
    local api_url tag
    api_url="https://api.github.com/repos/${repo}/releases/latest"
    tag=$(
        curl -fsSL "${api_url}" \
            | grep -m1 '"tag_name"' \
            | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/'
    )
    [[ -n "${tag}" ]] || die "could not determine the latest release tag from ${api_url}"
    printf '%s\n' "${tag}"
}

# Download one release asset and its `.sha256` sidecar, verifying the
# sidecar matches before returning.
# Args:
#   $1: version tag (e.g. "v1.2.0")
#   $2: asset filename (e.g. "fim-linux-x64")
#   $3: destination path for the downloaded, verified file
# Returns:
#   0 on success; calls die() on any download or checksum failure
fetch_verified_asset() {
    local version="$1" asset="$2" destination="$3"
    local base_url binary_url sha256_url expected_sha actual_sha

    base_url="https://github.com/${repo}/releases/download/${version}"
    binary_url="${base_url}/${asset}"
    sha256_url="${base_url}/${asset}.sha256"

    curl -fsSL --output "${destination}" "${binary_url}" \
        || die "download failed: ${binary_url}"
    expected_sha=$(curl -fsSL "${sha256_url}" | awk '{print $1}') \
        || die "download failed: ${sha256_url}"
    [[ -n "${expected_sha}" ]] || die "empty checksum from ${sha256_url}"

    command -v sha256sum >/dev/null 2>&1 \
        || die "sha256sum is required to verify the download but was not found"
    actual_sha=$(sha256sum "${destination}" | awk '{print $1}')
    [[ "${actual_sha}" == "${expected_sha}" ]] \
        || die "checksum mismatch for ${asset}: expected ${expected_sha}, got ${actual_sha}"
}

# Write a `.desktop` entry so fim-gui appears in a real application menu,
# not just as a shell command -- the piece CLI-only install-script
# templates (rustup, uv) skip and this GUI actually needs.
# Args:
#   $1: absolute path to the installed fim-gui wrapper
# Returns:
#   0 and writes the desktop entry
install_desktop_entry() {
    local gui_path="$1" applications_dir="${data_dir}/applications"
    mkdir -p "${applications_dir}"
    cat > "${applications_dir}/fim-gui.desktop" <<DESKTOP_ENTRY
[Desktop Entry]
Type=Application
Name=fim
Comment=Finite island model simulator
Exec=${gui_path}
Terminal=false
Categories=Science;Education;
DESKTOP_ENTRY
    # No custom icon exists in this repository yet (matching the
    # Windows/macOS builds' own unset-icon state) -- omitting Icon=
    # falls back to a generic default rather than inventing artwork.
}

main() {
    check_platform

    local version tmp_dir binary_path fim_path fim_gui_path
    version=$(resolve_version)
    tmp_dir=$(mktemp -d)
    trap 'rm -rf "${tmp_dir}"' EXIT

    binary_path="${tmp_dir}/fim-linux-x64"
    fetch_verified_asset "${version}" "fim-linux-x64" "${binary_path}"

    mkdir -p "${install_dir}"
    fim_path="${install_dir}/fim"
    install -m 0755 "${binary_path}" "${fim_path}"

    # `fim-gui` is a thin wrapper around the same binary's
    # `--graphical` flag (design doc §5.1), matching the two-entry-point
    # convention the Homebrew formula already uses.
    fim_gui_path="${install_dir}/fim-gui"
    cat > "${fim_gui_path}" <<WRAPPER
#!/usr/bin/env bash
exec "${fim_path}" --graphical "\$@"
WRAPPER
    chmod 0755 "${fim_gui_path}"

    install_desktop_entry "${fim_gui_path}"

    printf 'Installed %s\n' "$("${fim_path}" --version)"
    printf '  %s\n' "${fim_path}"
    printf '  %s\n' "${fim_gui_path}"
    case ":${PATH}:" in
        *":${install_dir}:"*) ;;
        *)
            local path_export_line="export PATH=\"${install_dir}:\$PATH\""
            printf '\n%s is not on your PATH yet. Add this to your shell\n' \
                "${install_dir}"
            printf 'startup file (e.g. ~/.bashrc or ~/.zshrc):\n\n'
            printf '  %s\n' "${path_export_line}"
            ;;
    esac
}

main "$@"
