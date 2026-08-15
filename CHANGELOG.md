# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Added repository-local Python, Ruff, mypy, pytest, API-documentation, and
  PyInstaller wrappers so `./build --ci` works without activating a virtual
  environment.
- Made the Git-hook installer portable to native macOS utilities.
- Constrained Pyparsing to the warning-free line supported by Matplotlib 3.9.

## [1.0.0] - 2026-08-14

### Added

- Deterministic finite island model simulation with migration, infinite-alleles
  mutation, genetic drift, and configurable convergence monitoring.
- Incremental JSON Lines persistence for every generation, replayable manifests,
  final differentiation reports, and headless visualizations.
- Command-line workflows for initialization, simulation, persisted-trajectory
  analysis, version reporting, and opt-in update checks.
- Python library API, deterministic tests, local quality gates, release
  packaging, and user and maintainer documentation.

[Unreleased]: https://github.com/selby-botany/jost-finite-island-model/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/selby-botany/jost-finite-island-model/releases/tag/v1.0.0
