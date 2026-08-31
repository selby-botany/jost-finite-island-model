<!-- markdownlint-disable MD013 -->

# Operational logging design

- [Operational logging design](#operational-logging-design)
  - [Who this document is for](#who-this-document-is-for)
  - [1. Why this exists](#1-why-this-exists)
  - [2. What stays exactly as it is](#2-what-stays-exactly-as-it-is)
  - [3. Architecture](#3-architecture)
    - [3.1 One logger per module, one configuration point per front end](#31-one-logger-per-module-one-configuration-point-per-front-end)
    - [3.2 `fim.logging_setup`](#32-fimlogging_setup)
  - [4. The CLI flag surface](#4-the-cli-flag-surface)
    - [4.1 `-l`/`--log LEVEL`](#41--l--log-level)
    - [4.2 `-L`/`--log-options KEY=VALUE[,KEY=VALUE]...`](#42--l--log-options-keyvaluekeyvalue)
  - [5. The GUI's own configuration surface](#5-the-guis-own-configuration-surface)
  - [6. Default destinations](#6-default-destinations)
  - [7. Format](#7-format)
  - [8. `warnings` integration](#8-warnings-integration)
  - [9. Performance discipline](#9-performance-discipline)
  - [10. Where calls are added, by module](#10-where-calls-are-added-by-module)
  - [11. Testing approach](#11-testing-approach)
  - [12. Commit schedule](#12-commit-schedule)
  - [13. Rejected alternatives](#13-rejected-alternatives)
  - [Metadata](#metadata)

## Who this document is for

Anyone maintaining or extending this project's operational logging —
where a log call belongs, what level to give it, how the `-l`/`-L` CLI
flags and the GUI's own environment variables reach it, and why the
design took the shape it did. Not a users' guide: `doc/usage.md` covers
the `-l`/`-L` flags themselves at the level a researcher running `fim`
needs.

## 1. Why this exists

This project is moderately complex, long-running (a single batch run
can take minutes to hours), and — per this project's own durability
standard (`doc/fim-simulator-detailed-design.md` §1) — meant to be
inherited and maintained by someone who did not build it. All three of
those raise the cost of a bug with no operational trail: today, a
failure deep in a multi-hour batch run leaves nothing behind but
whatever made it into `report.json`/`manifest.json` or a bare Python
traceback. Structured, leveled logging closes that gap: an inheriting
maintainer (or Geek Squad technician, per this project's own
documentation-audience model) can raise the level, reproduce the
failure, and read back *what the program was actually doing*, generation
by generation, without adding a debugger or instrumenting the code by
hand first.

## 2. What stays exactly as it is

The CLI's existing `print()`-based narration (`fim run`'s own progress
line and artifact-path summary, gated by `--quiet`) is a **separate,
pre-existing channel** and is not touched by this design: it is the
tool's documented stdout contract, potentially parsed by a calling
script, and changing its content or timing is a breaking change this
work has no reason to make. Operational logging is an independent,
additive channel — stderr and/or a log file, never stdout — so
`--quiet` and `-l`/`-L` compose freely: `fim run --quiet -l debug` is a
silent stdout, verbose-diagnostics run, and `fim run -l critical` (no
`--quiet`) keeps today's exact narration with logging turned down to
almost nothing.

## 3. Architecture

### 3.1 One logger per module, one configuration point per front end

Every module gets its own logger the standard library's own documented
way: `logger = logging.getLogger(__name__)` near the top, after the
imports. No module ever calls `logging.basicConfig` or otherwise
touches handler/level configuration itself — that is what makes `fim`
safe to import as a library (by `test/`, by `fim.gui`, by a future
consumer) with zero logging side effects unless something explicitly
turns it on, the same "library logs, only an application configures
logging" split the standard library's own documentation recommends.

`fim/__init__.py` attaches one `logging.NullHandler()` to the `fim`
package logger at import time — the standard library's own documented
pattern for exactly this — so a library-style import that never calls
`fim.logging_setup.configure()` produces no "no handlers found"
warning and no output at all.

Three real entry points call `fim.logging_setup.configure()` once,
before doing anything else: `fim.cli.main` (from its own parsed `-l`/
`-L` arguments), `fim.gui.app.main` (from `FIM_LOG_LEVEL`/
`FIM_LOG_OPTIONS`), and `fim.launcher.main` (so the zero-argument and
`--graphical` GUI paths are covered too, before dispatching to either
front end) — matching this project's own "the CLI and the GUI are two
front ends over one core" architecture (`doc/fim-gui-design.md` §1):
one shared configuration function, called once per real invocation,
never duplicated per front end.

### 3.2 `fim.logging_setup`

A new module, `src/fim/logging_setup.py`, with one public function:

```python
def configure(
    level: str | int = "warning",
    options: Mapping[str, str] | None = None,
) -> None:
```

`configure` is idempotent and safe to call more than once (a test
calling it twice, or a future front end importing `fim` inside
another): it replaces the `fim` logger's own handlers rather than
appending to them, so repeated calls never produce doubled output.

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
4. Sets the `fim` logger's own level to `level` (each handler's own
   level defaults to that same value unless `file_level`/`stream_level`
   overrides it — see §4.2).
5. Calls `logging.captureWarnings(True)` (§8).

## 4. The CLI flag surface

Both flags are defined once, on the top-level parser in
`cli._parser()`, before the subcommand parsers branch off — so every
subcommand (`init`/`run`/`stats`/`update`) accepts them identically,
and `fim -l debug run CONFIG` and `fim run CONFIG -l debug` both parse
(`argparse`'s own standard behavior for a parent-parser argument).
`main()` calls `fim.logging_setup.configure(...)` with the parsed
values immediately after `parser.parse_args()` returns, before
dispatching to any command.

### 4.1 `-l`/`--log LEVEL`

One of `debug`/`info`/`warn`/`error`/`critical` (case-insensitive;
`warn` is accepted as the familiar short form of `warning`). Sets the
`fim` logger's own effective level, and — unless overridden by
`-L file_level=`/`stream_level=` — both handlers' levels too. Default:
`warning`, matching `logging`'s own documented default and keeping an
unconfigured `fim` exactly as quiet as it is today.

### 4.2 `-L`/`--log-options KEY=VALUE[,KEY=VALUE]...`

A single string, comma-separated `key=value` pairs, parsed with
`urllib.parse.parse_qsl`-style splitting kept deliberately simple (no
CSV-in-CSV escaping needed: none of the values below can legitimately
contain a comma). Repeating `-L` is not supported — pass every option
in one comma-separated string, the same shape `-L` itself documents in
its own `--help` text.

| Key | Meaning | Default |
|---|---|---|
| `file` | Log file path, or the literal `none` to disable the file handler entirely | `fim.paths.default_log_file()` (§6) |
| `stream` | The literal `none` to disable the stderr handler entirely | stderr enabled |
| `file_level` | Overrides `-l`'s level for the file handler only | `-l`'s value |
| `stream_level` | Overrides `-l`'s level for the stream handler only | `-l`'s value |
| `format` | A `logging.Formatter` style string, applied to both handlers | §7 |
| `max_bytes` | `RotatingFileHandler`'s own `maxBytes`, integer | `1_048_576` (1 MiB) |
| `backup_count` | `RotatingFileHandler`'s own `backupCount`, integer | `5` |

An unrecognized key is a parser error (`parser.error(...)`, the same
"looks like a normal command-line error, not a traceback" contract
`cli.main`'s own docstring already states for every other user
mistake) — not silently ignored, so a typo in `-L` is caught
immediately rather than quietly producing the wrong log destination.

## 5. The GUI's own configuration surface

The GUI is launched by double-click, Start Menu tile, or
`fim --graphical`, never with subcommand-style flags of its own
(`fim.launcher.main` dispatches before any GUI-specific argument
parsing happens — see `doc/fim-gui-design.md` §2). Its logging
configuration is therefore two environment variables, read once by
`fim.launcher.main` before dispatching and passed through to the same
`fim.logging_setup.configure()` call the CLI uses:

- `FIM_LOG_LEVEL` — the same values as `-l` (§4.1); unset behaves
  exactly like `-l`'s own default.
- `FIM_LOG_OPTIONS` — the same `key=value[,key=value]...` syntax as
  `-L` (§4.2); unset behaves exactly like `-L`'s own default.

Deliberately no in-app UI for this (§13 records why): a maintainer or
Geek Squad technician sets the environment before launching (a
Terminal/PowerShell one-liner, or a modified shortcut's own "Target"
field) — the same mechanism this project already documents for
`PRE_PUSH_SKIP_*` (`dev/git-hooks/README.md`) and `MESSAGING_AUTO_DEV_
CERTS`-style toggles elsewhere in the wider codebase's own conventions.

## 6. Default destinations

`fim.paths` gains two functions, exactly parallel to the existing
`results_directory`/`default_output_directory` pair (§12 of
`doc/fim-gui-design.md` describes why `fim.paths` is the one place
every front end resolves a project-relative path):

```python
def log_directory(root: Path | None = None) -> Path:
    return (root if root is not None else project_root()) / "logs"

def default_log_file(root: Path | None = None) -> Path:
    return log_directory(root) / "fim.log"
```

`logs/` sits beside `results/` under the same resolved
`project_root()` — including the frozen-app fallback to the user's
home directory `fim.paths.project_root` already implements for a
Finder-launched `.app` with no meaningful `cwd()` — rather than a
separate, platform-specific log directory (XDG/`~/Library/Logs`/
`%LOCALAPPDATA%`): one root-resolution rule for everything this
program ever writes, already tested (`test/test_paths.py`), rather
than a second one invented for logs alone. `RotatingFileHandler`
creates `logs/` itself if missing (`delay=True` is not used: the file
is opened, and the directory created, at `configure()` time, so a
directory-creation failure surfaces immediately at startup rather than
on the first log call).

The stream handler always targets stderr, never stdout — keeping
stdout exclusively the existing `print()`-based narration's own
channel (§2), so piping `fim run CONFIG | grep ...` is never affected
by a log level change.

## 7. Format

File handler default: `%(asctime)s %(levelname)-8s %(name)s: %(message)s`
— a timestamp and the emitting module's own dotted name, useful once a
log file outlives the terminal session that produced it. Stream
handler default: `%(levelname)s: %(message)s` — no timestamp or
module name, matching the CLI's own already-terse `fim: error: ...`
style for the rare case a `warning`-or-above message reaches an
interactive terminal by default. `-L format=...` overrides both
handlers identically; independently formatting each is not supported
(no real use case surfaced one, and it would double `-L`'s own key
surface for a rarely-needed distinction).

## 8. `warnings` integration

`fim.logging_setup.configure()` calls `logging.captureWarnings(True)`,
the standard library's own documented bridge: every `warnings.warn(...)`
call is routed through a `py.warnings` logger instead of `warnings`'
own default stderr writer, so it obeys the same `-l`/`-L` configuration
as everything else instead of a second, independent output path.

The two mechanisms answer different questions, and this project uses
both, deliberately:

- **`logger.warning(...)` (and every other level)** narrates what the
  program is doing, for a human or a log file reading along —
  "operational tracing." Nothing about it is catchable by a caller.
- **`warnings.warn(..., SomeWarningCategory)`** flags a condition a
  *programmatic caller* — a test (`pytest.warns`), a future library
  consumer, or `python -W error` — should be able to detect, filter, or
  promote to an error, independent of whatever the ambient log level
  happens to be. Reserved for API-contract-level anomalies a caller
  might reasonably act on: a deprecated configuration key still
  accepted for now, or a replicate silently dropped from a statistic's
  own stopping-window because it came back undefined (`fim.engine`'s
  own already-documented `G_ST`-undefined-replicate handling) — the
  kind of thing this project's own determinism/durability standards
  say should never pass silently.

A condition that is purely narrative (a generation was persisted, a
file was written, a retry happened) is always a `logger` call, never a
`warnings.warn` — promoting ordinary narration to a `Warning` would
make `python -W error` (or a test asserting "no warnings") fail on
completely ordinary, successful operation.

## 9. Performance discipline

`fim.engine._run_one`'s own generation loop (`doc/fim-simulator-
design.md` §5) is the single hottest path in this codebase — a large
run advances thousands of generations, each doing real array work.
Two rules keep logging from measurably slowing it down:

1. **Always lazy `%`-style formatting, never an f-string, in a log
   call.** `logger.debug("generation=%d D=%.6g", generation, value)`
   defers formatting `value` until `logging` has already confirmed
   `DEBUG` is enabled; `logger.debug(f"generation={generation} ...")`
   builds the full string unconditionally, on every call, regardless
   of the active level. `ruff`'s own `G004` (`logging-f-string`) rule
   is enabled project-wide specifically to make a reviewer catch this
   by construction rather than by discipline alone (§12).
2. **Guard anything costlier than formatting a few scalars** — a
   per-generation array summary, for instance — behind
   `if logger.isEnabledFor(logging.DEBUG):` explicitly, so the summary
   itself is never computed unless `DEBUG` is actually active. Plain
   scalar arguments (a generation number, a float statistic already
   computed for another reason) need no such guard: `logging`'s own
   lazy formatting already makes the disabled-level cost negligible.

## 10. Where calls are added, by module

Not exhaustive — the target is judgment, not a call on every line —
but every module below gets at least the entries listed, each INFO
unless noted:

| Module | Calls added |
|---|---|
| `fim.cli` | Parsed arguments (DEBUG); which command dispatched; config loaded (path, key parameters); each artifact written (path); non-zero exit (ERROR, with the caught exception's own message). |
| `fim.engine` | Run start (params summary) and end (outcome, elapsed generations) for both scalar and batch; each generation's convergence-monitor decision (DEBUG); replicate start/end inside a batch; adaptive-stop triggered; the finite-alleles-capacity and unpicklable-argument guards already documented as regression-worthy (WARNING, via `warnings.warn` — §8 — since both are caller-actionable). |
| `fim.model.params` | Validation failure's own field/reason, immediately before the `ValueError` that already carries it is raised (DEBUG — the exception itself remains the actual signal). |
| `fim.convergence.*` | Criterion evaluated and its own stop/continue decision (DEBUG); final outcome (INFO). |
| `fim.persistence.*` | Every file write (path, DEBUG); atomic-directory publish and rollback (INFO/WARNING); manifest/trajectory integrity check outcome (DEBUG on success, WARNING on a detected mismatch, mirroring §8's "caller-actionable" test). |
| `fim.viz.*` | Figure saved (path, DEBUG); large-`d` fallback panel selected (DEBUG). |
| `fim.reanalyze` | Re-analysis start/end (trajectory path, generation selected). |
| `fim.update` | Network request start/end and outcome (INFO on success, WARNING on failure — this project's own only network call, so its own failure mode deserves visibility by default). |
| `fim.gui.app`/`runner`/`batch_runner`/`store` | Window/bridge lifecycle events; background thread start/stop; run/batch start, progress-push cadence (DEBUG), done/cancelled/error; every bridge method call (DEBUG, method name only — arguments may contain a full configuration and are not logged by default). |
| `fim.launcher` | Which of the three dispatch branches fired (DEBUG). |

## 11. Testing approach

- **Unit tests for `fim.logging_setup`** (`test/test_logging_setup.py`):
  level-string parsing (valid, invalid, case-insensitive), `-L`-style
  option parsing (every key, an unknown key rejected), idempotent
  re-configuration (calling `configure()` twice never doubles a
  handler), the file handler actually writing to the resolved default
  path, `captureWarnings` actually routing a `warnings.warn` call
  through `caplog`.
- **CLI static-analysis tests** (`test/cli/test_cli.py`), matching this
  project's own established pattern (`test/validation/test_ci_runtime_
  budget.py` for the equivalent CI-side case): `-l`/`-L` accepted on
  every subcommand, an invalid `-l` value rejected with a plain
  `parser.error` message rather than a traceback.
- **No test asserts exact log message text or count in a
  determinism-sensitive way.** This project's own house rule (`a test
  is a pure function of its commit`) already forbids asserting
  anything timestamp- or ordering-fragile; log records are inherently
  narrative, not a stable contract, so tests check *that* a call was
  made at the *right level* (`caplog.records`, filtered by level and a
  substring, never a full-line equality) rather than a message's exact
  wording, so a later prose edit to a log message never breaks a test.
- **`caplog`'s own propagation requirement**: `pytest`'s `caplog`
  fixture only sees records that propagate to the root logger, which
  `logging.NullHandler` on `fim` does not block (a handler never stops
  propagation; only `logger.propagate = False` would, and `fim`'s own
  logger never sets that) — no special test-only wiring needed beyond
  the standard fixture.

## 12. Commit schedule

1. This design document.
2. Foundation: `fim.logging_setup`, `fim.paths.log_directory`/
   `default_log_file`, `fim/__init__.py`'s `NullHandler`, `ruff`'s
   `G004` rule enabled in `pyproject.toml`, and `test/test_logging_
   setup.py`.
3. CLI wiring: `-l`/`-L` on the top-level parser, `main()` calling
   `configure()`, `doc/usage.md`'s own flag reference updated.
4. `fim.launcher`/`fim.gui.app` wiring: `FIM_LOG_LEVEL`/
   `FIM_LOG_OPTIONS`, `doc/fim-gui-design.md` updated.
5. `fim.cli`/`fim.engine`/`fim.persistence` instrumentation (the
   highest-value, most "long-running-operability" modules).
6. `fim.convergence`/`fim.viz`/`fim.reanalyze`/`fim.update`
   instrumentation.
7. `fim.gui.*` instrumentation.
8. `CHANGELOG.md` entry; `doc/developer.md` cross-reference.

## 13. Rejected alternatives

- **A GUI View-menu log-level control.** Real UI work (a new submenu,
  a bridge method, a test file) for a control only a maintainer
  debugging a specific problem would ever touch — the environment-
  variable surface (§5) reaches the identical `configure()` call with
  none of that cost, and is the same mechanism this codebase's own
  wider conventions already use for comparable maintainer-only
  toggles. Revisit if a non-technical user ever genuinely needs to
  raise their own log level without a terminal at hand — no evidence
  of that yet.
- **stderr-only by default, no persistent log file.** Would leave
  exactly the gap §1 exists to close: a GUI user has no terminal to
  read stderr from at all, and a CLI user's terminal scrollback is
  gone the moment the window closes. A rotated file that exists
  whether or not anyone thought to ask for it directly serves "long-
  running, maintainable by someone else."
- **A platform-specific log directory** (XDG/`~/Library/Logs`/
  `%LOCALAPPDATA%`). More conventional for a "real" desktop app, but a
  second, GUI-specific path-resolution rule this project would need to
  build, test, and keep in sync with the existing, already-correct
  `project_root()`/frozen-app-fallback logic `results_directory` already
  has — not worth the duplication for a project this size.
- **`structlog` or another third-party structured-logging library.**
  The standard library's own `logging` already covers every
  requirement here (levels, handlers, rotation, a `warnings` bridge)
  with no new dependency — and this project's own dependency-footprint
  discipline (`doc/fim-simulator-detailed-design.md` §2.1) already
  excludes anything not load-bearing for the one-file executable.

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-08-31
generator-responsibility: primary
```
