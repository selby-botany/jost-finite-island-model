# Security

## Supported version

Security fixes are provided for the current `1.x` release.

## Threat model

`fim` processes local YAML and JSON Lines files and writes local artifacts. A
simulation has no network path, does not execute configuration content, and
does not require elevated privileges.

The only network operation is the explicit `fim update --check` command. It
performs one HTTPS request to the public GitHub Releases API, reports the
latest release page, and never downloads or modifies the executable.

## Windows executable

The release executable is an unsigned research tool, so Windows SmartScreen
may show an unrecognized-publisher warning. Verify the adjacent SHA-256 file
before running it. The executable is built on a GitHub-hosted Windows runner
from the tagged source.

## Dependencies

Runtime dependencies are limited to NumPy, Matplotlib, and PyYAML and are
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
