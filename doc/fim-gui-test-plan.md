# Desktop GUI test plan

- [Desktop GUI test plan](#desktop-gui-test-plan)
  - [Who this document is for](#who-this-document-is-for)
  - [What the GUI is, in one paragraph](#what-the-gui-is-in-one-paragraph)
  - [The `gui` marker: headless vs. display-requiring](#the-gui-marker-headless-vs-display-requiring)
  - [Test files, by what they cover](#test-files-by-what-they-cover)
  - [Recurring testing patterns worth knowing before reading any one file](#recurring-testing-patterns-worth-knowing-before-reading-any-one-file)
  - [Known, already-resolved timing hazards](#known-already-resolved-timing-hazards)
  - [Static-analysis guards](#static-analysis-guards)
  - [Relationship to the rest of the test suite](#relationship-to-the-rest-of-the-test-suite)

## Who this document is for

Anyone maintaining or extending `fim.gui` — the desktop application — or
its own test suite under `test/gui/`. This is one of the three test
categories this project's own test suite is organized into (see `doc/
fim-simulator-detailed-test-plan.md`'s taxonomy: fim functional, fim-gui
functional, internal/deep); everything below is the second of those
three. Detailed, per-test documentation lives in each test file's own
docstrings (pydoc) — this document organizes and tabulates rather than
repeating that content. The companion
[desktop GUI design document](fim-gui-design.md) covers why and how
`fim.gui` itself is built; this document covers only how it is tested.

## What the GUI is, in one paragraph

`fim.gui` is a [pywebview](https://pywebview.flowrl.com/)-based desktop
front end: a static local `webui/` page (plain HTML/CSS/JS, no build
step, no framework) rendered inside a native window (WKWebView on
macOS, WebView2 on Windows, WebKitGTK on Linux), driven entirely through
one bridge class, `fim.gui.app.Api`, that the JS side calls into and
gets plain JSON-serializable values back from. Every screen calls
`fim.engine.fim`, `fim.viz.scatter`, and `fim.model.params.
SimulationParams` — the same public API `fim.cli` already uses (see
`doc/fim-simulator-functional-api.md`) — and never duplicates
validation, statistics, or run orchestration of its own. `doc/
developer.md`'s own architecture table is the durable, one-line
statement of that rule.

## The `gui` marker: headless vs. display-requiring

Not every file under `test/gui/` needs a real window. `pyproject.toml`'s
own marker registry states the distinction directly: `gui` means "opens
a real pywebview window; needs a display (`xvfb-run` in CI)." A file
carries that marker only when it actually constructs one; several
"headless functional" files below drive real DOM/JS logic through a
window that a *different*, marker-carrying test in the same area already
proved opens correctly — see "Recurring testing patterns," below, for
how that split works in practice. `gui`-marked tests are excluded from
the default `pytest` invocation (`pyproject.toml`'s own `addopts`,
alongside `slow`/`statistical`/`packaging`) and run in CI under
`xvfb-run` (a virtual, headless X display), never on a developer's own
default `pytest` run.

## Test files, by what they cover

| File | Tests | `gui`-marked? | Covers |
|---|---:|---|---|
| `test_app.py` | 6 | yes | The walking skeleton: the window actually builds, loads `webui/index.html`, and the `Api` bridge round-trips both in-process and cross-process. |
| `test_app_api.py` | 65 | no | Every `Api` bridge method's own logic, called as plain Python — form/config marshaling, batch-progress payload shaping, statistic formatting, recent-runs listing — never touching `webview.windows[0]`. |
| `test_animation.py` | 11 | no | `fim.gui.animation.pre_render_frames` — plain coordinate data, no `Figure` objects, no display dependency at all. |
| `test_animation_screen.py` | 1 (parametrized) | — | The unified run view's own scrubber, reached by opening a persisted run. |
| `test_batch_results_screen.py` | 4 | — | The run view's `completed` state for a batch run: pooled scatter, confidence-interval bars, per-replicate table. |
| `test_batch_runner.py` | 13 | no | `fim.gui.batch_runner` — real background threads, real parallel `fim.engine.fim` batch calls. |
| `test_batch_running.py` | 1 | — | A real batch run reaching the bridge end to end (`Api.start_run` → `batch_runner.start_batch_run` → message draining). |
| `test_config_form.py` | 36 | no | All six configuration tabs' own marshaling — `starter_form_values()` round-tripping through `form_values_to_payload` back into an equivalent `SimulationParams`. |
| `test_config_modal_dialogs.py` | 1 | — | Static-analysis guard: every Configure-menu dialog's "Close" button has the `tabindex` a WKWebView host requires for keyboard reachability. |
| `test_help_screen.py` | 3 | — | The in-app Help screen. |
| `test_input_screen.py` | 16 | — | The Configure menu's own modals/value-selectors and always-present controls. |
| `test_open_run_screen.py` | 2 | — | The File menu's "Open run…" action and the recent-runs picker it opens. |
| `test_recent_runs.py` | 6 | no | `fim.gui.recent_runs` — real manifests, written by a real `cli.main(["run", ...])` call, not hand-constructed. |
| `test_results_screen.py` | 5 | — | The run view's `completed` state for a single scalar run. |
| `test_runner.py` | 9 | no | `fim.gui.runner` — `ProgressThrottle`'s clock-driven predicate, and a real background thread running a real `fim.engine.fim` call. |
| `test_running_screen.py` | 3 | — | The running screen: starting a real background run and the page reacting to its progress/done/cancelled messages. |
| `test_store.py` | 19 | no | `GuiProgressStore`/`LiveProgressStore`/`RunCancelledError` against an in-memory trajectory fake or plain files, no thread and no real subprocess. |
| `test_webui_global_scope.py` | 1 | — | Static-analysis guard: every classic (non-module) JS file's top-level declarations are collision-free across the whole shared global scope. |

"—" in the `gui`-marked column means the file mixes both: it drives a
window a sibling test already independently proves opens, so it
inherits that guarantee rather than re-asserting it, and is marked
accordingly test-by-test rather than file-wide. Total: 202 tests across
18 files (counted directly from the test source this session).

## Recurring testing patterns worth knowing before reading any one file

- **`window.evaluate_js` is the one channel.** Every "headless
  functional" test in this package drives the real page through
  `window.evaluate_js(...)`, which returns the *raw* value of whatever
  JS expression it evaluates — never the resolved value of a `Promise`
  that expression happens to produce (confirmed directly against a real
  window before `fim.gui.app` was written). Tests that need an `async`
  bridge call's result therefore route it through a small JS wrapper
  that `await`s the call and writes its result somewhere a second
  `evaluate_js` reads back — `test/gui/conftest.py`'s own
  `drive_and_read` helper is the one place this pattern lives, so no
  individual test file re-derives it.
- **Structural, not pixel, assertions.** Screens are checked by DOM
  structure and state (which elements exist, what class they carry,
  what text/value they hold) — the same "assert structure, not pixels"
  house style `doc/fim-simulator-detailed-test-plan.md`'s own
  visualization-testing section documents for `test/viz/`.
- **Real threads, real processes, no fakes for the mechanism under
  test.** Where a test's whole point is background execution (a run
  actually running while the UI stays responsive, a batch actually using
  several OS processes), the test uses a real thread or a real
  `fim.engine.fim` call rather than mocking the mechanism itself — the
  same "stub the stochastic dependency, never fake the thing you are
  actually testing" principle `doc/fim-simulator-detailed-test-plan.md`'s
  determinism contract states generally.

## Known, already-resolved timing hazards

Two distinct, real timing investigations are recorded directly in
`test/gui/conftest.py`'s own module docstring, not repeated in full
here — both understood, both resolved or explicitly bounded, neither an
open problem:

1. A `20ms` polling cadence hammering pywebview's own macOS
   `AppHelper.callAfter` continuously raised the odds of colliding with
   a real background thread pushing run-progress messages through the
   same window at once — the poll interval was deliberately loosened,
   trading a slightly slower failure-path timeout for a much lower
   collision rate in the common, fast, already-converged case.
2. A separate, intermittent multi-minute stall in the whole `pytest
   test/gui/ -m gui` *process* (not any one test) traced to CPython's
   own interpreter finalizer waiting on a leftover OS thread during
   shutdown, after every test had already passed — not a stuck test, a
   process-exit-time artifact. This is the same underlying shape of
   symptom (processes/threads outliving the tests that spawned them)
   this project's CI investigation independently reproduced and
   explained on 2026-08-30 (`.github/workflows/ci.yml`'s own inline
   comment) as accumulated, already-dying `WebKitWebProcess` cleanup
   from earlier `gui` tests, starved of CPU by subsequent long-running
   tests rather than a competing cause — the same phenomenon, seen from
   two different angles, both self-resolving rather than a live defect.

## Static-analysis guards

Two files in this package are not functional tests at all, in the sense
above — they `grep`/parse source files directly, at zero display or
runtime cost, following the same "static-analysis test for non-obvious
invariants" pattern `doc/fim-simulator-detailed-test-plan.md`'s own
testing-patterns section documents generally: `test_config_modal_
dialogs.py` (every Configure-menu dialog's Close button carries the
`tabindex` a WKWebView host requires) and `test_webui_global_scope.py`
(every classic-script JS file's top-level declarations stay collision-
free across the one shared global scope every `webui/*.js` file lives
in).

## Relationship to the rest of the test suite

`test/viz/` (headless Matplotlib plotting, `fim.viz`) is **not** part of
this document — it has no GUI dependency at all and is part of the fim
functional surface `doc/fim-simulator-functional-api.md` and `doc/
fim-simulator-detailed-test-plan.md` cover. `fim.gui` calls `fim.viz`
directly rather than re-implementing any plotting logic of its own, so
the two are related but distinctly tested.

---

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-30
generator-responsibility: primary
```
