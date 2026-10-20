<!-- markdownlint-disable MD013 -->

# Operational logging design

- [Operational logging design](#operational-logging-design)
  - [Who this document is for](#who-this-document-is-for)
  - [1. Why this exists](#1-why-this-exists)
  - [2. Output channels](#2-output-channels)
  - [3. Architecture](#3-architecture)
    - [3.1 One logger per module, one configuration point per front end](#31-one-logger-per-module-one-configuration-point-per-front-end)
    - [3.2 `fim.logging_setup`](#32-fimlogging_setup)
  - [4. The CLI flag surface](#4-the-cli-flag-surface)
    - [4.1 `-l`/`--log LEVEL`](#41--l--log-level)
    - [4.2 `-L`/`--log-options KEY=VALUE[,KEY=VALUE]...`](#42--l--log-options-keyvaluekeyvalue)
  - [5. GUI configuration](#5-gui-configuration)
  - [6. Default destinations](#6-default-destinations)
  - [7. Format](#7-format)
  - [8. `warnings` integration](#8-warnings-integration)
  - [9. Performance discipline](#9-performance-discipline)
  - [10. Log events by module](#10-log-events-by-module)
  - [11. Testing approach](#11-testing-approach)
  - [12. Configuration integration](#12-configuration-integration)
  - [13. Rejected alternatives](#13-rejected-alternatives)
  - [Metadata](#metadata)

## Who this document is for

This reference covers log levels, destinations, configuration, and call
sites for maintainers of the CLI, GUI, and library. For command syntax,
see [usage](usage.md); for front-end boundaries, see the
[GUI design](fim-gui-design.md) and [developer guide](developer.md).

## 1. Why this exists

Operational logging records execution events for diagnostics, including
long-running batches and failures. Configurable levels expose run,
replicate, and generation-level events without changing scientific
artifacts or requiring a debugger.

## 2. Output channels

The CLI's `print()`-based progress and artifact-path summaries are a
stdout interface, controlled by `--quiet`. Operational logs use stderr
and/or a log file, never stdout. These controls are independent:
`fim -l debug run CONFIG --quiet` suppresses stdout while retaining debug
logs; `fim -l critical run CONFIG` retains progress output while limiting
logs to critical events. Logging flags precede the subcommand (§4).

## 3. Architecture

### 3.1 One logger per module, one configuration point per front end

Modules use `logger = logging.getLogger(__name__)` after their imports.
They do not call `logging.basicConfig` or configure handlers and levels.
Applications configure logging; importing the library does not open log
files or enable output.

`fim/__init__.py` attaches a `logging.NullHandler()` to the `fim` package
logger. Output is configured through `fim.logging_setup.configure()` or
the embedding application's logging configuration.

Three entry points call `fim.logging_setup.configure()`:

- `fim.launcher.main` reads `FIM_LOG_LEVEL` and `FIM_LOG_OPTIONS`
  before dispatch, allowing the dispatch decision to be logged at DEBUG.
- `fim.gui.app.main` reads the same variables, including when called
  directly rather than through the launcher.
- `fim.cli.main` uses parsed `-l`/`-L` arguments.

Each call replaces the handlers (§3.2). The last configuration wins.
CLI flags, including their default `warning` level, supersede launcher
environment settings; the environment is not a fallback for CLI flags.
An external logging configuration is an alternative to this default
setup: `--logging-config PATH` or `FIM_LOGGING_CONFIG` selects a YAML
`logging.config.dictConfig` mapping. Entry points call
`apply_logging_config_file()` instead of `configure()` when a file is
selected; the configurations are not merged.

### 3.2 `fim.logging_setup`

[`src/fim/logging_setup.py`](../src/fim/logging_setup.py) exposes:

```python
def configure(
    level: str | int = "warning",
    options: Mapping[str, str] | None = None,
) -> None:
```

`configure` replaces the `fim` handlers on each call rather than
appending handlers. Reconfiguration therefore does not duplicate output.

It:

1. Resolves `level` (case-insensitive `debug`/`info`/`warn`/`warning`/
   `error`/`critical`, or an already-numeric `logging` level) against
   `logging.getLevelNamesMapping()` (Python 3.11+; this project's own
   floor is 3.12).
2. Parses `options` (already split into a `Mapping[str, str]` by the
   caller — see §4.2) for the keys §6/§7 document, with every key
   optional and defaulting the way this document states.
3. Builds a `logging.handlers.RotatingFileHandler` (unless
   `options["file"]` is the literal string `"none"`) and, unless
   `options["stream"]` is `"none"`, a `logging.StreamHandler(sys.stderr)`
   — attaches whichever of the two are active to the `fim` logger,
   removing any handler a previous `configure()` call left there.
4. Sets each handler's level from `level` or its `file_level`/
   `stream_level` override, then sets the logger to the most permissive
   active handler level so it does not discard requested records.
5. Attaches the same handlers to `py.warnings` and re-establishes warning
   capture (§8).

## 4. The CLI flag surface

Both flags are defined on the top-level parser in `cli._parser()`:
`fim -l debug run CONFIG`. They must precede the subcommand;
`fim run CONFIG -l debug` is an unrecognized-argument error. A single
declaration avoids subparser defaults overwriting top-level values.
`main()` calls `fim.logging_setup.configure(...)` with the parsed
values immediately after `parser.parse_args()` returns, before
dispatching to any command.

### 4.1 `-l`/`--log LEVEL`

One of `debug`/`info`/`warn`/`error`/`critical` (case-insensitive;
`warn` is accepted as the familiar short form of `warning`). Sets the
`fim` logger's effective level, and — unless overridden by
`-L file_level=`/`stream_level=` — both handlers' levels too. Default:
`warning`.

### 4.2 `-L`/`--log-options KEY=VALUE[,KEY=VALUE]...`

A single string of comma-separated `key=value` pairs. The parser splits
on commas and on the first `=` in each pair; it has no escaping syntax,
so values cannot contain commas. Supply all options in one `-L` value.

| Key | Meaning | Default |
|---|---|---|
| `file` | Log file path, or the literal `none` to disable the file handler entirely | `fim.paths.default_log_file()` (§6) |
| `stream` | The literal `none` to disable the stderr handler entirely | stderr enabled |
| `file_level` | Overrides `-l`'s level for the file handler only | `-l`'s value |
| `stream_level` | Overrides `-l`'s level for the stream handler only | `-l`'s value |
| `format` | A `logging.Formatter` style string, applied to both handlers | §7 |
| `max_bytes` | `RotatingFileHandler`'s own `maxBytes`, integer | `1_048_576` (1 MiB) |
| `backup_count` | `RotatingFileHandler`'s own `backupCount`, integer | `5` |

An unrecognized key is a parser error (`parser.error(...)`), reported
on stderr with exit status 2 rather than a traceback.

## 5. GUI configuration

Graphical launches use environment variables for the default logging
setup:

- `FIM_LOG_LEVEL` — the same values as `-l` (§4.1); unset behaves
  exactly like `-l`'s own default.
- `FIM_LOG_OPTIONS` — the same `key=value[,key=value]...` syntax as
  `-L` (§4.2); unset behaves exactly like `-L`'s own default.

There is no in-app logging control (§13). Set these variables in the
launching shell or shortcut environment before opening the GUI.
For a full configuration, use `FIM_LOGGING_CONFIG` or the graphical
entry point's `--logging-config` option (§3.1).

## 6. Default destinations

`fim.paths` defines log paths alongside result paths:

`log_directory(root)` resolves in this order:

1. An explicit `root`, returning `root / "logs"`.
2. The process-level override set by `set_log_directory_override`.
3. `FIM_LOG_DIRECTORY`.
4. `project_root() / "logs"`.

`default_log_file(root)` appends `fim.log` to that directory.

`logs/` sits beside `results/` under the resolved `project_root()`,
including the home-directory fallback for frozen applications launched
without a meaningful working directory. Configuration creates the
directory and opens the rotating file handler immediately
(`delay=True` is not used), so path errors surface at startup.
Path resolution is covered by `test/test_paths.py`.

The stream handler targets stderr, preserving stdout for CLI progress
and results (§2).

## 7. Format

File handler default: `%(asctime)s %(levelname)-8s %(name)s: %(message)s`
— a timestamp and the emitting module's own dotted name, useful once a
log file outlives the terminal session that produced it. Stream
handler default: `%(levelname)s: %(message)s`, without timestamp or module
name. `-L format=...` overrides both handlers identically; separate
format overrides per handler are not supported.

## 8. `warnings` integration

`configure()` attaches its handlers to `py.warnings`, then calls
`logging.captureWarnings(False)` followed by `True` to re-establish the
bridge even if another caller has replaced `warnings.showwarning`.
Warnings emitted through that bridge use the configured destinations
and levels; warning filters still determine whether a warning is emitted.

The two mechanisms have distinct purposes:

- **`logger.warning(...)` (and every other level)** narrates what the
  program is doing, for a human or a log file reading along —
  "operational tracing." Nothing about it is catchable by a caller.
- **`warnings.warn(..., SomeWarningCategory)`** flags a condition a
  *programmatic caller* — a test (`pytest.warns`), a future library
  consumer, or `python -W error` — should be able to detect, filter, or
  promote to an error, independent of whatever the ambient log level
  happens to be. Reserved for API-contract-level anomalies a caller
  might act on, such as an accepted deprecated configuration key or a
  replicate omitted from a statistic's stopping window because the
  statistic is undefined.

A condition that is purely narrative (a generation was persisted, a
file was written, a retry happened) is always a `logger` call, never a
`warnings.warn` — promoting ordinary narration to a `Warning` would
make `python -W error` (or a test asserting "no warnings") fail on
completely ordinary, successful operation.

## 9. Performance discipline

The generation loop in `fim.engine.lineal.run_one` is
performance-sensitive. Logging follows two rules:

1. **Always lazy `%`-style formatting, never an f-string, in a log
   call.** `logger.debug("generation=%d D=%.6g", generation, value)`
   defers formatting `value` until `logging` has already confirmed
   `DEBUG` is enabled; `logger.debug(f"generation={generation} ...")`
   builds the full string unconditionally, on every call, regardless
   of the active level. `ruff`'s own `G004` (`logging-f-string`) rule
   is enabled project-wide to enforce this rule (§12).
2. **Guard anything costlier than formatting a few scalars** — a
   per-generation array summary, for instance — behind
   `if logger.isEnabledFor(logging.DEBUG):` explicitly, so the summary
   itself is never computed unless `DEBUG` is actually active. Plain
   scalar arguments (a generation number, a float statistic already
   computed for another reason) need no such guard: `logging`'s own
   lazy formatting already makes the disabled-level cost negligible.

## 10. Log events by module

The table summarizes operational events, at INFO unless noted:

| Module | Events |
|---|---|
| `fim.cli` | Parsed arguments (DEBUG); which command dispatched; config loaded (path, key parameters); each artifact written (path); non-zero exit (ERROR, with the caught exception's own message). |
| `fim.engine` | Run start (params summary) and end (outcome, converged, generation) for both scalar and batch; each generation's convergence-monitor decision (DEBUG, guarded per §9); replicate start/end inside a batch; adaptive-stop triggered (INFO, both the sequential and parallel-worker paths). |
| `fim.model.params` | Validation errors propagate to the front-end error handler; no duplicate per-raise DEBUG call. |
| `fim.convergence.*` | Criterion evaluated and its own stop/continue decision (DEBUG); final outcome (INFO). |
| `fim.persistence.*` | Every file write (path, DEBUG); manifest/trajectory integrity check outcome (DEBUG on success, WARNING on a detected mismatch, mirroring §8's "caller-actionable" test). |
| `fim.paths.atomic_directory` | Temporary build directory chosen (DEBUG); publish (INFO); rollback on the caller's own exception (WARNING, naming the exception type). |
| `fim.viz.*` | Figure saved (path, DEBUG); large-`d` fallback panel selected (DEBUG). |
| `fim.reanalyze` | Re-analysis start/end (trajectory path, generation selected). |
| `fim.update` | Explicit update-check request start/end and outcome (INFO on success, WARNING on failure). |
| `fim.gui.app`/`runner`/`batch_runner`/`store` | Window/bridge lifecycle events; background thread start/stop; run/batch start, progress-push cadence (DEBUG), done/cancelled/error; every `Api` bridge method call (DEBUG, method name only — via a `_log_bridge_call` decorator, since arguments may contain a full configuration and are not logged by default). |
| `fim.gui.recent_runs`/`animation` | Recent-run scan result count and any unrecognized manifest skipped (DEBUG); animation frames pre-rendered from how many persisted generations (DEBUG). |
| `fim.launcher` | Which of the three dispatch branches fired (DEBUG). |

Error-handling boundaries:

- **The finite-alleles-capacity and unpicklable-argument guards
  (`fim.model.allele.FiniteAlleleRegistry`/`FiniteAlleleSpace`,
  `fim.engine.lineal.require_picklable`) stay hard errors, never
  `warnings.warn`.** Each one raises `ValueError`/`RuntimeError`
  precisely because the run cannot correctly continue past it (an
  overrun finite-allele capacity, an argument that cannot cross a
  `max_workers` process boundary) — converting either to a warning
  would permit execution that cannot produce a valid result.
- **`fim.model.params` has no per-raise-site DEBUG call.** Validation
  exceptions identify the field and reason. `fim.cli.main`'s handler
  (`logger.error("fim %s failed: %s", ...)`, §10's `fim.cli` row)
  logs the message at ERROR when the exception reaches the CLI.

## 11. Testing approach

- **Unit tests for `fim.logging_setup`** (`test/test_logging_setup.py`):
  level-string parsing (valid, invalid, case-insensitive), `-L`-style
  option parsing (every key, an unknown key rejected), idempotent
  re-configuration (calling `configure()` twice never doubles a
  handler), file output, and a `warnings.warn` call reaching the
  configured file. Repeated configuration must restore warning capture
  after external replacement of `warnings.showwarning` (§8).
- **CLI static-analysis and error-path tests** (`test/cli/test_cli.py`),
  matching this project's own established pattern (`test/validation/
  test_ci_runtime_budget.py` for the equivalent CI-side case): `-l`/`-L`
  accepted before every subcommand, rejected after one, and an invalid
  `-l`/`-L` value exits via `parser.error` (`SystemExit(2)`, a plain
  message on stderr) rather than a traceback.
- **Log-file isolation.** Tests that invoke a front end use the
  `log_isolation` fixture from `test/conftest.py` to redirect
  `default_log_file()`. It is not suite-wide autouse: path-resolution
  tests must exercise the real function. Directory-local fixtures in
  `test/cli/` and `test/gui/`, and wrappers in `test/test_launcher.py`
  and `test/test_reanalyze.py`, opt in by depending on it.
  Directory `__init__.py` files qualify same-named `conftest` modules
  for mypy.
- **Subprocess isolation.** The `bin/fim --version` smoke test in
  `test/validation/test_python_wrappers.py` sets
  `FIM_LOG_OPTIONS=file=none` in the child environment. In-process
  monkeypatches do not affect the child. A bare `--version` configures
  launcher logging but exits before CLI logging configuration.
- **Message assertions.** Tests inspect levels and relevant substrings
  in `caplog.records`, not timestamps, scheduling-dependent ordering,
  or exact full-line formatting.
- **Propagation**: `pytest`'s `caplog`
  fixture only sees records that propagate to the root logger, which
  `logging.NullHandler` on `fim` does not block (a handler never stops
  propagation; only `logger.propagate = False` would, and `fim`'s own
  logger never sets that) — no special test-only wiring needed beyond
  the standard fixture.

## 12. Configuration integration

`fim.logging_setup`, `fim.paths.log_directory`, and
`fim.paths.default_log_file` define configuration and destinations.
The package `NullHandler` preserves library import behavior.
`pyproject.toml` enables Ruff's `G004` rule. Entry-point wiring and
module instrumentation follow §3 and §10; tests follow §11.

## 13. Rejected alternatives

- **In-app log-level control.** Environment variables provide the
  diagnostic configuration without a menu and bridge API.
- **stderr-only defaults.** Persistent rotating files retain diagnostics
  for GUI launches without terminals and after terminal sessions end.
- **Platform-specific directories.** Logs share the project-root rule
  used by results rather than XDG, `~/Library/Logs`, or `%LOCALAPPDATA%`.
- **Third-party logging libraries.** Standard-library logging supplies
  levels, handlers, rotation, and warning capture without another runtime
  dependency.

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-31
generator-responsibility: primary
```
