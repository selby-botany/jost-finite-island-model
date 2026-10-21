<!-- markdownlint-disable MD013 -->

# Finite island model simulator: engineering and release reference

- [Finite island model simulator: engineering and release reference](#finite-island-model-simulator-engineering-and-release-reference)
  - [Who this document is for](#who-this-document-is-for)
  - [1. Scope and ground rules](#1-scope-and-ground-rules)
  - [2. Engineering decisions](#2-engineering-decisions)
    - [2.1 Language, runtime, and dependency footprint](#21-language-runtime-and-dependency-footprint)
    - [2.2 Front-end shape](#22-front-end-shape)
    - [2.3 Distribution and update mechanism](#23-distribution-and-update-mechanism)
    - [2.4 Determinism and reproducibility contract](#24-determinism-and-reproducibility-contract)
  - [3. Repository layout](#3-repository-layout)
  - [4. Toolchain and quality gates](#4-toolchain-and-quality-gates)
  - [5. Continuous integration and release automation](#5-continuous-integration-and-release-automation)
    - [5.1 Branch and tag model](#51-branch-and-tag-model)
    - [5.2 The `ci` workflow](#52-the-ci-workflow)
    - [5.3 The `gitleaks` workflow](#53-the-gitleaks-workflow)
    - [5.4 The release jobs in `ci.yml`](#54-the-release-jobs-in-ciyml)
    - [5.5 Supply-chain hardening](#55-supply-chain-hardening)
  - [6. Packaging and distribution](#6-packaging-and-distribution)
    - [6.1 `pyproject.toml` and the source layout](#61-pyprojecttoml-and-the-source-layout)
    - [6.2 The Windows one-file executable](#62-the-windows-one-file-executable)
    - [6.3 Developer installation paths](#63-developer-installation-paths)
    - [6.4 Versioning](#64-versioning)
  - [7. The `build` script — local CI equivalent](#7-the-build-script--local-ci-equivalent)
  - [8. Documentation set and developer workflow](#8-documentation-set-and-developer-workflow)
    - [8.1 Generated API reference and doc freshness](#81-generated-api-reference-and-doc-freshness)
    - [8.2 Git hooks: the local pre-CI safety gate](#82-git-hooks-the-local-pre-ci-safety-gate)
    - [8.3 Documentation navigation and link checking](#83-documentation-navigation-and-link-checking)
  - [9. Test strategy summary](#9-test-strategy-summary)
  - [10. Risks and mitigations](#10-risks-and-mitigations)

## Who this document is for

This developer reference covers repository organization, quality gates,
continuous integration, packaging, and releases. The companion
[design document](fim-simulator-design.md) specifies the model and
architecture; the [functional API](fim-simulator-functional-api.md)
and [Python guide](python-api.md) document supported calling interfaces.

Test detail is delegated in full to the companion
[detailed test plan](fim-simulator-detailed-test-plan.md); §9 here is
only a summary and a pointer. Section references of the form "design §N"
point into the design document; bare "§N" references point within this
document.

Section §8 maps the documentation set by audience and purpose.

## 1. Scope and ground rules

This covers the finite island model simulator's engineering scope: a
single symmetric-island core, built so further variations are extensions
of the parameter set and pipeline rather than rewrites (design §9). Design
§11's "Out of scope" items are out of scope here too.

Engineering requirements:

- **Maintainability.** Setup, build, test, and release procedures must be
  usable without access to the original author. `CONTRIBUTING.md`
  provides the maintainer runbook.
- **Valid commit boundaries.** Each commit must pass lint, type, and
  test gates so history remains bisectable.

## 2. Engineering decisions

The design document settles the model, statistics, and architecture
(Python, NumPy, JSONL, ploidy-neutral `N`, and so on). This section records
the engineering decisions on top of that: the concrete runtime,
distribution, and reproducibility choices a maintainer needs to know.

### 2.1 Language, runtime, and dependency footprint

| Concern | Decision | Rationale |
|---|---|---|
| Language | Python 3.12+ | Design §4.4 confirms Python 3; 3.12 is the floor for `type` aliases and improved error messages, and has stable Windows wheels for every dependency below. |
| Array backend | NumPy | Design §4.4; the drift/mutation workload is batched multinomial/binomial sampling (design §5). |
| Plotting | Matplotlib | Design §8; prebuilt Windows wheels, no compiler needed (design §4.5). Rendered with the non-interactive `Agg` backend so plots are reproducible and headless-safe. |
| Config parsing | PyYAML | Parses YAML configuration (design §12). |
| Desktop interface | pywebview | Uses native webviews; Linux installs the `gtk` extra and requires GTK/WebKitGTK system libraries. |
| Transitive constraint | pyparsing | The supported range is declared explicitly in `pyproject.toml` for compatibility with Matplotlib. |
| **Excluded** | SciPy, pandas | NumPy supplies sampling, persistence exposes plain row mappings, and confidence intervals use a t-table plus standard-library `NormalDist`; neither package is a runtime requirement. |

`pyproject.toml` is authoritative for dependency versions and platform
markers. NumPy supports the simulation and statistics; Matplotlib
supports headless plots; PyYAML handles configuration; pywebview supplies
the desktop interface. The optional `jit` extra provides Numba for compiled
backends.

### 2.2 Front-end shape

The CLI and pywebview desktop application share the model, engine,
statistics, and persistence APIs. `fim.launcher:main` dispatches CLI
commands and graphical launches; `fim-gui` invokes the desktop entry
point directly. Front ends do not implement biological operators.
See the [GUI design](fim-gui-design.md).

### 2.3 Distribution and update mechanism

**Versioned GitHub Releases, with manual replacement as the update path
and an opt-in version check.** Concretely:

- Every `v*` tag triggers the release workflow (§5.4), which builds
  `fim-windows-x64.exe`, computes its SHA-256, and attaches both to a
  GitHub Release whose notes are the matching `CHANGELOG.md` section.
- Updating is downloading the newer `.exe` and replacing the old one —
  matching design §12's "uninstalling is deleting the `.exe`" model, with
  no installer, no admin rights, and no background updater.
- `fim --version` prints the bundled version. `fim update --check` (an
  explicit, user-invoked subcommand only) queries the GitHub Releases API
  over HTTPS and reports whether a newer tag exists, printing the download
  URL. It never downloads or self-modifies.

This preserves design §4.5's hard "no network dependency at run time"
guarantee precisely: a *simulation* run — the update pipeline, statistics,
and visualization — reaches no network. The only code path that touches the
network is a subcommand a user runs deliberately to ask "is there a newer
version," never as part of producing a result.

### 2.4 Determinism and reproducibility contract

Simulation and test reproducibility follow these requirements:

1. **Explicitly seeded random streams.** Streams derive from
   `SimulationParams.seed` and are supplied to stochastic operators and
   initialization. Replicates and demes have explicit stream states;
   checkpoints retain the states needed for continuation. Model code
   does not use the global NumPy RNG, `random`, or mid-run reseeding.
   A run is reproducible within the numerical-platform limits in design §4.6.
2. **No wall-clock or environment in logic.** Timestamps appear only in the
   manifest and in output *filenames*, never in a value that a statistic,
   convergence decision, or persisted row depends on.
3. **No network in a run.** Enforced structurally by §2.3.
4. **Tolerances derived before the seed is chosen.** Every statistical test
   fixes its seed(s) and derives its tolerance band analytically from the
   sample size *in advance* (see the test plan). A seed is never selected
   after the fact because it happens to pass.

## 3. Repository layout

The repository extends the design document's module layout (design §5)
with the packaging, CI, install, and documentation scaffolding an
inheritable project needs:

```text
jost-finite-island-model/
├── .github/
│   └── workflows/
│       ├── ci.yml                 # lint/type/test/coverage; on a tag, gates and
│       │                           # runs build exe + wheel/sdist → GitHub Release
│       └── gitleaks-ci.yml        # secret scan
├── .gitignore
├── .htmlhintrc                    # HTML lint config (whole documents)
├── .htmlhintrc-fragment           # HTML lint config (generated help partials)
├── .markdownlint.json             # markdown lint config
├── .markdownlintignore
├── .stylelintrc.json              # CSS lint config
├── .yamllint.yml                  # workflow YAML lint config
├── build                          # local CI equivalent (lint+type+test+package)
├── CHANGELOG.md                   # Keep a Changelog format
├── CONTRIBUTING.md                # maintainer runbook (single-maintainer)
├── LICENSE.md                     # AGPL-3.0-or-later
├── README.md                      # the full user entry point
├── SECURITY.md                    # threat model + reporting
├── pyproject.toml                 # PEP 621 metadata, deps, tool config
├── version.txt                    # single source of truth for the version string
├── dev/
│   ├── git-hooks/                 # version-controlled hooks (§8.2)
│   │   ├── install                # symlinks the hooks into .git/hooks/
│   │   ├── pre-commit             # staged format + API-doc refresh + filename guard
│   │   ├── commit-msg             # Conventional Commits enforcement
│   │   ├── pre-push               # fast static gates against the pushed commits
│   │   └── README.md              # hook set + install instructions
│   └── bin/
│       ├── calibrate-statistical-bands  # versioned equilibrium-test band
│       │                           # characterization (§9)
│       ├── check-doc-links        # validates Markdown links + anchors (§8.3)
│       ├── extract-release-notes  # one CHANGELOG.md section → release notes (§5.4)
│       ├── generate-api-docs      # docstrings → src/fim/API.md (pydoc-markdown)
│       └── validate-repository    # shell/YAML/Markdown/secret checks (§4)
├── doc/                           # design docs + user/developer guides
│   ├── finite-island-model-introduction.md   # companion: the model
│   ├── jost-differentiation-measures.md      # companion: the statistics
│   ├── fim-simulator-design.md    # model, statistics, and architecture
│   ├── fim-simulator-detailed-design.md  # this doc
│   ├── fim-simulator-test-plan.md # companion test plan (botanist-facing)
│   ├── fim-simulator-detailed-test-plan.md # companion test plan (developer-facing)
│   ├── fim-simulator-functional-api.md     # externally accessible engine API
│   ├── fim-gui-design.md          # desktop GUI architecture and design
│   ├── fim-gui-test-plan.md       # desktop GUI test coverage
│   ├── fim-logging-design.md      # operational logging: -l/-L, where calls live
│   ├── usage.md                   # user-facing command + config reference
│   ├── configuration.md           # the P-bag schema, every key, every default
│   ├── developer.md               # architecture-for-maintainers + how to extend
│   ├── statistical-calibration-evidence.md  # retained calibration-pass
│   │                               # output (§9)
│   └── img/                       # design mockups and release screenshots
├── include/
│   └── dot-bashrc                 # puts bin/ on PATH for the current shell
├── install/
│   ├── README.md                  # non-default install paths
│   └── homebrew/
│       ├── Formula/fim.rb          # macOS/Linux developer install
│       └── test-formula            # dockerized brew style/audit check
├── packaging/
│   └── fim.spec                   # PyInstaller one-file spec
├── src/
│   ├── README.md                  # developer orientation for the source tree (§8.1)
│   └── fim/
│       ├── __init__.py            # __version__ read from version.txt
│       ├── API.md                 # generated Markdown API reference (§8.1)
│       ├── model/
│       │   ├── allele.py          # AlleleId, AlleleRegistry
│       │   ├── locus.py           # LocusSpec
│       │   ├── state.py           # ModelState (ψ_k,t), (de)serialization
│       │   ├── params.py          # SimulationParams, validated P-bag schema
│       │   ├── initial.py         # initial-condition generators
│       │   ├── operators.py       # migrate(), mutate(), drift() — pure fns
│       │   └── topology.py        # sparse and stepping-stone migration maps
│       ├── convergence/
│       │   ├── criteria.py        # ConvergenceCriterion protocol + built-ins
│       │   └── monitor.py         # ConvergenceMonitor
│       ├── statistics/
│       │   ├── differentiation.py # H, H_S, H_T, G_ST, D, E_ST, K_ST, Hill numbers
│       │   └── interval.py        # across-replicate confidence intervals
│       ├── persistence/
│       │   ├── store.py           # TrajectoryStore protocol
│       │   ├── binary_store.py    # BinaryLogStore — the file-backed store
│       │   └── manifest.py
│       ├── viz/
│       │   ├── scatter.py         # canonical d-dimensional frequency scatter
│       │   └── diagnostics.py     # convergence trace, per-deme frequency bars
│       ├── engine/                # run loops and backend selection
│       └── cli.py                 # command-line entry point
├── test/
│   ├── conftest.py                # shared fixtures, seeded RNG helpers
│   ├── data/                      # golden-value fixtures (Part IV scenarios)
│   ├── model/
│   ├── convergence/
│   ├── statistics/
│   ├── persistence/
│   ├── viz/
│   ├── engine/
│   ├── cli/
│   └── validation/                # published-scenario + asymptotic tests
└── bin/
    ├── fim                        # thin POSIX wrapper invoking the CLI from a clone
    ├── mypy, pytest, python3, ...  # wrappers selecting the .venv toolchain (§4)
    └── gitleaks, markdownlint, eslint, ...  # digest-pinned Docker-image wrappers (§4)
```

The `src/fim/` subtree follows the design document's module layout
(design §5), plus one directory the design document implies but does not
name — `test/validation/` for the stochastic published-scenario checks
(design §10) that are neither pure unit tests nor tied to one module, and
which also hosts the repository-tooling checks (test plan §10.1). The
`dev/` subtree holds the developer workflow that never ships to a user:
the git-hook safety gates, the API-doc generator, the link checker, the
release-notes extractor, and the repository-file validator (§4, §8).
`src/README.md` plus the generated `src/fim/API.md` are the two
source-tree documents §8.1 defines.

`bin/` and `include/dot-bashrc` are the repository's own toolchain
boundary: sourcing `include/dot-bashrc` puts `bin/` on `PATH`, and every
wrapper there resolves either the project's `.venv` interpreter or a
digest-pinned Docker image, so no check depends on what a maintainer
happens to have installed. `build` and the git hooks prepend `bin/`
themselves, so sourcing the file is a convenience for interactive use
rather than a prerequisite.

## 4. Toolchain and quality gates

CI invokes the same Python gates as the local `build` script (§7).
Dependency constraints live in `pyproject.toml`'s optional `dev` group;
Docker-backed wrappers in `bin/` pin image digests. The table identifies
the tools; consult the manifest for current version ranges.

| Gate | Tool | Pin (baseline) | What it enforces |
|---|---|---|---|
| Lint + format | Ruff | `pyproject.toml` | Style, import order, common bugs; `ruff format` is the formatter. Line length 88. |
| Type check | mypy | `pyproject.toml` | Strict checking of source and tests; `AlleleId` is a distinct `NewType(int)` (design §3.2). |
| Unit + property tests | pytest, Hypothesis | `pyproject.toml` | Correctness (§10, test plan). |
| Coverage | coverage.py | `coverage==7.*` | Branch coverage; gate at the threshold in §5.2. |
| API reference docs | pydoc-markdown | `pydoc-markdown==4.*` | Regenerates `src/fim/API.md` from module docstrings; dev-only, never a runtime dependency, so it never enters the PyInstaller bundle (§2.1/§8.1). |
| Secret scan | gitleaks | `gitleaks/gitleaks-action@v2` | No credentials in history (§5.3). |
| Repository-file checks | ShellCheck, yamllint, markdownlint-cli2, ESLint, Stylelint, HTMLHint, gitleaks, Homebrew | image digest per `bin/` wrapper | Shell scripts, workflow YAML, Markdown, GUI JavaScript/CSS/HTML, committed secrets, and the Homebrew formula, all through `dev/bin/validate-repository`. |
| GUI asset integrity | `dev/bin/check-webui-assets` | interpreter-only, no Docker | No orphaned CSS rule, no unstyled/unread class, no broken local link or SVG sprite reference; runs in `./build`. |

The two gate families differ in where they run, deliberately.
`./build --ci` and `ci.yml` (§5.2) cover everything the Python package is
made of and need nothing but an interpreter. The repository-file checks
need Docker, so they run on demand through `dev/bin/validate-repository`
rather than inside `ci.yml`; the secret scan is the one of them CI repeats
on its own, in a separate workflow (§5.3), because a leaked credential
cannot wait for a maintainer to run a local command.

`AlleleId` distinguishes allele identifiers from unrelated integers in
typed interfaces. As an integer-backed `NewType`, it does not itself
prohibit ordering or arithmetic; identity-only use is a model invariant
(design §3.2).

The same gate set runs locally *before* CI through repository-managed git
hooks (§8.2): `pre-commit` auto-formats staged Python and refreshes the
generated API docs, `commit-msg` enforces Conventional Commits, and
`pre-push` runs the fast static gates over the whole tree before a push
lands. The hooks are a convenience and a first line of defense, never the
authority — CI (§5) re-runs every gate, so a hook bypassed with
`--no-verify` still cannot land un-gated code on `main`.

## 5. Continuous integration and release automation

The project is a standard public GitHub-distributed application. CI is
self-contained — standard published actions plus the project's own `build`
script — rather than a shared/reusable organization workflow, because this
repository stands alone and should be forkable and buildable with no
dependency on any private workflow repo.

### 5.1 Branch and tag model

A simple branch model:

- `dev` — the working branch; every commit lands here.
- `staging` — an optional pre-release gate; pushing here (or dispatching
  it manually) triggers `beta.yml` (§5.4), which builds an unofficial,
  unreleased artifact for every platform `ci.yml`'s own tagged release
  path builds, for hands-on verification before cutting a real tag.
- `main` — release-ready; `dev` is fast-forwarded or merged here only at a
  release boundary.
- `v*` tags — cut from `main`, drive the release workflow (§5.4).

CI (`ci.yml`) runs on pushes to `dev`/`main` and on pull requests
targeting them, and on `v*` tags. Concurrency cancels superseded runs
except on tags, so a release build is never cancelled mid-flight.

### 5.2 The `ci` workflow

`.github/workflows/ci.yml` runs on `ubuntu-latest` across a Python matrix
(`3.12`, `3.13`) for every push to `dev`/`main` and every pull request
targeting them. It installs the pinned `dev` dependencies, then runs the
test suite as two separately named, separately budgeted steps before the
workflow file's logic bottoms out in `./build`, the one script a
maintainer runs identically offline (§7). Permissions are `contents: read`.

Two separately named steps expose their durations and enforce independent
runner budgets:

1. `./build --no-lint --no-type --no-docs --no-package` — the same
   deterministic layer `--ci` also covers (`pyproject.toml`'s default
   marker filter, no coverage). `timeout-minutes: 5`.
2. `./build --ci` — the authoritative gate (below). `timeout-minutes: 20`.

Each step's own duration is visible in the Actions run summary natively,
with no extra instrumentation; each `timeout-minutes` is a hard budget
enforced by the runner itself rather than a wall-clock assertion inside
the test run, which machine-speed variance would make a non-deterministic
pass/fail signal (the same commit reporting differently only because of
runner load). `test/validation/test_ci_runtime_budget.py` statically
checks both steps stay present, correctly ordered, and budgeted.

`--ci` itself runs these stages in order (§7 gives the flag surface):

1. `ruff check` and `ruff format --check` over `src`, `test`, and the
   Python programs under `dev/bin`.
2. `mypy` (bare — `[tool.mypy]`'s `strict = true` and
   `files = ["src", "test"]` are the single source of truth for scope; an
   explicit positional argument here would silently narrow it back to
   `src` alone and drop `test` from the checked set — the exact
   regression test/test_mypy_scope.py guards against).
3. `pytest` with branch coverage, excluding only the `slow` marker — so
   the authoritative gate still runs every other layer the fast default
   invocation skips for local iteration speed (`statistical`, `gui`, and
   `packaging` — test plan §3), failing below the coverage gate: **90%**
   of `src/fim`, with every package measured; `viz/` carries no coverage
   omit and is currently at 100%.
4. Regenerate `src/fim/API.md` and `test/TESTS.md` to a scratch path and
   diff each against its committed copy (the doc-freshness gate, §8.1),
   then validate every Markdown link and in-page anchor with
   `dev/bin/check-doc-links` (§8.3).
5. Build the wheel and sdist, install the wheel into a throwaway virtual
   environment outside the checkout, and run `fim --version` and
   `fim --help` from the installed entry point. This stage checks
   `pyproject.toml`'s entry-point wiring.

The `slow`-marked engine-scenario tests in
`test/validation/test_simulator_equilibrium.py` run separately through
`./build --slow-only`. The `slow-tests` job has a 90-minute budget and
runs on a daily `schedule` or manual `workflow_dispatch`, not on pushes
or pull requests. These CPU-bound literature comparisons are assigned
to nightly or on-demand validation
([test plan §3](fim-simulator-detailed-test-plan.md#3-layout-tooling-and-markers)).
The `build` and Homebrew formula jobs (§6.3) exclude these triggers.

### 5.3 The `gitleaks` workflow

`.github/workflows/gitleaks-ci.yml` runs a full-history checkout
(`fetch-depth: 0`) and `gitleaks/gitleaks-action`, both pinned to a
commit SHA (§5.5). It is a separate workflow, not a step
in `ci.yml`, so a secret-scan failure is legible on its own and does not
mask a test failure or vice versa.

### 5.4 The release jobs in `ci.yml`

Release jobs in `ci.yml` use the `needs:` graph to require a passing
build and verified tag before publishing:

1. **`verify-tag` (`if: startsWith(github.ref, 'refs/tags/v')`, runs
   alongside `build`).** A full-history checkout, then: `git rev-parse --verify
   "$GITHUB_REF_NAME^{tag}"` must succeed, which is only true for an
   *annotated* tag (a lightweight tag has no tag object for ^{tag} to
   resolve) — rejecting a `git tag v1.2.3` shorthand that skipped a
   message entirely; then `git merge-base --is-ancestor "$GITHUB_SHA"
   origin/main` must hold, rejecting a tag pushed from a commit `main`
   has never merged.
2. **Five platform jobs, each `needs: [build, verify-tag]` and
   independent of the other four** (PyInstaller never cross-compiles —
   producing a native executable always means actually running the
   build on a matching runner, not passing a different flag on a shared
   one):
   - **`windows`** (`windows-latest`) and **`windows-arm64`**
     (`windows-11-arm`, the ARM64-native runner) — `pyinstaller
     packaging/fim.spec`, smoke-test the resulting `dist/fim.exe`
     (`--version` must print the tag's version; a tiny bundled config
     must run end-to-end offline; the zero-argument launch must stay
     alive for a few seconds, proving WebView2 starts), rename to
     `fim-windows-x64.exe`/`fim-windows-arm64.exe`, emit each `.sha256`.
   - **`macos-arm64`** (`macos-latest`, Apple Silicon) and
     **`macos-x64`** (`macos-15-intel`) — the same PyInstaller spec
     builds a real `.app` bundle on macOS (`packaging/fim.spec`'s
     `sys.platform == "darwin"` branch, onedir plus `BUNDLE`, so Finder
     can double-click-launch it); the same smoke checks run against it,
     then it is wrapped in a `.dmg` (with a symlinked `/Applications`
     for the standard drag-to-install pattern) and hashed.
   - **`linux-x64`** (`ubuntu-latest`, built inside a
     `python:3.12-slim-bookworm` container rather than a bare runner or
     a `manylinux` image — a bare runner's glibc can be newer than a
     user's own system, and manylinux's own bundled Pythons lack the
     `--enable-shared` build PyInstaller requires) — the same smoke
     checks, run under `xvfb-run` for the GUI-launch check; a second,
     dependency-purged smoke run confirms the zero-argument launch
     fails loudly (not silently or by hanging) on a machine with no
     WebKitGTK at all.
3. **`publish`** (`needs: [windows, windows-arm64, macos-arm64,
   macos-x64, linux-x64]`, runs-on `ubuntu-latest`, its own `contents:
   write` permission — the only job in the workflow that needs it).
   Build the `sdist` + `wheel`, verify `version.txt` equals the tag
   (fail loudly on mismatch — a tag that disagrees with `version.txt`
   is a release bug, not a warning), extract the matching
   `CHANGELOG.md` section as the release notes, download and re-verify
   every platform job's artifact against its own `.sha256` (an
   artifact crossed a job boundary to get here, so it is checked again
   rather than re-shipped unread), generate one consolidated
   `SHA256SUMS` manifest (§5.5) covering every artifact, and create the
   GitHub Release attaching all of it: both Windows executables, both
   macOS `.dmg` images, the Linux binary, the wheel, and the sdist.

The version check keeps bundle, manifest, CLI, and formula versions
consistent. `verify-tag` and `needs:` require an annotated tag reachable
from `main` and a passing build before publication. The separately
scheduled slow tests are not part of the per-tag build gate (§5.2).

Repository-level branch protection on `main` and tag protection on `v*`
(who may push either) are configured directly in GitHub settings, not in
a workflow file — see `CONTRIBUTING.md`'s "Repository settings" checklist.

### 5.5 Supply-chain hardening

Every `uses:` reference in all three workflow files (`ci.yml`, §5.2–§5.4;
`gitleaks-ci.yml`, §5.3; `beta.yml`, an unofficial per-platform build
triggered from `staging`, §5.1) names a full 40-character commit SHA,
never a mutable tag like `@v4` — a tag owner can
silently repoint it at different, unreviewed content at any time, the same
risk this project's own `bin/` wrappers already avoid by pinning Docker
images to a digest rather than a floating tag. A trailing `# vN` comment
keeps the pin human-readable; test/validation/test_workflow_pins.py
parses every workflow file and fails if any reference is not a full SHA.

`.github/dependabot.yml` tracks `pip`
(the ranges in `pyproject.toml`) and `github-actions` (the SHA pins
above), each on a weekly cadence, each landing as an ordinary reviewable
PR rather than an automatic merge.

`publish` (§5.4) generates `SHA256SUMS` inside `dist/` — covering the
wheel, the sdist, and all five platform executables — before `gh release
create` runs, so every artifact a release actually ships has a checksum
attached to it. Each platform executable also keeps its own separate
`.sha256` sidecar file (`README.md` documents the Windows one
specifically for a Windows user's manual verification), which `publish`
re-verifies against the file that actually arrived before ever
re-shipping it.

**Not implemented:** a hash-locked constraints file (`pip install
--require-hashes` against a lock file covering transitive dependencies,
so even a version-range match cannot silently substitute compromised
package content) needs a lock-file tool this project does not currently
depend on (`pip-tools` or `uv`) and an ongoing regeneration process.
Version constraints alone do not verify package content hashes.

## 6. Packaging and distribution

### 6.1 `pyproject.toml` and the source layout

PEP 621 metadata with a `src/` layout and a standard build backend
(`hatchling`). Key fields: `name = "fim"`, `requires-python = ">=3.12"`,
runtime dependencies listed in §2.1; a `dev` optional group
holding the pinned toolchain (§4); dynamic `version` read from
`version.txt`; and a console entry point:

```toml
[project.scripts]
fim = "fim.launcher:main"
fim-gui = "fim.gui.app:main"
```

The `src/` layout guarantees tests run against the installed package, not
the working tree, which is what makes the packaging smoke job (§5.2)
meaningful.

### 6.2 The Windows one-file executable

`packaging/fim.spec` drives PyInstaller in one-file mode (design §4.5). The
spec collects Matplotlib data, the `Agg` backend, pywebview, and local web
assets. It excludes Tk/Qt Matplotlib backends and sets `console=True`.
The launcher supports both CLI and desktop invocation (§2.2).
The build is done in the release workflow on `windows-latest`
(§5.4); no cross-compilation, no local Windows machine required of the
maintainer.

The binary bundles Python and package dependencies. Desktop operation
also requires the platform webview runtime; `fim init` creates a starter
configuration. See [installation](../install/README.md).

### 6.3 Developer installation paths

`install/homebrew/Formula/fim.rb` provides a macOS/Linux developer install
(`pipx`-style behavior via a Python formula, or a thin wrapper installing
`bin/fim`), and `install/homebrew/test-formula` validates it with
`brew style`/`brew audit` inside the `homebrew/brew` Docker image — no
Homebrew required on the host. `install/README.md`
documents the non-default paths (Homebrew, `pip install`, and plain
`PATH`-from-a-clone via `bin/fim`). The homepage/URL fields point at
`github.com/selby-botany/jost-finite-island-model`.

### 6.4 Versioning

`version.txt` is the single source of truth. `src/fim/__init__.py` reads it
to expose `__version__`; `pyproject.toml` binds `version` to it dynamically;
the manifest records it per run (design §6); the release workflow asserts
the tag matches it (§5.4). Versioned with Semantic Versioning.
`CHANGELOG.md` follows Keep a Changelog with an `[Unreleased]` section
that every feature commit updates as it lands.

## 7. The `build` script — local CI equivalent

A single `build` script at the repository root is the local mirror of CI
for this Python project. CI invokes it directly (§5.2).

Stages, in order, each skippable by flag for fast iteration:

```text
build [--ci] [--coverage] [--dry-run]
      [--no-lint] [--no-type] [--no-test] [--no-docs] [--no-package]
      [--slow-only] [--help]

  1. lint     ruff check + ruff format --check
  2. type     mypy (bare; scope comes from [tool.mypy] in pyproject.toml)
  3. test     pytest (+ branch coverage with --coverage or --ci); --ci
              excludes only the `slow` marker; --slow-only runs the
              `slow` marker alone, with lint/type/docs/package forced off
  4. docs     regenerate src/fim/API.md and test/TESTS.md from
              docstrings; with --ci, verify each matches its committed
              copy and fail if stale (§8.1); run dev/bin/check-doc-links
              to validate every Markdown link and anchor (§8.3)
  5. package  build wheel/sdist, install into a throwaway venv,
              run `fim --version` and `fim --help`
```

`--ci` selects the full, non-skippable gate set with coverage enforcement
and the stale-doc check, and is what `ci.yml`'s main gate calls;
`--slow-only` is what its separately scheduled `slow-tests` job calls
instead (§5.2). Run without `--ci`, the `docs` stage regenerates
`src/fim/API.md`/`test/TESTS.md` in place (the write the `pre-commit`
hook performs); with `--ci` it regenerates each to a temporary location
and diffs, never writing, so CI stays read-only. `--dry-run` prints each
command without running it. The script needs only Python and a POSIX
shell; it creates and tears down its own virtual environments so it
never mutates the maintainer's global Python.

## 8. Documentation set and developer workflow

All user-facing documentation serves the four reader roles the project
mandates. Documents are organized for reader efficiency (progressive
disclosure, role callouts) rather than one file per role.

| Document | Primary role(s) | Contents |
|---|---|---|
| `README.md` | Non-technical user, sysops, developer | Contents, quick start (install + first run), architecture overview, link map to everything else. The single entry point. |
| `doc/usage.md` | Non-technical user, sysops | Every subcommand and flag, the config file walked key by key, worked examples. |
| `doc/configuration.md` | Sysops, developer | The `P`-bag schema (design §4.3) as a reference: every key, type, default, and effect. |
| `doc/developer.md` | Developer | Architecture for a maintainer/inheritor: module map, the pure-function pipeline, where each future "what if" lands (design §9), how to run and extend the tests. |
| `src/README.md` | Developer | Orientation for the source tree: module map, the pure-function pipeline in one paragraph, how to run `build`, and how the generated API reference is produced and kept fresh (§8.1). |
| `src/fim/API.md` | Developer | Generated Markdown API reference (pydoc) for the public API; regenerated by the `pre-commit` hook and the `build` docs stage, verified fresh in CI (§8.1). |
| `install/README.md` | Sysops, developer | Installation paths other than the Windows executable: the Python package, running from a clone, and the Homebrew formula. |
| `SECURITY.md` | Sysops, Geek Squad, developer | Threat model (offline tool, unsigned Windows binary, SmartScreen note), the opt-in-only network path (§2.3), CVE/dependency posture, and how to report an issue. |
| `CONTRIBUTING.md` | Developer (maintainer) | Maintainer runbook: dev setup (incl. `bash dev/git-hooks/install`), `build`, test layout, commit conventions, release steps. Explicitly single-maintainer (§1). |
| `CHANGELOG.md` | Sysops, developer | Keep a Changelog; release notes source (§5.4). |
| `dev/git-hooks/README.md` | Developer | The hook set, what each gate does, and the one-line install step (§8.2). |
| The two companion design docs | Developer, botanist | Model + statistics reference (design doc's own sources). |
| `doc/fim-gui-design.md` | Developer | Desktop GUI architecture: the `Api` bridge, the unified run view, run orchestration, and how it shares logic with the CLI. |
| `doc/fim-logging-design.md` | Developer | Operational logging: the `-l`/`-L` CLI flags, the GUI's environment-variable equivalents, and where log calls live. |
| This document + test plan | Developer | Engineering, release, and test reference. |

The durability rule applies to every row: no document says "ask the
author." Recovery, extension, and troubleshooting steps are written for a
competent stranger.

### 8.1 Generated API reference and doc freshness

The source tree carries two developer documents. `src/README.md` is
hand-written — an orientation to the module layout, the three-stage
pure-function pipeline, and how to run the tooling. `src/fim/API.md` is
**generated** from the module docstrings, which follow a Purpose / Args /
Returns convention. The generator is `pydoc-markdown`, invoked through the
thin `dev/bin/generate-api-docs` wrapper and the `build` docs stage (§7).

`pydoc-markdown` is a **dev-only** tool: it is in the `dev` dependency
group, never in the runtime set, so it never enters the Windows bundle
(§2.1). The API reference is committed into the tree — not produced only as
a CI artifact — for two concrete reasons: it is browsable directly on
GitHub with no build step, and its diff in a pull request or a `git log` is
a reviewable signal that the public API surface changed.

Generated-reference freshness is checked at three points:

1. **`pre-commit` regenerates and re-stages.** When a staged change touches
   `src/fim/**/*.py`, the hook regenerates `src/fim/API.md` and re-stages
   it, so the doc lands in the same commit as the code.
2. **`pre-push` verifies the tree about to be published.** The hook
   regenerates the reference and fails if the committed `API.md` differs.
   This closes the gap `pre-commit` cannot: a rebase, or a commit made with
   `--no-verify`, never runs `pre-commit`, so a series can reach the point
   of being pushed carrying a stale reference. The hook reads the working
   tree, so it assumes the working tree is what is being pushed; layer 3
   is the one that makes no such assumption.
3. **CI re-checks.** `build --ci` (and therefore `ci.yml`, §5.2)
   regenerates to a scratch path and runs `git diff --exit-code`, so even a
   fully bypassed local setup cannot land a stale `API.md` on `main`.

### 8.2 Git hooks: the local pre-CI safety gate

The repository ships version-controlled git hooks under `dev/git-hooks/`.
Hook sources live in the working tree (authoritative and reviewable); a
`dev/git-hooks/install` script symlinks them into `.git/hooks/` so an edit
to a hook takes effect on the next run without reinstalling. The one-line
setup step — `bash dev/git-hooks/install`, re-run whenever the hook set
changes — is recorded in `README.md`, `CONTRIBUTING.md`, and
`dev/git-hooks/README.md`.

Three hooks:

- **`commit-msg` — Conventional Commits.** Validates the first non-comment
  subject line against `type(scope)!: summary`, allowing merge, revert, and
  `fixup!`/`squash!` prefixes.
- **`pre-commit` — staged, fast, self-healing.** On staged files only:
  run `ruff format` and `ruff check --fix` on staged Python and re-stage
  the result; regenerate `src/fim/API.md` when a staged `.py` changed and
  re-stage it (§8.1); and reject newly added non-ASCII filenames.
  Staged-only keeps it fast enough to run on every commit without
  tempting a bypass.
- **`pre-push` — the whole tree, not just what one commit touched.** Runs
  `ruff check src test`, bare `mypy`, the `pytest` subset excluding
  the `statistical`, `slow`, and `packaging` markers, and the API-doc
  freshness check (§8.1). Each gate is bypassable in a genuine emergency
  through `PRE_PUSH_SKIP_LINT`, `PRE_PUSH_SKIP_TYPE`, `PRE_PUSH_SKIP_TEST`,
  and `PRE_PUSH_SKIP_DOCS`.

Each hook
no-ops with an informational message when its tool or `pyproject.toml` is
absent, so a fresh clone that has not yet run `pip install` of the `dev`
group is never blocked by a missing linter.

`--no-verify` bypasses local hooks. CI (§5) runs its gates independently.
Local hooks provide earlier feedback through staged-file checks and a
fast test subset.

### 8.3 Documentation navigation and link checking

Documentation is only durable (§1) if a reader who lands on any page can
reach every related page and every section within it:

- **Intra-document.** Every document longer than a couple of screens
  carries a table of contents whose anchors resolve. Long reference
  documents (`doc/usage.md`, `doc/configuration.md`, `src/fim/API.md`) have
  section anchors dense enough that any subcommand, flag, or `P`-bag key is
  one in-page jump away.
- **Inter-document.** `README.md` is the hub and links to every user- and
  developer-facing document; each document links back to `README.md` and
  sideways to its natural neighbors (usage ↔ configuration, developer ↔ the
  two design docs ↔ the test plan, `src/README.md` ↔ `src/fim/API.md`). The
  generated `src/fim/API.md` is reachable from `src/README.md` and
  `doc/developer.md`, and links back.
- **Link checks.** `dev/bin/check-doc-links`
  validates that every relative link and in-page anchor across all Markdown
  resolves to an existing file/heading, and flags orphan documents (nothing
  links to them). It runs in the `build` docs stage (§7) and in CI (§5.2),
  so a broken or dangling link fails the build exactly like a stale
  `API.md` does (§8.1) — a link that rots after a rename cannot silently
  survive. The checker is offline and deterministic (§2.4); external
  `http(s)` URLs are out of its scope by design (they would make the check
  a function of the public internet, not the commit).

## 9. Test strategy summary

Full detail is in the
[detailed test plan](fim-simulator-detailed-test-plan.md); this is
the shape only. Eight test layers, mapped to where they live:

- **Unit** (`test/<module>/`) — every value object and pure function,
  including the $\sum_a p_a\approx1$ invariant and (de)serialization round-trips.
- **Property-based** (Hypothesis) — the differentiation guide's Part V
  algebraic identities over randomly generated frequency tables.
- **Golden-value** (`test/data/`) — the Part IV hand-checked scenarios
  asserted to exact values, including the documented $D=0.5556$ erratum.
- **Statistical / asymptotic** (`test/validation/`) — drift variance
  `p(1-p)/N`, and many-replicate equilibria vs. the Part VI formulas; all
  seeded, all tolerance-banded a priori (§2.4).
- **Published-scenario** (`test/validation/`) — the two Dear-Nolan
  scenarios as banded statistical oracles.
- **Functional / end-to-end** (`test/cli/`, `test/engine/`) — a real CLI
  `run` producing the documented artifacts; `stats` re-analysis; manifest
  replay.
- **Packaging smoke** (CI/`build`) — entry point and one-file exe answer
  `--version`/`--help` and run offline.
- **Repository tooling** (`test/validation/`) — the git hooks, the
  API-doc freshness gate, the link checker, and the release-notes
  extractor, each exercised against fixture repositories and fixture
  Markdown rather than the live tree (test plan §10.1).

Every layer obeys the determinism contract (§2.4): fixed seeds, bands
derived before seeds, no wall-clock, no network, order-independent.

**Not part of the deterministic gate:** a benchmark/scale-regression layer — wall-clock
and memory guards for large migration matrices, high locus counts, and
near-degenerate frequency vectors. Performance characterization is
documented separately in the
[backend benchmarks](fim-engine-backend-benchmarks.md); it does not
provide a per-commit performance gate.

**Calibration provenance.** The Statistical/asymptotic
layer's equilibrium tests band against a per-replicate spread with no
known closed form (test plan §7.1). That spread comes from
`dev/bin/calibrate-statistical-bands`, a versioned characterization
program, deliberately not wired into `build` or `ci.yml` — a
characterization pass is itself stochastic by design, so it stays out of
the deterministic gate the rest of this section describes. Its raw
output (seeds, per-replicate values, environment fingerprint) is retained
in `test/validation/statistical-calibration-evidence.json`.

## 10. Risks and mitigations

| Risk | Consequence if ignored | Mitigation |
|---|---|---|
| Non-deterministic test outcomes | Results do not identify a property of the commit | Explicit seeded streams, independently derived bands, isolated state, and no live-network assertions (§2.4, §9). |
| PyInstaller + Matplotlib bundle bloat or missing data files | A `.exe` that fails to render or is needlessly large | Exclude GUI backends, bundle only `Agg` + Matplotlib data (§6.2); a release-workflow smoke test renders offline on a clean runner (§5.4). |
| Large trajectory histories (design §6) | Storage and I/O costs | Binary logs, configurable retention, and a `TrajectoryStore` protocol that isolates additional storage implementations. |
| `d > 3` scatter illegible | The canonical output fails its own requirement | Two projection fallbacks with explicit "this is a projection" labeling (design §8). |
| Version string drift across bundle/manifest/formula | A release that misreports itself | `version.txt` is the single source; the release workflow fails on tag ≠ `version.txt` (§5.4/§6.4). |
| Generated `API.md` differs from source | Incorrect API reference | `pre-commit` regenerates; `pre-push` checks the working tree; CI verifies the checked-out reference (§8.1). |
| Git hooks bypassed with `--no-verify` | Un-gated code reaches the branch | Hooks are convenience only; CI (§5) is the authority and re-runs every gate, so a bypass is caught before merge (§8.2). |
| Limited review availability | Regressions may escape manual inspection | Documented procedures, `build` gates (§7), and local hooks (§8.2) supplement review. |
