# Maintainer scripts

- [Maintainer scripts](#maintainer-scripts)
  - [Why generated files exist at all](#why-generated-files-exist-at-all)
  - [At a glance](#at-a-glance)
  - [`benchmark-engines`](#benchmark-engines)
  - [`benchmark-queue`](#benchmark-queue)
  - [`build-examples-catalog`](#build-examples-catalog)
  - [`calibrate-auto-threshold`](#calibrate-auto-threshold)
  - [`calibrate-convergence-defaults`](#calibrate-convergence-defaults)
  - [`calibrate-statistical-bands`](#calibrate-statistical-bands)
  - [`check-doc-links`](#check-doc-links)
  - [`check-webui-assets`](#check-webui-assets)
  - [`compare-against-hierfstat`](#compare-against-hierfstat)
  - [`extract-release-notes`](#extract-release-notes)
  - [`generate-api-docs`](#generate-api-docs)
  - [`generate-heatmap-queue`](#generate-heatmap-queue)
  - [`generate-help-html`](#generate-help-html)
  - [`generate-test-docs`](#generate-test-docs)
  - [`regenerate-example-outputs`](#regenerate-example-outputs)
  - [`render-heatmap`](#render-heatmap)
  - [`update-worked-examples`](#update-worked-examples)
  - [`validate-repository`](#validate-repository)
  - [Related documents](#related-documents)

These commands generate documentation, validate repository files, calibrate
scientific tests, and measure engine performance. For running simulations,
see the [command reference](../../doc/usage.md). For setup and releases, see
the [maintainer runbook](../../CONTRIBUTING.md).

Every command supports `-h`/`--help`. Run the examples from the repository
root after sourcing `include/dot-bashrc`.

## Contents

- [Why generated files exist at all](#why-generated-files-exist-at-all)
- [At a glance](#at-a-glance)
- [`benchmark-engines`](#benchmark-engines)
- [`benchmark-queue`](#benchmark-queue)
- [`build-examples-catalog`](#build-examples-catalog)
- [`calibrate-auto-threshold`](#calibrate-auto-threshold)
- [`calibrate-convergence-defaults`](#calibrate-convergence-defaults)
- [`calibrate-statistical-bands`](#calibrate-statistical-bands)
- [`check-doc-links`](#check-doc-links)
- [`check-webui-assets`](#check-webui-assets)
- [`compare-against-hierfstat`](#compare-against-hierfstat)
- [`extract-release-notes`](#extract-release-notes)
- [`generate-api-docs`](#generate-api-docs)
- [`generate-heatmap-queue`](#generate-heatmap-queue)
- [`generate-help-html`](#generate-help-html)
- [`regenerate-example-outputs`](#regenerate-example-outputs)
- [`update-worked-examples`](#update-worked-examples)
- [`render-heatmap`](#render-heatmap)
- [`validate-repository`](#validate-repository)
- [Related documents](#related-documents)

## Why generated files exist at all

Generated files provide browsable or bundled copies of authoritative source
material:

| Generated files | Source | Generator |
|---|---|---|
| `src/fim/API.md` | Python docstrings | `generate-api-docs` |
| `src/fim/gui/webui/help/` | `doc/usage.md`, `doc/configuration.md`, `doc/statistics.md` | `generate-help-html` |
| `src/fim/gui/webui/examples/` | `doc/examples/` | `build-examples-catalog` |
| Marked YAML blocks in `doc/usage.md` | `doc/examples/<example>/config.yaml` | `update-worked-examples` |

Edit the source, then regenerate. Hand edits to generated output are
overwritten. Freshness tests compare committed output with regenerated files;
Git hooks refresh affected files automatically.

## At a glance

| Command | Purpose |
|---|---|
| [`benchmark-engines`](#benchmark-engines) | Times how long each `engine_backend` choice takes as one setting (deme count, population size, mutation/migration rate, locus length) sweeps across a range, so a choice between them can be made from evidence instead of a guess |
| [`benchmark-queue`](#benchmark-queue) | Runs a list of `benchmark-engines` (or any other) commands one after another, each one waiting for the machine to be quiet first, so several sweeps can be queued up and left running for hours without their own timing numbers contaminating each other |
| [`build-examples-catalog`](#build-examples-catalog) | Rebuilds the desktop app's bundled list of examples (`src/fim/gui/webui/examples/`) from `doc/examples/`, so the Examples dialog shows exactly the examples, classes, and explanations the repository holds |
| [`calibrate-auto-threshold`](#calibrate-auto-threshold) | Measures, on your own machine, the deme count above which `engine_backend: auto` should switch engines — the shipped default was measured on a different machine and this project's own history has already found it can go stale |
| [`calibrate-convergence-defaults`](#calibrate-convergence-defaults) | Measures, with fixed seeds, how well the burn-in-then-average convergence rule lands on its target and how honest its error bar is, and how fast the allele-spectrum statistics settle next to `D` |
| [`calibrate-statistical-bands`](#calibrate-statistical-bands) | Re-measures how much random variation is normal for the three published-science validation scenarios, so the tests that check the simulator against them use an honest, evidence-based tolerance |
| [`check-doc-links`](#check-doc-links) | Confirms every link between documentation pages actually goes somewhere, and that no page is orphaned with nothing linking to it |
| [`check-webui-assets`](#check-webui-assets) | Confirms the desktop app's styles, links, and icons still line up with each other -- no styling rule nothing uses, no class nothing styles, no link or icon pointing at something that is not there |
| [`compare-against-hierfstat`](#compare-against-hierfstat) | Runs an independently-written simulator (hierfstat, an R package, inside Docker) alongside this project's own, and prints how closely the two agree |
| [`extract-release-notes`](#extract-release-notes) | Pulls one version's own section out of `CHANGELOG.md`, for GitHub's release page |
| [`generate-api-docs`](#generate-api-docs) | Rebuilds the generated API reference (`src/fim/API.md`) from the code's own docstrings |
| [`generate-heatmap-queue`](#generate-heatmap-queue) | Writes a `benchmark-queue` file that measures every combination of deme count and locus length at once, to check whether the engines' own speed crossover is really a simple rectangle in that two-setting space |
| [`generate-help-html`](#generate-help-html) | Rebuilds the desktop app's in-app Help screen content from `doc/usage.md`/`doc/configuration.md` |
| [`generate-test-docs`](#generate-test-docs) | Rebuilds the test-suite reference (`test/TESTS.md`) from test docstrings |
| [`regenerate-example-outputs`](#regenerate-example-outputs) | Reruns the worked examples under `doc/examples/` and replaces their committed result files, so the results the desktop app ships with are exactly what the current code produces |
| [`render-heatmap`](#render-heatmap) | Turns `generate-heatmap-queue`'s own results into two readable grids: which engine won at each combination, and by how much |
| [`update-worked-examples`](#update-worked-examples) | Keeps the usage guide's inline worked-example YAML synchronized with canonical configs under `doc/examples/<example>/config.yaml` |
| [`validate-repository`](#validate-repository) | Runs every repository-hygiene checker (shell scripts, YAML, Markdown, JavaScript, CSS, HTML, leaked secrets) over the whole checkout |

Every command supports `-h`/`--help` for the same explanation you are
reading now, plus its exact command-line syntax.

## `benchmark-engines`

Times `lineal` at the selected batch size, `generational` with and without
its optional speed-up setting, and `generational-vector`. Results include
seconds and ratios relative to a single-lineage `lineal` run at the same
setting. See [engine backends](../../doc/developer.md#engine-backends) for
implementation details and [benchmark results](../../doc/fim-engine-backend-benchmarks.md)
for recorded measurements.

**How it relates to `fim sweep`:** The benchmark shares the sweep layer's axes
and value checks (`fim.sweep.expand_axis`, so `--values` must be whole numbers
for a count and within the axis's legal range), and `generate-heatmap-queue`
takes its grid from `fim.sweep.enumerate_values`. It does not share
execution: a benchmark runs points one at a time in fresh subprocesses on a
quiet machine, takes medians of timed trials and discards its results, which is
the opposite of a sweep's concurrent, stored points. (`N` is the benchmark's
gene copies per deme, the number it has always meant; a sweep's `N` is
individuals.)

**Why it matters:** This project offers a choice between three engines
that trade determinism, speed, and memory use against each other in
ways that are not obvious from reading the code alone (see the
[developer guide](../../doc/developer.md) for what each one actually
does differently) -- and the right choice genuinely depends on how big
a simulation you are running, not on a fixed rule. `engine_backend=
"auto"` already picks between two of them using a threshold
(`auto_vector_min_d`) that was measured, once, on one specific machine;
this script is how that measurement gets made in the first place, and
how it gets checked again later if the underlying code changes in a way
that might move it (this project's own history already has one example:
a correctness fix to how randomness is drawn turned out to also change
how much benefit multi-threading provides -- exactly the kind of thing
this script is meant to catch, by measuring directly rather than
trusting an old number).

**When to run it:** Whenever you want real evidence for how the engines
compare at a scale you actually care about, or whenever you suspect a
recent code change might have shifted that balance. Not run
automatically by any test, build, or release step -- like
`calibrate-statistical-bands` below, a benchmark that ran on every push
would make timing-sensitive CI results depend on how busy the CI
runner happened to be that day, not on the code. Run it on an otherwise
idle machine for a trustworthy result (a browser doing something in the
background, another simulation running at the same time, or a laptop on
battery-saving mode can all skew the numbers).

**Usage:**

```console
dev/bin/benchmark-engines --sweep d --values 4,10,20,35,50,80,120 \
    --replicates 16 --generations 100 --trials 3 \
    --output benchmark-d-sweep.json
```

- `--sweep`: `d`, `N`, `mu`, `m`, or `loci-length`.
- `--values`: comma-separated values of the swept setting.
- `--replicates` and `--generations`: size each simulation.
- `--trials`: repetitions; the reported time is their median.
- `--output`: optional JSON evidence with results and machine/time metadata.
  Without it, only the table is printed. Timing files are local evidence,
  not committed artifacts; record durable findings in a benchmark report.
- `--config`: use a `fim` YAML configuration for nonswept settings.
  It must specify one locus and scalar `N`, `m`, and `mu`. Without this
  option, built-in reference-scale values apply.

## `benchmark-queue`

Runs stages sequentially. Commands within a stage run concurrently; the next
stage waits for all of them to finish. Before each stage, the default idle
check polls every 30 seconds for up to an hour, then proceeds with a warning
if load remains high.

Use one command per stage for exclusive machine use. Concurrent commands can
distort timings, especially when an engine uses all available cores. Use
concurrent stages only where the machine has sufficient spare capacity.
Each command's output is logged as it runs; `summary.json` is updated after
each stage.

```console
dev/bin/benchmark-queue my-sweeps.json --log-dir benchmark-logs
```

Example queue:

```json
{
  "settings": {
    "idle_load_fraction": 2.0
  },
  "stages": [
    [
      {"label": "d10-len4", "command": ["dev/bin/benchmark-engines",
        "--sweep", "d", "--values", "10", "--config", "grid.yaml"]},
      {"label": "d10-len5", "command": ["dev/bin/benchmark-engines",
        "--sweep", "d", "--values", "10", "--config", "grid-len5.yaml"]}
    ],
    [
      {
        "label": "d-fine-grid",
        "command": ["dev/bin/benchmark-engines", "--sweep", "d",
                    "--values", "10,35,70,120", "--replicates", "16",
                    "--generations", "100", "--trials", "3"]
      }
    ]
  ]
}
```

`label` must be unique across the file. It names `<log-dir>/<label>.log`
and the command's summary entry. Optional per-command `timeout_seconds`
overrides `settings.default_timeout_seconds` (default: four hours).

A failure or timeout does not stop later stages unless
`settings.stop_on_failure` or `--stop-on-failure` is set. A running stage
always finishes before the queue stops.

All `settings` entries are optional. `idle_load_fraction` is the allowed
one-minute load average divided by CPU count; the default is `0.1`.
Choose a threshold for the machine rather than assuming the example's `2.0`
is appropriate. Keep separate queue files for different machine profiles.
Command-line options override file settings:
`--idle-load-fraction`, `--idle-poll-seconds`, `--idle-max-wait-seconds`,
`--default-timeout-seconds`, and `--stop-on-failure`.

## `build-examples-catalog`

Reads example directories containing `README.md` under `doc/examples/` and
the class list in `doc/examples/classes.yaml`. Writes
`src/fim/gui/webui/examples/catalog.json` and copies committed result
artifacts, including kept replicates and compressed JSONL parts.

The packaged app contains `webui/`, not `doc/`, so the bundle must be rebuilt
when example content changes. Metadata comes from `config.yaml` when present;
otherwise the first README heading supplies the name, its first paragraph
the description, and the class is “Unclassified” (listed last).
Without `classes.yaml`, all examples must be unclassified. Unknown classes,
empty classes, and malformed class lists fail the build.

```console
dev/bin/build-examples-catalog
dev/bin/build-examples-catalog --check
```

The default refreshes the bundle and removes unlisted files. `--check`
reports stale files without changes. The pre-commit hook runs the generator
when `doc/examples/`, `doc/usage.md` (example ordering), or the generator is
staged. `test/test_examples_catalog.py` checks freshness.

## `calibrate-auto-threshold`

Sweeps deme count using the `benchmark-engines` measurement code and compares
`generational` with `generational-vector`. Reports the smallest tested `d`
above which the vector backend consistently wins.

The shipped `auto_vector_min_d: 2` is a measured default, not a
hardware-independent boundary. Recalibrate on the target machine or after
performance changes. If the vector backend wins throughout the tested range,
the script reports no crossover and suggests testing smaller values.
This timing-dependent command is not an automated gate.

```console
dev/bin/calibrate-auto-threshold \
    --d-values 4,10,20,30,40,60,80,120 --replicates 16 \
    --generations 100 --trials 3 \
    --output auto-vector-min-d-evidence.json
```

All options have defaults listed by `--help`. Apply a measured threshold by
setting `auto_vector_min_d` in a configuration; the script changes no
configuration or default. Optional `--output` records local evidence, not
input loaded automatically by `simulate_params()`.

## `calibrate-convergence-defaults`

Measures convergence against known targets:

- `rule REGIME`: 12 seeded single runs; counts misses beyond the requested
  precision and beyond 1.96 reported standard errors.
- `spectrum REGIME`: measures allele-spectrum relaxation times alongside `D`.

Regimes include a fast island model, Golden Part VI, and a six-deme ring.
Each command prints a JSON object, recorded under the matching key in
`test/validation/convergence-rule-calibration-evidence.json`.
See [convergence](../../doc/convergence.md) for the stopping rule and its
interpretation.

Run after changing convergence, burn-in derivation, or regime parameters.
Fixed seeds make the evidence reproducible; tests may call these functions.
Typical `rule` runs take several minutes, up to about ten for `ring` with
four workers.

```console
dev/bin/calibrate-convergence-defaults rule golden-part-vi --loci 4 --jobs 4
dev/bin/calibrate-convergence-defaults spectrum fast --loci 4 --jobs 4
dev/bin/calibrate-convergence-defaults --list
```

## `calibrate-statistical-bands`

Runs the three [scientific validation scenarios](../../doc/examples/README.md)
with multiple seeds and records their variation in
`test/validation/statistical-calibration-evidence.json`. Tests use this
committed evidence to set tolerance bands around theoretical predictions.
The bands must accommodate sampling variation without hiding model errors.

Recalibrate when scenario parameters change enough to invalidate the evidence.
The script is a manual procedure, not a test/build/release step; review and
commit the resulting evidence as a separate, documented change.

```console
dev/bin/calibrate-statistical-bands \
    --replicates-part-vi 30 --replicates-dear-nolan-low 40 \
    --replicates-dear-nolan-high 20 \
    --output test/validation/statistical-calibration-evidence.json
```

Defaults are listed by `--help`; without arguments the script writes the
committed evidence path. Higher replicate counts improve precision at
additional runtime cost.

## `check-doc-links`

Checks local file targets, heading anchors, and orphan pages across repository
Markdown. It does not require Docker.

```console
dev/bin/check-doc-links
```

Run after adding, moving, renaming, or deleting pages or linked headings.
It also runs in `./build --ci` and the pre-push hook. It takes no arguments,
locates the repository from any working directory, prints each problem, and
exits nonzero on failure; otherwise it prints a confirmation.

## `check-webui-assets`

Checks references across the desktop app's HTML, CSS, and JavaScript:
reachable styling rules, class names with styles or script uses, local file
and link targets, and icon names. Generated-help classes have named
exceptions with explanations in the script.

```console
dev/bin/check-webui-assets
```

Run after web asset changes, particularly deletions. The check complements
per-file linters and runs in `./build` after help generation.
It takes no arguments, locates the app from any working directory, prints
each problem, and exits nonzero on failure. No Docker is required.

## `compare-against-hierfstat`

Builds a Docker image with
[hierfstat](https://cran.r-project.org/package=hierfstat), an independent R
implementation of migration, drift, and mutation. It simulates a reference
scenario, computes `fim` statistics (`H_S`, `H_T`, `G_ST`, `D`) from
hierfstat's genotypes, and prints them beside theoretical predictions.

The comparison uses genotypes rather than hierfstat's summary statistics,
which apply sampling corrections inappropriate to this comparison.
See [migration conventions](../../doc/migration-conventions.md) and
[differentiation measures](../../doc/jost-differentiation-measures.md)
before interpreting differences.

```console
dev/bin/compare-against-hierfstat \
    --population-size 100 --m 0.01 --mu 0.005 --d 4 \
    --generations 4000 --nbal 99 --nbloc 200 --seed 900001
```

All flags have reference-scenario defaults. `--skip-build` reuses the image.
Docker is required; the initial build takes several minutes. This manual
comparison has no established pass/fail tolerance and is not an automated
gate; `--help` describes that limitation.

## `extract-release-notes`

Prints a selected version's `CHANGELOG.md` section without its heading or
surrounding sections. The release workflow uses it for the GitHub Release
description. Run manually to preview notes.

```console
dev/bin/extract-release-notes v1.2.0
dev/bin/extract-release-notes 1.2.0 --changelog path/to/CHANGELOG.md
```

The version accepts an optional leading `v`. `--changelog` overrides the
default `CHANGELOG.md` in the current directory.

## `generate-api-docs`

**What it does:** Rebuilds `src/fim/API.md`, the committed reference
that lists every public part of the simulator's own code (`fim` Python
package) -- every module, class, and function -- together with its
docstring (the explanation written directly above it in the source
code).

Module, class, function, method, and data headings link to their Python
source using GitHub-relative line anchors. Modules link to line 1;
decorated declarations link to the `class`, `def`, or `async def` line,
not the first decorator. Existing section anchors and table-of-contents
links remain unchanged.

**Why it matters:** Anyone who wants to know exactly what a function
does or what values it expects should be able to look that up on GitHub
without installing Python or opening a code editor -- that is what
`src/fim/API.md` is for. Its content comes directly from the same
docstrings a developer reads in the source file itself, which is the
whole point: the reference can never say something *different* from
what the code's own documentation already says, because it is not a
separate description written by hand -- it is that same description,
copied out automatically. It can only ever become *stale* (showing an
older version of a docstring that has since been improved), which this
script fixes immediately by simply being re-run. See "[Why generated
files exist at all](#why-generated-files-exist-at-all)" above for why
`src/fim/API.md` itself should never be hand-edited even to fix a small
mistake in it -- the real fix is always to correct the docstring in the
actual source file and regenerate.

**When to run it:** You rarely need to remember this yourself. The
`pre-commit` git hook already runs it automatically -- and re-includes
its output in your commit -- whenever a change you are committing
touches any file under `src/fim/`. `pre-push` and `./build --ci`
separately double-check that the committed file still matches what
running this script again right now would produce, in case a commit was
somehow made without that hook running (for instance, from a tool that
bypasses Git hooks entirely).

**Usage:**

```console
dev/bin/generate-api-docs
dev/bin/generate-api-docs scratch-api.md
```

With no arguments, overwrites the real, committed `src/fim/API.md`. A
path argument writes there instead, without touching the committed
file. Source and navigation links are relative to the output's directory,
including when the output is outside the repository. Freshness checks
therefore use a temporary file beside the committed reference so their
relative links are identical, without overwriting the reference.

## `generate-test-docs`

**What it does:** Rebuilds [`test/TESTS.md`](../../test/TESTS.md) from
test-module, class, fixture, helper, and test-function docstrings.
Like `generate-api-docs`, headings link to source declaration lines,
including methods of nested classes and decorated tests. Module links
start at line 1, and existing qualified section anchors stay unchanged.

**When to run it:** After changing Python files under `test/` or this
generator. The pre-commit hook refreshes it for staged test changes;
pre-push and `./build --ci` check freshness.

**Usage:**

```console
dev/bin/generate-test-docs
dev/bin/generate-test-docs /tmp/scratch-tests.md
```

An optional path writes a separate reference with links relative to that
location. Freshness checks render beside the committed reference, as for
`generate-api-docs`.

See the [Python guide](../../doc/python-api.md) for runnable examples and
the [functional API contract](../../doc/fim-simulator-functional-api.md)
for supported imports.

## `generate-heatmap-queue`

Writes a `benchmark-queue` file that measures every combination of selected
deme counts and locus lengths. The grid tests whether the two thresholds
used by `engine_backend: auto` (`auto_vector_min_d` and
`auto_vector_max_capacity`) approximate the measured backend crossover.

```console
dev/bin/generate-heatmap-queue \
    --d-values 10,35,70,120,300 --length-values 5,6,7,8 \
    --out-dir fim-heatmap
dev/bin/benchmark-queue fim-heatmap/queue.json \
    --log-dir fim-heatmap/logs
dev/bin/render-heatmap fim-heatmap
```

- `--d-values` and `--length-values`: comma-separated grid axes.
- `--fixed-N`, `--fixed-m`, `--fixed-mu`: settings held constant, with
  defaults matching `benchmark-engines`.
- `--replicates`, `--generations`, `--trials`: size and repeat measurements.
- `--out-dir`: configurations, queue, and subsequent result files.

Locus-length measurements share a concurrent stage. Ensure sufficient
capacity to avoid timing interference; see [`benchmark-queue`](#benchmark-queue).
Start with a coarse grid, then refine near the measured boundary.

## `generate-help-html`

Converts `doc/usage.md` and `doc/configuration.md` into offline desktop
help under `src/fim/gui/webui/help/`, together with `doc/statistics.md`.
First synchronizes usage-guide YAML
through [`update-worked-examples`](#update-worked-examples).

```console
dev/bin/generate-help-html
dev/bin/generate-help-html --output-dir scratch-help
```

The default overwrites committed HTML; `--output-dir` writes elsewhere.
Edit the Markdown sources, not generated HTML. The pre-commit hook refreshes
help when either guide changes; the pre-push hook and `./build --ci` check
freshness.

Inline TeX and fenced `math` equations are converted to native MathML by
the pinned development dependency `latex2mathml`. Equation tags appear
beside display equations. The bundled pages need no network connection,
JavaScript math library, or separately downloaded fonts. Unsupported
TeX commands stop generation instead of appearing as raw markup.
Statistic-key headings remain unchanged so desktop help links keep working.

## `regenerate-example-outputs`

Runs examples with `config.yaml` under `doc/examples/`, then replaces
their committed outputs. The invocation is
`python -m fim.launcher run doc/examples/<id>/config.yaml --output <staging-directory> --quiet`.
A failed run leaves committed artifacts unchanged.

Outputs include single-run artifacts or the batch manifest, summary, and
kept replicates. Trajectory, convergence, and sigma-band JSONL are losslessly
gzip-compressed into parts of at most 50 MiB. Pairwise matrices and scatter
plots are retained. Opening an example restores JSONL and verifies manifest
digests; manual unpacking or rerunning is unnecessary.

Run after configuration, engine, statistic, or report-format changes when
the slow replay test (`test/test_doc_examples.py`) reports a mismatch.
Examples may take about an hour (Dear-Nolan low) or a quarter-hour (Jost Part
VI), depending on backend and hardware. Bound load on shared machines.

```console
dev/bin/regenerate-example-outputs                      # all examples
dev/bin/regenerate-example-outputs golden-part-vi       # one example
dev/bin/regenerate-example-outputs --list
dev/bin/regenerate-example-outputs --dry-run
dev/bin/regenerate-example-outputs --jobs 3 --workers 2
```

`--jobs` bounds concurrent examples. `--workers` sets `lineal` batch worker
processes and is not passed to other engines. Neither changes results.
Commit outputs with the rebuilt examples bundle staged by the hook.

## `update-worked-examples`

Copies each `doc/examples/<example>/config.yaml` into its marked YAML block
in `doc/usage.md`. The pre-commit hook refreshes the guide when a
configuration or the guide changes. Tests verify each configuration is
included once.

```console
dev/bin/update-worked-examples
dev/bin/update-worked-examples --check
```

The default updates the guide; `--check` reports stale or unreferenced
configurations without changing files.

## `render-heatmap`

Reads `result-len*.json` files produced by the generated benchmark queue.
Prints grids with deme count down rows and locus length across columns:
the winning engine and the ratio of `generational-vector` time to
`generational` time with JIT enabled. Ratios below 1 favor the vector engine.

```console
dev/bin/render-heatmap fim-heatmap
```

Pass the directory supplied to `generate-heatmap-queue --out-dir`.
Run after the queue finishes; missing results are reported rather than
rendered as an empty grid.

## `validate-repository`

Runs pinned Docker-backed tools across the repository:

- ShellCheck: shell scripts
- yamllint: YAML, including GitHub Actions and Dependabot
- markdownlint: Markdown formatting
- ESLint, Stylelint, HTMLHint: desktop JavaScript, CSS, and HTML
- gitleaks: credential-like content in the complete commit history

```console
dev/bin/validate-repository
```

Requires a running Docker daemon. Images are
downloaded on first use and cached. No host linter installation is required.
This command is separate from `./build`, which does not require Docker.
The Docker-free [`check-webui-assets`](#check-webui-assets) runs in the build.

By default, credential scanning audits all Git refs, including local agent
checkpoints. The pre-push hook supplies `--gitleaks-log-opts` with outgoing
commit revisions so an ordinary push does not rescan unrelated history.
Other repository checks still cover the full checkout. For an explicit range:

```console
dev/bin/validate-repository --gitleaks-log-opts 'origin/dev..HEAD'
```

| Automatic execution | Conditions |
|---|---|
| Pre-push hook | Every push unless Docker is unavailable or `PRE_PUSH_SKIP_VALIDATE=true` |
| CI `lint-assets` | Pushes and pull requests to `staging`/`main`, and release tags |

`lint-assets` does not run on `dev`. CI validation covers pushes made without
local checks. Credential scanning additionally runs on every branch in
`.github/workflows/gitleaks-ci.yml`. Treat a published credential as
compromised and rotate it; deleting it from the working tree is insufficient.

## Related documents

- [Maintainer runbook](../../CONTRIBUTING.md)
- [Repository-managed hooks](../git-hooks/README.md)
- [Developer and extension guide](../../doc/developer.md)
- [Scientific test plan](../../doc/fim-simulator-test-plan.md)
- [Source-tree orientation](../../src/README.md)
- [Project overview](../../README.md)
