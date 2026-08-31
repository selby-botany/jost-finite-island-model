<!-- markdownlint-disable MD013 -->

# Desktop GUI design

- [Desktop GUI design](#desktop-gui-design)
  - [Who this document is for](#who-this-document-is-for)
  - [1. What the GUI is](#1-what-the-gui-is)
  - [2. Why pywebview, not Tk](#2-why-pywebview-not-tk)
  - [3. Architecture overview](#3-architecture-overview)
  - [4. The `Api` bridge](#4-the-api-bridge)
    - [4.1 The `evaluate_js`/Promise pattern](#41-the-evaluate_jspromise-pattern)
    - [4.2 Bridge method surface](#42-bridge-method-surface)
  - [5. The unified run view](#5-the-unified-run-view)
    - [5.1 From six screens to one view](#51-from-six-screens-to-one-view)
    - [5.2 States: initial, running, completed](#52-states-initial-running-completed)
  - [6. The configuration form](#6-the-configuration-form)
    - [6.1 Tabs and the cardinality rule](#61-tabs-and-the-cardinality-rule)
    - [6.2 Load-only badges and unrepresentable constructs](#62-load-only-badges-and-unrepresentable-constructs)
  - [7. Run orchestration](#7-run-orchestration)
    - [7.1 Scalar runs](#71-scalar-runs)
    - [7.2 Batch runs](#72-batch-runs)
    - [7.3 Shared output-directory semantics with the CLI](#73-shared-output-directory-semantics-with-the-cli)
  - [8. Animation](#8-animation)
  - [9. Recent runs and opening a persisted run](#9-recent-runs-and-opening-a-persisted-run)
  - [10. Native menus](#10-native-menus)
  - [11. In-app help](#11-in-app-help)
  - [12. Shared logic with the CLI](#12-shared-logic-with-the-cli)
  - [13. Milestone glossary](#13-milestone-glossary)
  - [14. Testing](#14-testing)
  - [15. Packaging](#15-packaging)
  - [Metadata](#metadata)

## Who this document is for

Anyone maintaining or extending `fim.gui` — the desktop application.
The companion [design document](fim-simulator-design.md) and its own
[engineering reference](fim-simulator-detailed-design.md) cover the
simulator core and its release process; this document covers the
desktop front end specifically: why it is built the way it is, how its
pieces fit together, and where each responsibility lives. Test detail
is delegated to [`doc/fim-gui-test-plan.md`](fim-gui-test-plan.md)
(§14 here is only a pointer).

## 1. What the GUI is

`fim.gui` is a [pywebview](https://pywebview.flowrl.com/)-based desktop
front end: a static local `webui/` page (plain HTML/CSS/JS, no build
step, no framework) rendered inside a native window (WKWebView on
macOS, WebView2 on Windows, WebKitGTK on Linux), driven entirely
through one bridge class, `fim.gui.app.Api` (§4), that the JS side
calls into and gets plain JSON-serializable values back from. Every
screen calls `fim.engine.fim`, `fim.viz.scatter`, and
`fim.model.params.SimulationParams` — the same public API `fim.cli`
already uses (see
[`doc/fim-simulator-functional-api.md`](fim-simulator-functional-api.md))
— and never duplicates validation, statistics, or run orchestration of
its own. [`doc/developer.md`](developer.md)'s own architecture table
states that rule directly: "GUI: call `fim.engine.fim`; do not
duplicate model logic."

`fim.launcher` dispatches to `fim.gui.app.main` for the zero-argument
and `--graphical` invocations, exactly as it dispatches to `fim.cli`
otherwise — the GUI is one more front end over the same engine, not a
parallel implementation.

## 2. Why pywebview, not Tk

The GUI was originally built on Tk (Python's bundled toolkit) and later
migrated to pywebview. The reasons the migration settled on pywebview
rather than staying on Tk:

- **A real rendering engine for the visualizations that matter most.**
  The scatter plots this project's own reference visualization is
  built around (Lou Jost's `Dear-NolanMarch17Final.pdf` Figs. 1-2) are
  naturally suited to Canvas-based client-side rendering — smooth
  scrubbing/animation, coincidence-count marker scaling, and
  common/rare coloring are all cheap in a browser engine and awkward to
  reproduce well in Tk's own canvas widget.
- **One shared visual language with the rest of the project's own
  documentation**, which is already HTML/Markdown-based (`doc/`,
  `src/fim/API.md`, `test/TESTS.md`) — the in-app help screen (§11)
  reuses the exact same generated fragments a browser or GitHub already
  renders, rather than a second, Tk-specific help representation.
- **No new runtime dependency for the common case.** pywebview embeds
  the platform's own native web view (WKWebView, WebView2, WebKitGTK)
  rather than bundling a browser engine — the same "no heavy toolkit
  in the one-file executable" constraint (detailed design §2.1) that
  ruled out a full Electron-style front end in the first place.

The migration kept every screen's own responsibility and every
bridge-visible behavior unchanged; only the rendering technology
underneath moved. `fim.gui.store.RunCancelledError` (§7) is named that,
not the Tk-era prototype's `RunCancelled`, purely because ruff's `N818`
(exception names end in `Error`) is part of this project's lint gate —
a naming correction, not a behavior change.

## 3. Architecture overview

| Module | Responsibility |
|---|---|
| `fim.launcher` | Dispatches to `fim.gui.app.main` (zero-argument, `--graphical`) or `fim.cli.main` otherwise. Does not change across front ends. |
| `fim.gui.app` | pywebview bootstrap, native menu construction, and the `Api` bridge class (§4) — the GUI's own entry point. |
| `fim.gui.config_form` | Pure, pywebview-free marshaling between `SimulationParams`, the form's `dict[str, str]`, and `SimulationParams.from_mapping`'s payload shape (§6). |
| `fim.gui.runner` | Background-thread orchestration for a single scalar run (§7.1). |
| `fim.gui.batch_runner` | Background-thread orchestration for a multi-replicate batch, using real OS worker processes (§7.2). |
| `fim.gui.store` | `GuiProgressStore`/`LiveProgressStore` — the two `TrajectoryStore`-decorating progress/cancellation mechanisms §7.1/§7.2 use. |
| `fim.gui.animation` | Samples a persisted trajectory into raw scatter coordinates for client-side playback (§8). |
| `fim.gui.recent_runs` | Scans `results/` for completed scalar/batch runs (§9). |
| `webui/` | The static page itself: `index.html`, `app.js`, `app.css`, and `screens/*.js` — no build step, no framework. |

Four modules that started as CLI-private helpers were extracted into
shared, public homes specifically so the GUI and the CLI run the exact
same logic rather than two independently maintained copies of it:
`fim.paths` (output-directory naming and atomic publish, out of
`fim.cli`'s own `_atomic_directory`), `fim.update` (the GitHub Releases
version check), `fim.reanalyze` (re-analyzing a persisted trajectory,
out of `fim.cli`'s `reanalyze_trajectory`), and `fim.persistence.report`
(`write_report`, out of `fim.cli`'s private `_write_json`). §12 covers
this in more detail.

## 4. The `Api` bridge

### 4.1 The `evaluate_js`/Promise pattern

`window.evaluate_js(...)` returns the *raw* value of whatever JS
expression it evaluates — never the resolved value of a `Promise` that
expression happens to produce. A `js_api` call such as
`window.pywebview.api.ping()` returns a `Promise` to JS, so a direct
`evaluate_js("window.pywebview.api.ping()")` reads back `{}`
(Chromium's own JSON view of an unresolved `Promise` object), not the
call's actual result. Every real call into the bridge — from
`webui/*.js`, and from a headless test alike — therefore goes through
a small `async` JS wrapper that `await`s the `js_api` call and writes
its result into the DOM (or, for a test, a `window`-scoped variable),
read back with a second, separate `evaluate_js` call. This is not a
workaround bolted on for testing: it is the correct, natural shape for
a real UI event handler too, which never needs to return a value to
Python either — only update its own screen once the `await` resolves.

`create_window` and `main` are deliberately separate: `main` blocks
(`webview.start()` runs the GUI's own event loop until the window
closes), so nothing calls it directly except a real launch. Every
headless test instead calls `create_window()` itself, then drives the
result with its own `webview.start(callback)`.

### 4.2 Bridge method surface

Every method below is a plain Python method on `Api`, called from JS as
`window.pywebview.api.<name>(...)` and returning a JSON-serializable
value (never a raw exception — validation and engine errors come back
as a structured `{"error": ...}` payload the page renders).

| Method | Purpose |
|---|---|
| `start_run` | Validate the form, then dispatch to a scalar or batch run depending on `n_replicates`. |
| `cancel_run` | Signal cancellation to whichever run is in flight (§7). |
| `open_output_folder` | Open a completed run's output directory in the OS file browser. |
| `get_starter_form` | Return `starter_form_values()` — the config form's own default values. |
| `validate_form` | Validate form values without starting a run (live field-level feedback). |
| `get_initial_state_panels` / `get_initial_state_deme_pair_panel` | The `p_0` scatter shown before a run starts (§5.2). |
| `load_yaml` / `save_yaml` | The File menu's "Open/Save configuration" actions. |
| `get_default_max_workers` | The batch tab's own default worker-count suggestion. |
| `get_significant_digits` / `set_significant_digits` | The View menu's numeric-precision setting. |
| `get_live_deme_pair` / `set_live_deme_pair` | The deme-pair selector for a `d > 3` scatter (matching `fim.viz.scatter`'s own large-`d` fallback). |
| `list_recent_runs` | Screen 6's/File-menu's recent-runs list (§9). |
| `browse_for_trajectory` / `open_run` | Open an arbitrary persisted run by file dialog. |
| `get_animation_frames` / `get_animation_deme_pair_frames` | Sampled scatter coordinates for the scrubber (§8). |
| `get_deme_pair_panel` / `get_batch_deme_pair_panel` | Re-render a completed run's scatter at a different deme pair. |
| `ping` / `ping_from_worker` | The walking-skeleton round-trip proof, in-process and cross-process. |
| `open_external_link` | Opens a URL in the OS default browser (Help menu, About). |
| `check_for_updates` | The GUI's own "Check for updates" action, via `fim.update` (§12). |
| `get_about_info` | Version string and repository/documentation links for the About dialog. |

## 5. The unified run view

### 5.1 From six screens to one view

The GUI originally had six separate screens: model input, running,
scalar results, batch results, animation, and open/recent runs. The
running, results, batch-results, and animation screens were later
consolidated into one **unified run view** — a single page
(`run-view-initial.js`, `run-view-running.js`, `run-view-completed.js`,
`run-view-controls.js`) that renders differently depending on the
run's own state, rather than four separate screen files the app
navigates between. The scrubber (`scrubber.js`), previously its own
fifth screen reached only by a separate "Animate" button, is folded
directly into the `completed` state instead: the one time slider simply
keeps existing across scalar and batch results, rather than requiring
a dedicated screen and button to reach it.

Configuration input and the open/recent-runs picker remain their own
areas (the Configure menu's modals and value-selectors, and the File
menu's "Open run…" action, respectively) — only the run-in-progress and
run-completed states were unified, since those four screens shared
almost all of their own rendering logic (the same scatter canvas, the
same six named statistics, the same deme-pair selector) and differed
mainly in which state a given run happened to be in.

### 5.2 States: initial, running, completed

- **`initial`** (`run-view-initial.js`) — shown as soon as the form has
  valid values, before any run starts: the `p_0` scatter, axis labels,
  the six named statistics, and a generation-0 progress bar, so the
  canvas is never blank at startup or after a form reset. Backed by
  `Api.get_initial_state_panels`.
- **`running`** (`run-view-running.js`) — a live scalar or batch run in
  progress, polling the bridge for progress and redrawing the scatter
  as new generations arrive.
- **`completed`** (`run-view-completed.js`) — `enterCompletedState
  (payload, isBatch)` is the one shared entry point every caller uses:
  a live scalar run finishing, a live batch finishing, and opening a
  persisted run for re-analysis (always scalar) alike, branching
  internally on `isBatch` rather than being two unrelated code paths —
  "one state model, not two." Every statistic arrives already
  formatted server-side (matching `cli._format_optional`), except
  `Differentiation_q`, which is never server-formatted and appears only
  on a batch's own confidence-interval display.

Every classic (non-module) script on this page shares one global
scope: a `const` declared in one `screens/*.js` file is visible by bare
name in every other, so a name declared in two of them would be a
`SyntaxError`, not a shadow. `test_webui_global_scope.py` is a static
guard against exactly that collision, and `run-view-initial.js` (loaded
first among the three state files) is the deliberate single owner of
every element reference the other two also need.

## 6. The configuration form

### 6.1 Tabs and the cardinality rule

`fim.gui.config_form` is a pure, pywebview-free set of functions
marshaling `SimulationParams` to and from the tabbed model-input
screen's own `dict[str, str]` of one string per field, and from there
to a `dict[str, object]` payload ready for
`SimulationParams.from_mapping` — the identical validator `fim.cli`
already uses. Nothing in this module duplicates a validation rule
`from_mapping` already enforces: a malformed string is coerced to the
right Python type before being handed to that one validator, never
re-checked against a second, GUI-local copy of a rule.

Six tabs, grouped the same way
[`doc/configuration.md`](configuration.md)'s own section headings do:
**Population**, **Migration**, **Mutation**, **Initial conditions**,
**Convergence**, and **Batch**.

The cardinality rule decides what earns a live widget at all: `O(1)`
and `O(d)`/`O(loci)`-sized fields do (a comma-separated text field
faithfully represents either); a `d`-by-`d` migration matrix, an
arbitrary sparse migration map, a per-locus `p_0`, a genuinely
per-locus `mu`, or a `loci` list with custom `locus_id`s do not.

### 6.2 Load-only badges and unrepresentable constructs

A loaded configuration that actually uses one of the widget-unfriendly
constructs above is handled one of two ways:

- **`m` and `p_0`** get a read-only "loaded from file" badge: the form
  displays the value but does not let it be edited through a widget.
- **A genuinely per-locus `mu`, or a `loci` list with custom
  `locus_id`s**, raise a clear `ValueError` from
  `params_to_form_values` instead — the same "edit the YAML file
  directly" pattern this form has always used for a construct it
  cannot represent at all, badge or not.

## 7. Run orchestration

### 7.1 Scalar runs

`fim.gui.runner` runs `fim.engine.fim` — a single blocking call — on a
`threading.Thread`, so a multi-thousand-generation run never freezes
the GUI's own main thread. Progress and cancellation come from
`fim.gui.store.GuiProgressStore`, a decorator around the
`TrajectoryStore` protocol that holds a `threading.Event` and a
callback closure directly — real Python objects the calling thread can
read and write, since nothing here crosses a process boundary. The
push interval is throttled to `PROGRESS_THROTTLE_INTERVAL_SECONDS`
(0.05s) so a fast-drifting run does not flood the bridge with more
updates than the page can usefully render.

### 7.2 Batch runs

`fim.gui.batch_runner` runs a multi-replicate batch in parallel, as
real OS processes, via `fim.engine.fim(..., max_workers=N,
store_factory=...)` — the same call shape `cli._command_run_batch`'s
own default (non-`--sequential`) path already makes. Progress and
cancellation for this path are entirely file-mediated
(`fim.gui.store.LiveProgressStore`), not pushed through an in-process
queue: a `threading.Event` and a callback closure cannot be pickled
across a `ProcessPoolExecutor` worker boundary, so each worker's
`LiveProgressStore` instead writes a small `.progress` sidecar file
after each generation and checks a shared cancellation file before
each write. The bridge polls every in-flight replicate's own sidecar
on a coarser cadence (`_BATCH_POLL_INTERVAL_SECONDS`, 0.5s) than the
scalar path's own in-process push interval, since each poll re-reads a
whole `trajectory.jsonl` per currently-reporting replicate — a real,
if usually small, cost that grows with replicate count and how far
each has run. A batch's terminal outcome (done, cancelled, or error)
still arrives through the same in-process message queue as before,
since that remains a single discrete event worth queueing; only
per-generation progress moved off the queue and onto the filesystem.

### 7.3 Shared output-directory semantics with the CLI

Both paths do their work inside `fim.paths.atomic_directory` — the
exact context manager `cli._command_run_scalar` and
`cli._command_run_batch` already use: every write lands in a hidden
temporary sibling of `output_directory`, published with one atomic
rename only if the `with` block exits normally. A cancelled run raises
`RunCancelledError` out of that block; an unexpected engine error
raises one of a small set of expected engine errors. Either way,
`atomic_directory`'s own exception handling discards the temporary
directory and `output_directory` is never created — no GUI-specific
cleanup code is needed for either outcome. The same four artifacts are
written, in the same order, as the CLI's own
`_write_run_artifacts`: `trajectory.jsonl` streamed generation by
generation, then `report.json` and `scatter.png` once the run
finishes, then — last, and only once both are flushed —
`manifest.json`, augmented with each artifact's SHA-256 digest.

`paths.default_output_directory()` names a directory by the current
second (`run-YYYYMMDD-HHMMSS`, UTC), deliberately unchanged from the
CLI's own pre-extraction naming — so two calls inside the same real
second collide on the identical path. This bridge owns fixing that
narrow reliability gap, not `fim.paths` itself: a user clicking "Run
simulation" again within the same second a previous attempt's
directory was created would otherwise see a confusing "output
directory already exists" error for what is, from their perspective,
an entirely fresh run. `start_run` retries at a short interval, up to
a small maximum wait, for the wall clock to cross into a new second
before giving up for real.

## 8. Animation

`fim.gui.animation.pre_render_frames` samples at most
`GUI_ANIMATION_MAX_FRAMES` (100) generations from a persisted
trajectory, evenly spaced across the run's persisted range and always
including generation 0 and the final generation. Each sampled
generation produces a plain coordinate array
(`fim.viz.scatter.frequency_points`), not a rendered Matplotlib
`Figure`: the scrubber ships the whole sampled set to the page once and
drives play/pause/scrub entirely with client-side Canvas redraws, so
nothing on this path needs to render anything at all — pre-computation
itself is cheaper too, since building coordinate arrays costs a
fraction of what building the same number of Matplotlib figures did.

Animation frames are only ever requested for a trajectory whose
integrity has already been verified — by the run that just completed
(a live run's own manifest was just written, never edited), or by
`fim.reanalyze.reanalyze_trajectory` when opening a persisted run
(which itself calls `fim.persistence.manifest.
verify_trajectory_integrity`). `pre_render_frames` therefore reads the
trajectory directly, trusting the caller, rather than re-verifying it
a second time.

## 9. Recent runs and opening a persisted run

`fim.gui.recent_runs` scans `fim.paths.results_directory()` for
`*/manifest.json`, reading each with `fim.persistence.manifest.
read_manifest` (scalar) or `read_batch_manifest` (batch) — the same
files `fim stats` and `fim run`'s own batch summary already default to.
A batch's manifest is listed but labeled distinctly (e.g. "batch
(14/20)") rather than treated as something the picker can open
directly: opening one specific replicate's own trajectory is the path
to any single replicate, since a batch-level manifest has no single
trajectory of its own to verify or re-analyze.

## 10. Native menus

Five native menus, built once in `fim.gui.app._build_menu` and stable
across the whole window's lifetime:

- **File** — New/Open/Save configuration, Open run…, Quit.
- **Configure** — the modals and value-selectors the unified run view's
  `initial`/`running` states use to change model-input values without
  leaving the current run's own view.
- **Run** — Run simulation, Cancel run.
- **View** — the significant-digits setting (2-8) every formatted
  statistic on the page uses.
- **Help** — the in-app help screen (§11), "Documentation on GitHub,"
  and About.

## 11. In-app help

The Help screen (`webui/screens/help.js`) fetches one of
`dev/bin/generate-help-html`'s own committed, body-only HTML fragments
(`webui/help/usage.html`, `webui/help/configuration.html`, generated
from `doc/usage.md`/`doc/configuration.md`) and injects it into the
page — no bridge call, since the fragment is a static file already
bundled alongside `index.html` (`packaging/fim.spec`'s own
whole-`webui/`-tree `datas` entry needs no special-casing for it).
`dev/lib/docslug.py`'s `anchor_for` is shared, unchanged, between
`dev/bin/check-doc-links` and `dev/bin/generate-help-html`, so an
in-app help link and the same heading's real GitHub anchor can never
independently drift — one slugger, two callers.

The Help screen is reachable from every other screen via the native
Help menu, including mid-run — the one screen in this app that needs a
real "return to wherever I was" instead of a fixed Back target, since
every other screen has one obvious place Back returns to and Help does
not.

## 12. Shared logic with the CLI

Four modules were extracted out of `fim.cli`'s own private helpers into
shared, public homes specifically so the GUI performs the exact same
operation as the CLI, not a second, independently maintained
implementation of it:

| Module | Extracted from | Shared by |
|---|---|---|
| `fim.paths` | `cli._atomic_directory` and the CLI's output-directory naming | `fim run`'s own scalar/batch paths, and §7.1/§7.2 above |
| `fim.update` | `fim update --check`'s GitHub Releases lookup | The GUI's "Check for updates" action (`Api.check_for_updates`) |
| `fim.reanalyze` | `cli.reanalyze_trajectory` | `fim stats`, and the GUI's "open an existing run"/animation paths (§8, §9) |
| `fim.persistence.report` | `cli.py`'s private `_write_json` | `report.json`'s writer, shared by `fim run`'s reports, `fim stats`'s re-analysis reports, and the GUI's own run reports |

`fim.viz.scatter` gained a parallel set of "public" (no longer
module-private) functions the GUI's bridge calls directly to get raw
scatter coordinates for client-side rendering, entirely separate from
`plot_frequency_scatter`'s own `Figure`-building path: `frequency_
points`, `pooled_frequency_points`, `panels_from_points`/`scatter_
panels`, and `pooled_scatter_panels`/`deme_pair_panel`.
`plot_frequency_scatter` itself still calls the same underlying
functions internally for the CLI's own `scatter.png` — one
implementation, two consumers, not a second copy of the coordinate math
for the GUI's own use.

## 13. Milestone glossary

Development work on this GUI was tracked against short milestone codes
(`G<n>` for GUI milestones, `W<n>` for a later "unified run view"
work stream) that still appear in a handful of test names and comments
as a stable, searchable label for "the change that introduced this."
This is not a complete project history — only the codes with a
concrete, still-relevant meaning are listed:

| Code | What it named |
|---|---|
| G0 | Extracting `fim.paths`/atomic-directory-publish out of `fim.cli` into a shared module (§12), including the unchanged `run-YYYYMMDD-HHMMSS` naming and the stricter "no pre-existing target directory at all" contract. |
| G3 | The six named statistics (`D`, `G_ST`, `E_ST`, `K_ST`, `H_S`, `H_T`) every results view shows. |
| G11 | The configuration form's `mu`/`mu_b` scope: shared scalars only, with a genuinely per-locus `mu` handled by the load-only-badge/raise path instead of a dedicated widget (§6.2). |
| W5 | `Api.start_run` dispatching to `fim.gui.batch_runner.start_batch_run`, and `fim.gui.app._drain_batch_messages` draining its messages — the batch path's own backend half. |
| W6 | `fim.gui.animation.pre_render_frames` returning plain coordinate data, and `Api.get_animation_frames` as its own bridge caller (§8). |

## 14. Testing

Full detail is in
[`doc/fim-gui-test-plan.md`](fim-gui-test-plan.md): the `gui` pytest
marker's meaning, the file-by-file test coverage table, and the
recurring `evaluate_js`/structural-assertion testing patterns this
package's own suite uses.

## 15. Packaging

The Windows, macOS, and Linux one-file/one-bundle builds this GUI
ships in — including the GTK/WebKit runtime dependencies the Linux
build needs at both build and run time, and the beta build pipeline —
are covered in
[`doc/fim-simulator-detailed-design.md`](fim-simulator-detailed-design.md)
§6.

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-30
generator-responsibility: primary
```
