# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Dedicated test coverage for per-deme island sizes (`N` as a length-`d`
  array): a per-deme drift-variance statistical test, an end-to-end engine
  run that bounds every generation's per-deme support by its own `N_i`,
  an engine-level check that `deme_weighting: size` uses actual per-deme
  sizes rather than an equal split, a params test bounding
  `initial_allele_count` by the smallest configured `N_i`, and a CLI test
  running a config with `N` as a list.
- Dedicated test coverage for a full asymmetric migration matrix (`m` as a
  `d` by `d` matrix): an operator test with genuinely directional, per-row
  weights (not merely a symmetric square matrix), a test proving the
  matrix path ignores `population_size` even when deme sizes differ by
  three orders of magnitude, a test proving the scalar rate is exactly the
  matrix's equal-size symmetric special case, a reproducible three-deme
  engine run over an asymmetric matrix, and a CLI test running a config
  with `m` as a matrix.
- Dedicated test coverage for per-locus length (`LocusSpec.length`
  varying across a run's `loci`): a params test with genuinely distinct
  lengths per locus rather than one shared value, an engine-level test
  proving the report is bit-identical regardless of which locus carries
  which length (length is inert data — it drives no statistic), a
  reproducible end-to-end run over loci with very different lengths, and
  a CLI test running a config with unequal per-locus lengths.

### Changed

- Updated the simulator design document (§9, §12) to record that unequal
  deme sizes, a full migration matrix, and per-locus length all shipped
  in the very first `SimulationParams`/`locus.py` implementation rather
  than remaining deferred, and to point at the new tests and
  `doc/configuration.md` as the supporting evidence and user-facing
  contract.
- Expanded `doc/configuration.md`'s `m` section with a worked asymmetric
  matrix example and two explicit behavioral notes: a matrix's rows are
  authoritative and are never rescaled by `N`, and the scalar rate is the
  matrix's equal-size symmetric special case.
- Expanded `doc/configuration.md`'s `loci` section with a worked example
  of two loci with different lengths and a note that length currently has
  no effect on simulation behavior.

## [1.0.0] - 2026-08-16

### Added

- Deterministic finite island model simulation with migration, infinite-alleles
  mutation, genetic drift, and configurable convergence monitoring.
- Incremental JSON Lines persistence for every generation, replayable manifests,
  final differentiation reports, and headless visualizations.
- Command-line workflows for initialization, simulation, persisted-trajectory
  analysis, version reporting, and opt-in update checks.
- Python library API, deterministic tests, local quality gates, release
  packaging, and user and maintainer documentation.

### Fixed

- Moved default starter configurations and run artifacts from the user
  `Documents` directory to the portable, space-free `project-root/results/`
  directory.
- Added repository-local Python, Ruff, mypy, pytest, API-documentation, and
  PyInstaller wrappers so `./build --ci` works without activating a virtual
  environment.
- Made the Git-hook installer portable to native macOS utilities.
- Constrained Pyparsing to the warning-free line supported by Matplotlib 3.9.
- Made API generation enumerate every source module explicitly so Linux and
  macOS editable installs produce identical documentation.

[Unreleased]: https://github.com/selby-botany/jost-finite-island-model/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/selby-botany/jost-finite-island-model/releases/tag/v1.0.0
