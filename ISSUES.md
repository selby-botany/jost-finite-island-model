# Known issues

Defects that are understood but not yet fully resolved, together with what
the application does about them today and what evidence would close them.

An issue is listed here when it can affect someone using `fim` and is not
merely a planned improvement. Items that are fully fixed move to
[CHANGELOG.md](CHANGELOG.md) and leave this file.

## Open

### Intermittent hang during GUI shutdown

**Status:** cause identified; fixed in the test suite, mitigated in the
application
**Affects:** `fim gui` (all platforms)
**First observed:** 2026-09-06, in continuous integration
**Diagnosed:** 2026-09-07, from a captured thread dump

#### What you would see

You close the `fim` window. The window disappears, but the program does not
actually finish. Depending on how you started it:

- Started from a dock icon, shortcut, or Finder: the application may keep
  showing as running, and starting `fim` again may appear to do nothing.
- Started from a terminal: the prompt does not come back.

Your results are safe. A run writes its output as it goes, so anything the
run had finished is already on disk regardless of how the program ends.

#### What the application does about it

From version 1.x onward `fim gui` bounds its own shutdown. If shutdown has
not finished 20 seconds after the window closes, the program prints an
explanation, records a diagnostic report, and exits with status `3`.

You should not have to force-quit `fim`. If you do, that is itself worth
reporting.

To wait longer, or to disable the bound entirely while investigating, set
`FIM_GUI_SHUTDOWN_TIMEOUT` (seconds; `0` disables). See
[usage](doc/usage.md#if-the-window-closes-but-fim-keeps-running).

#### If it happens to you

The diagnostic report is the useful part, and it is written to the log file
as well as the terminal, so it survives even when `fim` was started from an
icon with no terminal attached.

1. Find `logs/fim.log` under the `fim` project directory.
2. Look for `shutdown deadman fired`. The thread stacks printed immediately
   after it identify what failed to finish.
3. Attach that section to a bug report, along with your platform and how you
   started `fim`.

That excerpt is what makes a report actionable; without it, a recurrence can
only be recorded as "it hung once".

#### Technical detail

The mechanism is understood, and as of 2026-09-07 so is the specific
cause. Python's interpreter shutdown (`Py_FinalizeEx`) joins every
non-daemon thread before exiting. One thread that never finishes therefore
blocks shutdown indefinitely, with the window already gone.

The thread was captured by name in a CI diagnostic dump:

```text
fim: 1 non-daemon thread(s) alive at interpreter shutdown; these will block
Py_FinalizeEx:
  <Thread(Thread-1162 (_call), started 13556019200)>
      target=js_bridge_call.<locals>._call module=webview.util
```

pywebview delivers every `window.pywebview.api.*` call on its own
**non-daemon** thread (`Thread(target=_call)` in `webview/util.py`, with no
`daemon=True`). A window destroyed while such a call is still in flight
strands that thread, and `threading._shutdown` then waits on it forever.

This is the same class the suite had already fixed five times, each at its
own call site by awaiting the call and polling a settle flag (see
`test/gui/conftest.py`). Those fixes were correct but inherently
per-call-site: every new screen can reintroduce the bug. `test/gui/
conftest.py`'s `await_bridge_threads` now waits for in-flight bridge
threads before the window is destroyed, applied once in teardown so it
covers every test uniformly.

Why this issue stays open: that fix is in the **test** suite. The same
sequence is possible in the real application — a user closing the window
while a bridge call is in flight — and there the deadman timer is the only
protection. A structural fix in `fim.gui.app` (settling in-flight bridge
calls before the window closes, rather than bounding the consequences) has
not been implemented.

History and its limits:

- Two consecutive CI runs stalled in interpreter teardown *after*
  reporting a full passing summary, proving the hang was independent of
  any test failure.
- Three subsequent full runs completed cleanly — which is exactly why the
  intermittency was untrustworthy as evidence of a fix.
- It then recurred twice on 2026-09-07, the second time with the
  diagnostics in place, which named the thread and ended the guesswork.

The lesson worth keeping: three clean runs of an intermittent failure
demonstrated nothing. What resolved it was capturing the failure, which
required building the diagnostics first.

Do not remove or lengthen the deadman on the assumption that the underlying
bug is gone. The mechanism that produces this class of defect is a property
of the libraries involved rather than of any single bug, and every previous
instance was found only after reaching a real build.

Relevant code:

- `_start_shutdown_deadman` in `src/fim/gui/app.py` — the production bound
- `await_bridge_threads` in `test/gui/conftest.py` — the structural fix for
  the diagnosed cause, in the test suite
- `pytest_unconfigure` in `test/conftest.py` — the diagnostics that
  captured it

Note for maintainers: `atexit` cannot be used to diagnose this. CPython
joins non-daemon threads *before* running `atexit` callbacks, so an `atexit`
hook is unreachable on exactly this failure. An implementation built that
way passed every unit test and was silent on the real hang; only an
end-to-end test against a genuinely wedged interpreter revealed it.

## Metadata

```text
generator-name: Copilot CLI
generator-version: Claude Opus 4.5
generator-model-token: claude-opus-4-5
generator-provider: Anthropic
generation-date: 2026-09-07
generator-responsibility: other
```
