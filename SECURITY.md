# Security

## Supported version

Security fixes are provided for the current `1.x` release.

| Version | Supported |
|---|---|
| 1.x | Yes |
| < 1.0 | No — pre-release; no compatibility or security guarantees |

## Threat model

`fim` processes local YAML and JSON Lines files and writes local artifacts. A
simulation has no network path, does not execute configuration content, and
does not require elevated privileges.

The only network operation is the explicit `fim update --check` command. It
performs one HTTPS request to the public GitHub Releases API, reports the
latest release page, and never downloads or modifies the executable.

## Local data handling

Treat configurations and trajectories from unknown sources as untrusted data.
`fim` never executes their contents, but a deliberately large input can consume
substantial memory, processor time, or disk space. Run unfamiliar inputs with
normal user permissions and explicit resource limits appropriate to the host.

## Windows executable

Both release executables (`fim-windows-x64.exe` and `fim-windows-arm64.exe`)
are unsigned research tools, so Windows SmartScreen may show an
unrecognized-publisher warning for either one. Verify the adjacent SHA-256
file before running it. Each is built natively — PyInstaller does not
cross-compile — on its own architecture-matched GitHub-hosted Windows
runner, from the same tagged source.

## Dependencies

Runtime dependencies are limited to NumPy, Matplotlib, and PyYAML, plus one
deliberately pinned transitive dependency of Matplotlib, pyparsing (see
[the detailed design's engineering decisions](doc/fim-simulator-detailed-design.md#2-engineering-decisions)
for why it is pinned explicitly rather than left to resolution). All four are
version constrained in `pyproject.toml`. CI runs deterministic tests against
the supported Python versions. Release changes should review published
dependency advisories before tagging.

## Reporting a vulnerability

Use GitHub's private security-advisory reporting feature for the repository.
Include affected version, reproduction steps, expected impact, and whether a
proof-of-concept file contains sensitive data. Do not attach real research
data or credentials to a public issue.

Return to the [project overview](README.md) or read the
[maintainer runbook](CONTRIBUTING.md).
