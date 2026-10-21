# Known issues

Current limitations and unresolved risks affecting use or maintenance of
`fim`. Resolved defects are recorded in [CHANGELOG.md](CHANGELOG.md).

## Open

### Intermittent hang during GUI shutdown

**Status:** the diagnosed bridge-thread race is addressed; the shutdown
timeout remains a safeguard against other blocked threads
**Affects:** `fim gui` on all platforms

#### What you would see

The window closes, but the process continues running. A terminal prompt may
not return, or an application launched from an icon may still appear active.

Completed output remains on disk. For saved runs, resumable progress is
limited to the latest committed checkpoint; see
[saved-run recovery](doc/run-lifecycle.md#recovery-after-a-crash-or-power-failure).

#### What the application does about it

If shutdown has not finished 20 seconds after the window closes, `fim`
records thread diagnostics and exits with status `3`.
`FIM_GUI_SHUTDOWN_TIMEOUT` sets the timeout in seconds; `0` disables it.
Disable it only when investigating shutdown. See
[shutdown troubleshooting](doc/usage.md#if-the-window-closes-but-fim-keeps-running).

Closing the window waits for in-flight Python/JavaScript bridge calls,
rejects further calls, and stops active work. Saved runs pause at a
checkpoint. These measures address known close-path races but cannot
guarantee that every library thread terminates.

#### If it happens to you

1. Find `logs/fim.log` under the `fim` project directory.
2. Find `shutdown deadman fired` and the thread stacks following it.
3. Include that excerpt, your platform, and how you launched `fim` in a
   bug report. Report any shutdown that still requires a force-quit.

Diagnostics are written to the log even when no terminal is attached.

#### Technical detail

CPython joins non-daemon threads before interpreter exit. pywebview dispatches
bridge calls on non-daemon threads; destroying a window during a call can
leave a thread blocked.

`create_window` registers a synchronous `events.closing` handler and wraps
the window's `destroy` method to wait for bridge calls. Both are required:
direct `destroy()` calls do not emit `events.closing` on every platform.
The wait logs calls that exceed its bound. The shutdown timeout provides a
separate process-level safeguard.

Relevant source and tests:

- [`src/fim/gui/app.py`](src/fim/gui/app.py):
  `_start_shutdown_deadman`, `in_flight_bridge_threads`,
  `await_bridge_threads`, `_wrap_destroy_to_settle_first`,
  `Api.on_window_closing`, `Api.finish_close`, and `Api.stop_active_work`
- `test/gui/test_shutdown_deadman.py`
- `test/gui/test_close_guard.py`, `test_close_guard_screen.py`, and
  `test_close_guard_work.py`
- `pytest_unconfigure` in `test/conftest.py`

Do not use `atexit` to diagnose blocked non-daemon threads: CPython joins
those threads before invoking `atexit` callbacks.

### One built-in worked example has no GUI form representation

**Status:** labeled as YAML-only; a per-locus mutation-rate editor is not
scheduled
**Affects:** the Home and Configure example lists and the full example picker

#### What you would see

Six of the seven built-in worked examples load into the Configure form.
“Per-base mutation rate across unequal locus lengths” is labeled
“(view YAML only)”. Selecting it displays an inline explanation rather than
loading the form.

#### Why it happens

The example uses different mutation rates at its two loci. The Configure
form has no per-locus `mu` editor; YAML files with this structure have the
same limitation. Run the example from the command line, or view its committed
results. See [per-locus mutation rates](doc/configuration.md#deriving-mu-from-a-per-base-rate).

#### What would close this

Add a per-locus mutation-rate editor and define how it preserves
`mu_b`-derived provenance. Update the GUI design's form contract and remove
this example's YAML-only label once loading is supported. Retain the labeling
mechanism for configurations the form cannot represent.

Relevant source and tests:

- `mu_from_params` in
  [`src/fim/gui/config_form.py`](src/fim/gui/config_form.py)
- `Api.list_presets` (`loadable`) and `Api.get_preset_form_values` in
  [`src/fim/gui/app.py`](src/fim/gui/app.py)
- `refreshExampleOptions` and `showExampleLoadNotice` in
  [`src/fim/gui/webui/screens/presets.js`](src/fim/gui/webui/screens/presets.js)
- `test_every_other_builtin_preset_loads_into_form_values` in
  `test/gui/test_app_api.py`

### Linux build image is pinned to an end-of-life Debian release

**Status:** pinned snapshot workaround in use; migration not started
**Affects:** Linux release and beta packaging infrastructure

#### What happens today

The `linux-x64` and `linux-beta-x64` jobs use
`python:3.12-slim-bullseye` for its `glibc` 2.31 compatibility baseline and
the `webkit2gtk-4.0`/`libsoup-2.4` pair used by pywebview's GTK backend.

Package sources point to the `20250721T000000Z` snapshot at
`snapshot.debian.org`. The jobs pass
`-o Acquire::Check-Valid-Until=false` to `apt-get update` because the
snapshot's repository metadata has expired. Package signatures remain
checked.

#### Why this needs to be revisited

Disabling `Check-Valid-Until` removes the freshness check for every source
in that update invocation, including any source added later. The frozen
snapshot also lacks security updates published after 2025-07-21. This is a
build-environment risk; it does not by itself establish a defect in a
released executable.

Move the build image to a supported Debian release after verifying its
WebKitGTK/libsoup package combination and the resulting minimum `glibc`
requirement. As an interim improvement, a deb822 source stanza with
`Check-Valid-Until: no` can limit the freshness waiver to the snapshot
entries.

Relevant workflows:

- [`linux-x64` in `.github/workflows/ci.yml`](.github/workflows/ci.yml)
- [`linux-beta-x64` in `.github/workflows/beta.yml`](.github/workflows/beta.yml)

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-09-08
generator-responsibility: other
```
