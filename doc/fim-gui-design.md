<!-- markdownlint-disable MD013 -->

# Desktop GUI design

- [Desktop GUI design](#desktop-gui-design)
  - [Who this document is for](#who-this-document-is-for)
  - [1. What the GUI is](#1-what-the-gui-is)
  - [2. Rendering backend](#2-rendering-backend)
  - [3. Architecture overview](#3-architecture-overview)
  - [4. The `Api` bridge](#4-the-api-bridge)
    - [4.1 The `evaluate_js`/Promise pattern](#41-the-evaluate_jspromise-pattern)
    - [4.2 Bridge method surface](#42-bridge-method-surface)
  - [5. The unified run view](#5-the-unified-run-view)
    - [5.1 View structure](#51-view-structure)
    - [5.2 States: initial, running, completed](#52-states-initial-running-completed)
  - [6. The configuration form](#6-the-configuration-form)
    - [6.1 Two panels and the cardinality rule](#61-two-panels-and-the-cardinality-rule)
    - [6.2 Load-only badges and unrepresentable constructs](#62-load-only-badges-and-unrepresentable-constructs)
    - [6.3 The execution-engine selector](#63-the-execution-engine-selector)
  - [7. Run orchestration](#7-run-orchestration)
    - [7.1 Scalar runs](#71-scalar-runs)
    - [7.2 Batch runs](#72-batch-runs)
    - [7.3 Shared output-directory semantics with the CLI](#73-shared-output-directory-semantics-with-the-cli)
  - [8. Animation](#8-animation)
  - [9. Recent runs and opening a persisted run](#9-recent-runs-and-opening-a-persisted-run)
  - [10. Navigation: the persistent rail and native menus](#10-navigation-the-persistent-rail-and-native-menus)
  - [11. In-app help](#11-in-app-help)
  - [12. Shared logic with the CLI](#12-shared-logic-with-the-cli)
  - [13. Milestone glossary](#13-milestone-glossary)
  - [14. Testing](#14-testing)
  - [15. Packaging](#15-packaging)

## Who this document is for

Anyone maintaining or extending `fim.gui` — the desktop application.
The companion [design document](fim-simulator-design.md) and
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
screen calls `fim.engine.simulate_params`, `fim.viz.scatter`, and
`fim.model.params.SimulationParams` — the same public API `fim.cli`
already uses (see
[`doc/fim-simulator-functional-api.md`](fim-simulator-functional-api.md))
— and never duplicates validation, statistics, or run orchestration of
its own. The [developer guide](developer.md) architecture table
states that rule directly: "GUI: call `fim.engine.simulate_params`; do not
duplicate model logic."

`fim.launcher` dispatches to `fim.gui.app.main` for the zero-argument
and `--graphical` invocations, exactly as it dispatches to `fim.cli`
otherwise — the GUI is one more front end over the same engine, not a
parallel implementation.

## 2. Rendering backend

pywebview embeds the platform's native web view: WKWebView on macOS,
WebView2 on Windows, and WebKitGTK on Linux. The executable does not
bundle a separate browser engine. Platform dependencies are documented
in the [installation guide](../install/README.md).

Canvas renders the allele-frequency scatter, coincidence counts, and
trajectory playback. HTML and CSS provide the controls and layout.
The Help screen displays HTML generated from the same Markdown sources
as the repository documentation (§11).

## 3. Architecture overview

| Module | Responsibility |
|---|---|
| `fim.launcher` | Dispatches to `fim.gui.app.main` (zero-argument, `--graphical`) or `fim.cli.main` otherwise. Does not change across front ends. |
| `fim.gui.app` | pywebview bootstrap, native menus, and the `Api` bridge class (§4). |
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
| `get_starter_form` | Return `starter_form_values()`, the default configuration form values. |
| `validate_form` | Validate form values without starting a run (live field-level feedback). |
| `get_initial_state_panels` / `get_initial_state_deme_pair_panel` | The `p_0` scatter shown before a run starts (§5.2). |
| `load_yaml` / `save_yaml` | The File menu's "Open/Save configuration" actions. |
| `get_default_max_workers` | Default batch worker count. |
| `get_engine_backend_availability` | Whether the optional `numba` dependency is installed, so the execution-engine selector can relabel the two options that need it (§6.3). |
| `get_significant_digits` / `set_significant_digits` | Display precision in Settings. |
| `get_live_deme_pair` / `set_live_deme_pair` | Selected demes for pairwise scatter plots. |
| `list_recent_runs` | Screen 6's/File-menu's recent-runs list (§9). |
| `open_run` | Reopen a persisted run into the unified results view. |
| `compare_runs` | Overlay completed runs' final-state scatter panels and identify differing configuration fields. |
| `get_animation_frames` / `get_animation_deme_pair_frames` | Sampled scatter coordinates for the scrubber (§8). |
| `get_deme_pair_panel` / `get_batch_deme_pair_panel` | Re-render a completed run's scatter at a different deme pair. |
| `ping` / `ping_from_worker` | The walking-skeleton round-trip proof, in-process and cross-process. |
| `open_external_link` | Opens a URL in the OS default browser (Help menu, About). |
| `check_for_updates` | Check for updates through `fim.update` (§12). |
| `get_about_info` | Version string and repository/documentation links for the About dialog. |

## 5. The unified run view

### 5.1 View structure

The run view uses three states: `initial`, `running`, and `completed`.
The corresponding scripts are `run-view-initial.js`,
`run-view-running.js`, and `run-view-completed.js`;
`run-view-controls.js` handles shared controls. The view contains the
scatter canvas, statistics tables, deme-pair selector, and trajectory
scrubber. Scalar and batch results share this structure.

Configure and Home are separate destinations for editing model inputs
and browsing saved runs. Navigation history records the destination
from which Help was opened.

### 5.2 States: initial, running, completed

- **`initial`** (`run-view-initial.js`) — shown as soon as the form has
  valid values, before any run starts: the `p_0` scatter, axis labels,
  the selected statistics, and a generation-0 progress bar, so the
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
  on a batch confidence-interval display.

Every classic (non-module) script on this page shares one global
scope: a `const` declared in one `screens/*.js` file is visible by bare
name in every other, so a name declared in two of them would be a
`SyntaxError`, not a shadow. `test_webui_global_scope.py` is a static
guard against exactly that collision, and `run-view-initial.js` (loaded
first among the three state files) is the deliberate single owner of
every element reference the other two also need.

## 6. The configuration form

### 6.1 Two panels and the cardinality rule

`fim.gui.config_form` is a pure, pywebview-free set of functions
marshaling `SimulationParams` to and from the model-input screen's
`dict[str, str]` of one string per field, and from there to a
`dict[str, object]` payload ready for `SimulationParams.from_mapping`
— the identical validator `fim.cli` already uses. Nothing in this
module duplicates a validation rule `from_mapping` already enforces: a
malformed string is coerced to the right Python type before being
handed to that one validator, never re-checked against a second,
GUI-local copy of a rule.

Configure is reached from the persistent rail. Scalar fields use
individual controls; per-deme and per-locus lists use list or grid
editors. Grid editors support a `d`-by-`d` migration matrix, custom
locus identifiers, and explicit deme-by-locus starting frequencies.
Arbitrary per-locus mutation rates require a configuration file.

### 6.2 Load-only badges and unrepresentable constructs

The migration matrix, custom loci, and explicit initial-frequency modes
support editing values loaded from a configuration file.

- **Arbitrary per-locus `mu`** raises `ValueError` from
  `params_to_form_values`; edit these values in YAML. Per-locus rates that one per-base rate
  generates (`mu_b` over loci of unequal length, which
  `SimulationParams` keeps only in expanded form) are not "genuinely
  per-locus": `mu_from_params` recovers the `mu_b` and renders it in
  the selector's `mu_b` mode.

### 6.3 The execution-engine selector

The **execution engine** selector sets
`SimulationParams.engine_backend`. Settings provides defaults, and a
configuration can override them. See the
[configuration reference](configuration.md) for backend and JIT constraints.

| Option | What it is |
|---|---|
| `lineal` | The single-threaded reference implementation every other backend's correctness is checked against. |
| `auto` (**desktop default**) | Selects a backend using deme-count and locus-capacity thresholds. |
| `generational` | Thread-parallel; bit-identical to `lineal` for a given seed. |
| `generational-vector` | Array-native backend; requires `numba`. |

All four values remain selectable so configurations round-trip without
substituting a different backend. The bridge reports optional-dependency
availability; options requiring an unavailable dependency are labeled
accordingly. Platform packaging determines whether `numba` is bundled (§15).

Settings exposes `jit`, `auto_vector_min_d`, `auto_vector_max_capacity`,
and `max_concurrent_replicates`. Engine-specific controls appear when
relevant. Threshold calibration is documented in the
[maintainer scripts reference](../dev/bin/README.md).
Every engine supports the within-run standard-deviation band.

## 7. Run orchestration

### 7.1 Scalar runs

`fim.gui.runner` runs `fim.engine.simulate_params` — a single blocking call — on a
`threading.Thread`, so a multi-thousand-generation run never freezes
the GUI main thread. Progress and cancellation come from
`fim.gui.store.GuiProgressStore`, a decorator around the
`TrajectoryStore` protocol that holds a `threading.Event` and a
callback closure directly — real Python objects the calling thread can
read and write, since nothing here crosses a process boundary. The
push interval is throttled to `PROGRESS_THROTTLE_INTERVAL_SECONDS`
(0.05s) so a fast-drifting run does not flood the bridge with more
updates than the page can usefully render.

### 7.2 Batch runs

`fim.gui.batch_runner` runs a multi-replicate batch in parallel, as
real OS processes, via `fim.engine.simulate_params(..., max_workers=N,
store_factory=...)` — the same call shape `cli._command_run_batch`'s
own default (non-`--sequential`) path already makes. Progress and
cancellation for this path are entirely file-mediated
(`fim.gui.store.LiveProgressStore`), not pushed through an in-process
queue: a `threading.Event` and a callback closure cannot be pickled
across a `ProcessPoolExecutor` worker boundary, so each worker's
`LiveProgressStore` instead writes a small `.progress` sidecar file
after each generation and checks a shared cancellation file before
each write. The bridge polls each in-flight replicate's sidecar
on a coarser cadence (`_BATCH_POLL_INTERVAL_SECONDS`, 0.5s) than the
scalar in-process push interval, since each poll re-reads a
whole `trajectory.tlog` per currently-reporting replicate (a block scan, then
one rebuilt generation; see `doc/trajectory-log.md`) — a real,
if usually small, cost that grows with replicate count and how far
each has run. A batch's terminal outcome (done, cancelled, or error)
arrives through the in-process message queue. Per-generation progress
uses filesystem sidecars.

### 7.3 Shared output-directory semantics with the CLI

Both paths do their work inside `fim.paths.atomic_directory` — the
exact context manager `cli._command_run_scalar` and
`cli._command_run_batch` already use: every write lands in a hidden
temporary sibling of `output_directory`, published with one atomic
rename only if the `with` block exits normally. A cancelled run raises
`RunCancelledError` out of that block; an unexpected engine error
raises one of a small set of expected engine errors. Either way,
`atomic_directory` exception handling discards the temporary
directory and `output_directory` is never created — no GUI-specific
cleanup code is needed for either outcome. The same four artifacts are
written, in the same order, as the CLI's
`_write_run_artifacts`: `trajectory.tlog` streamed generation by
generation, then `report.json` and `scatter.png` once the run
finishes, then — last, and only once both are flushed —
`manifest.json`, augmented with each artifact's SHA-256 digest.

`paths.default_output_directory()` uses `run-YYYYMMDD-HHMMSS` in UTC.
Two calls in one second can select the same path. `start_run` uses a
bounded wait for a distinct timestamp before reporting a directory
collision.

## 8. Animation

`fim.gui.animation.pre_render_frames` samples at most
`GUI_ANIMATION_MAX_FRAMES` (100) generations from a persisted
trajectory, evenly spaced across the run's persisted range and always
including generation 0 and the final generation. Each sampled
generation produces a plain coordinate array
(`fim.viz.scatter.frequency_points`) along with supplemental
literature visualization payloads (`allele_composition_payload`,
`frequency_spectrum_payload`), not a rendered Matplotlib `Figure`: the
scrubber ships the whole sampled set to the page once and drives
play/pause/scrub entirely with client-side Canvas redraws across the
main scatter canvas and supplemental cards. Frame preparation supplies
data; the client renders it.

The scrubber (`webui/scrubber.js`) functions in dual modes: during a
live scalar run, it operates in `"live"` mode, pinning by default to the
most recent generation arriving via progress pushes while permitting
the user to drag the slider back to inspect previous generations
without interrupting execution; in `"replay"` mode (`completed` state),
it plays back or scrubs through the sampled trajectory frames.

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
files used by `fim stats` and command-line batch summaries.
A batch's manifest is listed but labeled distinctly (e.g. "batch
(14/20)") rather than treated as something the picker can open
directly: open an individual replicate's trajectory
to any single replicate, since a batch-level manifest has no single
trajectory of its own to verify or re-analyze.

## 10. Navigation: the persistent rail and native menus

A persistent rail (`webui/screens/nav-rail.js`) is always visible along
the window's left edge: **Home**, **Configure**, **Explore**, **Run**,
**Results**, **Compare**, and — set apart at the bottom — **Help**, current
destination highlighted, reachable from any screen including mid-run. Run
and Results both point at the same unified run view §5 describes; Configure
is the two-panel screen §6.1 describes (FIM parameters, Structure); Explore
and Compare are their own destinations (theoretical-prediction lookup and
multi-run overlay, respectively — see the [desktop workflow
table](usage.md#desktop-gui-fim-gui) for what each shows). A read-only
parameter strip beneath the title bar always shows the current N/d/m/mu;
clicking any of the four jumps straight to Configure. The document toolbar
below it lists the open configurations, runs, and studies. Each document
owns its form values, view history, dirty state, and job association;
switching selects another document without starting, stopping, or pausing
work. Results already open are selected rather than duplicated, while new
drafts remain separate. Open documents are session-only and are not
restored after a restart. Closing a document runs the save/discard or
job-stop/pause flow for that document; it never deletes saved results.

Right-click a document for **Close other tabs** or **Close all results**;
both actions are also in the File menu. Close other tabs keeps the chosen
document. Close all results closes completed run and sweep-result documents,
not configurations or saved runs. Dirty documents and active jobs use the
ordinary close dialog one at a time. **Keep** skips that document and
continues; Escape cancels the remaining bulk close. Above 12 open documents,
a notice suggests closing all results; 12 is a soft limit, not a cap.

The document toolbar is keyboard reachable. Arrow keys, Home, and End move
focus; Enter or Space selects the focused document; Delete requests its
closure. File menu actions and shortcuts target the selected document:
Command+N/Control+N creates a configuration, Command+W/Control+W closes the
selected tab, and Control+Tab cycles tabs. On macOS, Command+Shift+] and
Command+Shift+[ also cycle forward and backward. The close dialog remains
the authority for dirty documents and active jobs.

Three native menus, built once in `fim.gui.app._build_menu` and stable
across the whole window's lifetime, duplicate the everyday rail/mouse
actions for a keyboard-shortcut user — every item reuses the exact same
action the matching on-screen control already performs, none of it new
business logic:

- **File** — New configuration, Close tab, Next tab, Previous tab,
  Close other tabs, Close all results, Open/Save configuration, Load
  example…, Open run…, Reveal output folder, Quit.
- **Run** — Run simulation, Cancel run.
- **Help** — the in-app help screen (§11), "Documentation on GitHub,"
  Check for updates, and About.

Configure provides model and convergence controls. Settings provides
display preferences, including significant digits, and default run
settings.

## 11. In-app help

The Help screen ([help.js](../src/fim/gui/webui/screens/help.js)) loads
committed HTML fragments generated by
[`generate-help-html`](../dev/bin/generate-help-html):

| Source | Bundled page |
|---|---|
| [Usage guide](usage.md) | `webui/help/usage.html` |
| [Configuration reference](configuration.md) | `webui/help/configuration.html` |
| [Statistics reference](statistics.md) | `webui/help/statistics.html` |

These pages are bundled with the application and available offline.
Links between them use `data-fim-help` and stay in the application.
Links to mathematical guides, generated API entries, and source use
`data-fim-external` and open through the bridge in the system browser.
The generator and link checker share `dev/lib/docslug.py` for heading IDs.

Inline $\TeX$ and fenced `math` equations are converted to native MathML
when help is generated. Displayed equation tags remain visible.
Fractions, sums, and subscripts therefore render offline without a
JavaScript math library or downloaded fonts.

The native Help menu provides all three references. Statistic names
in table rows and result-column headings are keyboard-accessible help
buttons built by [`buildStatisticName`](../src/fim/gui/webui/meters.js).
Each catalog entry's
`help_anchor` selects its definition in the statistics reference, which
links directly to the API symbol; the generated API supplies the source
link. Non-catalog display quantities
use `STATISTIC_REFERENCE_ANCHORS` in `statistics-catalog.js`.
Help-button events do not activate the row's trajectory toggle.

Back returns to the preceding screen, including when Help is opened
during a simulation.

## 12. Shared logic with the CLI

The GUI and command line share these modules:

| Module | Responsibility | Shared by |
|---|---|---|
| `fim.paths` | Atomic directory publication and output-directory naming | Scalar and batch command-line and GUI runs |
| `fim.update` | `fim update --check`'s GitHub Releases lookup | The GUI's "Check for updates" action (`Api.check_for_updates`) |
| `fim.reanalyze` | Recompute statistics from saved trajectories | `fim stats` and GUI run reopening and playback (§8, §9) |
| `fim.persistence.report` | Write report JSON | Simulation reports and reanalysis in both interfaces |

`fim.viz.scatter` provides functions the GUI bridge calls to obtain
scatter coordinates for client-side rendering: `frequency_
points`, `pooled_frequency_points`, `panels_from_points`/`scatter_
panels`, and `pooled_scatter_panels`/`deme_pair_panel`.
`plot_frequency_scatter` itself still calls the same underlying
functions internally for the command-line `scatter.png`.

## 13. Milestone glossary

Test names and comments use the following labels for implementation areas:

| Code | Implementation area |
|---|---|
| G0 | Atomic directory publication through `fim.paths`, timestamp-based naming, and rejection of existing output directories (§12). |
| G3 | The core differentiation and diversity statistics (`D`, `G_ST`, `E_ST`, `K_ST`, `H_S`, `H_T`). |
| G11 | The configuration form's `mu`/`mu_b` scope: shared scalars only, with a genuinely per-locus `mu` handled by the load-only-badge/raise path instead of a dedicated widget (§6.2). |
| W5 | Batch dispatch through `start_batch_run` and progress delivery through `_drain_batch_messages`. |
| W6 | Coordinate preparation through `pre_render_frames` and delivery through `Api.get_animation_frames` (§8). |

## 14. Testing

Full detail is in
[`doc/fim-gui-test-plan.md`](fim-gui-test-plan.md): the `gui` pytest
marker's meaning, the file-by-file test coverage table, and the
recurring `evaluate_js`/structural-assertion testing patterns this
package uses.

## 15. Packaging

The Windows, macOS, and Linux one-file/one-bundle builds this GUI
ships in — including the GTK/WebKit runtime dependencies the Linux
build needs at both build and run time, and the beta build pipeline —
are covered in
[`doc/fim-simulator-detailed-design.md`](fim-simulator-detailed-design.md)
§6.

Apple Silicon macOS, Windows x64, and Linux x64 builds install the
`[jit]` extra and bundle `numba`. Windows ARM64 and Intel macOS builds
omit it because the pinned dependency set lacks compatible binary
wheels. The engine selector labels options that require an unavailable
dependency (§6.3). The dependency declarations and platform constraints
are recorded in [`pyproject.toml`](../pyproject.toml).
