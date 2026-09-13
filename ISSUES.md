# Known issues

Defects that are understood but not yet fully resolved, together with what
the application does about them today and what evidence would close them.

An issue is listed here when it can affect someone using `fim` and is not
merely a planned improvement. Items that are fully fixed move to
[CHANGELOG.md](CHANGELOG.md) and leave this file.

## Open

### Intermittent hang during GUI shutdown

**Status:** cause identified; the one diagnosed cause is now settled at the
source, in both the test suite and the application, with the deadman timer
kept as a backstop for whatever the next undiagnosed cause turns out to be
**Affects:** `fim gui` (all platforms)
**First observed:** 2026-09-06, in continuous integration
**Diagnosed:** 2026-09-07, from a captured thread dump
**Production fix landed:** 2026-09-13 (design doc `20260912-claude-sonnet-5-
shutdown-bridge-thread-settle-design.md`, `selby/restricted`)

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
own call site by awaiting the call and polling a settle flag. Those fixes
were correct but inherently per-call-site: every new screen can reintroduce
the bug. `in_flight_bridge_threads`/`await_bridge_threads` (`src/fim/gui/
app.py` — moved there from `test/gui/conftest.py`, where they lived until
2026-09-13, so production code and the test suite now share one
implementation) wait for in-flight bridge threads to finish before a window
is actually destroyed, applied once as a structural backstop rather than
re-derived at every call site.

That backstop now runs in the real application, not only in the test
suite's own teardown, at both of the two ways this application can trigger
a close: `create_window` registers it on `window.events.closing`, a real,
synchronous hook every platform's own native close-request handler fires
before tearing the window down (confirmed live on macOS: a slow subscriber
measurably delayed the platform's own close call by its own full
duration); and `_build_menu`'s own "Quit fim" action settles explicitly
before calling `window.destroy()`, needed because `window.destroy()` does
not fire `events.closing` at all on macOS (confirmed live — traced to
`NSWindow.close` bypassing the `windowShouldClose_` delegate method
entirely), so relying on the `events.closing` hook alone would have left
the File menu's own Quit item on the most commonly used development
platform completely unprotected.

Why this issue stays open regardless: this closes the one specific,
diagnosed, reproduced race for the two real ways this application can
trigger a close today. It does not, and cannot, prove no other non-daemon
thread can ever strand itself before or after this fix — the mechanism
that produces this class of defect is a property of the libraries involved
rather than of any single bug (see below), so the deadman stays exactly as
it is, as the backstop for whatever the next instance turns out to be.

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

- `_start_shutdown_deadman` in `src/fim/gui/app.py` — the production bound,
  the backstop for whatever this fix does not catch
- `in_flight_bridge_threads`/`await_bridge_threads` in `src/fim/gui/app.py`
  — the structural fix for the diagnosed cause, shared by production and
  the test suite
- `create_window`'s own `events.closing` registration and `_build_menu`'s
  own "Quit fim" wrapper, both in `src/fim/gui/app.py` — the two real
  places that fix is actually wired into the running application
- `test/gui/test_shutdown_deadman.py` — regression tests for both wiring
  points, and for `in_flight_bridge_threads`/`await_bridge_threads`
  themselves
- `pytest_unconfigure` in `test/conftest.py` — the diagnostics that
  captured it

Note for maintainers: `atexit` cannot be used to diagnose this. CPython
joins non-daemon threads *before* running `atexit` callbacks, so an `atexit`
hook is unreachable on exactly this failure. An implementation built that
way passed every unit test and was silent on the real hang; only an
end-to-end test against a genuinely wedged interpreter revealed it.

### Linux build image is pinned to an end-of-life Debian release

**Status:** stopgap applied; durable fix not started
**Affects:** the Linux packaging pipeline (`.github/workflows/ci.yml`'s
`linux-x64`, `.github/workflows/beta.yml`'s `linux-beta-x64`) — not any
released `fim` build, and not `fim` as installed or run by anyone
**First observed:** 2026-09-08, in continuous integration (run 34187301462)

This is a build-infrastructure issue, not an application defect — nothing
about how `fim` behaves for a user is affected. It is recorded here because
the stopgap trades away real properties of the build pipeline (both a
security check and package currency), and that trade needs to be revisited
rather than forgotten once it stops being the thing that is actively
broken.

#### What happens today

Both Linux packaging jobs build inside `python:3.12-slim-bullseye`
(Debian 11), chosen deliberately for `glibc` 2.31's older-baseline
portability and for the `webkit2gtk-4.0`/`libsoup-2.4` package pair
`pywebview`'s own GTK backend falls back to (see the comments at each
job's `container:` line). Debian 11 went fully end-of-life mid-flight
during this project's own use of this image, in two stages:

1. `deb.debian.org/debian-security`'s own `bullseye-security`
   `InRelease` stopped being refreshed, so its `Valid-Until` lapsed —
   `apt-get update` failed outright with "Release file ... is expired"
   (exit code 100, run 34187301462).
2. Within a day, `deb.debian.org` stopped serving that suite's actual
   `.deb` files too — `apt-get update` kept succeeding (the index still
   resolved), but `apt-get install` 404d on every package apt resolved
   from `bullseye-security` (`systemd`, `perl`, `glibc`, `dpkg-dev`,
   `webkit2gtk`, and others — run 34236473262).

The stopgap (commits `345263f`, then superseding it once stage 2
appeared) repoints `sources.list` at `snapshot.debian.org`'s pinned
`20250721T000000Z` capture of all three suites — a fallback the image
itself already ships, commented out — and passes `-o
Acquire::Check-Valid-Until=false` to `apt-get update`, required
alongside the repoint since a pinned historical snapshot is permanently
past its own `Valid-Until` by construction.

#### Why this needs to be revisited

`Acquire::Check-Valid-Until` exists to defend against a repository
replay/freeze attack: `deb.debian.org`/`snapshot.debian.org` are plain
`http://`, not `https://`, so package integrity rests entirely on the
`Release` file's GPG signature plus this freshness bound, not transport
security. Disabling the check trusts whatever validly-signed snapshot is
served, indefinitely. For `snapshot.debian.org` specifically this is a
smaller step than it sounds — the whole point of that host is serving a
fixed, permanently-immutable, GPG-signed capture — but it is still a
global waiver on that one `apt-get update` invocation, not scoped to
just this one source, so a future source added to either job's own
`apt-get install` list would silently lose this same protection with no
new decision made.

Confirmed live before applying each stage of the stopgap:
`archive.debian.org` — Debian's usual durable home for an EOL suite —
does not help at either stage: it carries no `bullseye`/
`bullseye-security` suite at all yet. `snapshot.debian.org`'s pinned
capture was verified with a real `apt-get install` of the exact package
list both jobs use, not just `update` — it correctly resolves
security-patched versions (e.g. `systemd 247.3-7+deb11u6`).

New trade-off introduced by the snapshot repoint, not present in the
`345263f` stopgap it supersedes: `20250721T000000Z` is over a year
before this issue was first observed, so the build now uses whatever
security-patched package versions existed as of that date, not
whatever was newest on `deb.debian.org` right before it went fully
EOL (already-confirmed stale even before stage 2: `libsystemd0
247.3-7+deb11u6` from the snapshot vs. `+deb11u8` briefly visible on
the live mirror). This is a one-time step backward in currency, not an
ongoing drift — the snapshot is immutable, so it will not get any
staler than it already is — but it means this build image now trails
Debian 11's own last living state by whatever gap existed on
2025-07-21, permanently, until the durable fix below lands.

The durable fix is moving the Linux build image off Debian 11 entirely —
the underlying reason both jobs are pinned to it (documented in-line at
each `container:` line) will need to be re-verified against a current
Debian release's own `webkit2gtk`/`libsoup` package names and `glibc`
baseline before that move can happen safely. Until then, a better-scoped
interim step — a deb822 source stanza with `Check-Valid-Until: no`
scoped to just the affected entries, leaving the check active for
anything added later — has not yet been implemented either.

Relevant code:

- `linux-x64`'s `container:`/`apt-get` step in
  `.github/workflows/ci.yml`
- `linux-beta-x64`'s `container:`/`apt-get` step in
  `.github/workflows/beta.yml`

## Metadata

```text
generator-name: Claude Code
generator-version: Claude Sonnet 5
generator-model-token: claude-sonnet-5
generator-provider: Anthropic
generation-date: 2026-09-08
generator-responsibility: other
```
